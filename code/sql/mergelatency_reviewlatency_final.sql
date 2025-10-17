**Run (SQL):**

```sql
DECLARE months ARRAY<STRING> DEFAULT [
  '202401','202402','202403','202404','202405','202406','202407','202408','202409','202410',
  '202411','202412','202501','202502','202503','202504','202505','202506','202507','202508','202509'
];

BEGIN
  -- Helper set for fast filtering
  CREATE OR REPLACE TEMP TABLE tmp_sample AS
  SELECT LOWER(repo_name) AS repo_name
  FROM `YOUR_PROJECT.msr2026.sample_repos_top2000`;

  -- Targets to append into
  CREATE OR REPLACE TABLE `YOUR_PROJECT.msr2026.pr_merge_latency_sample`
    (repo_name STRING, pr_number STRING, pr_open_ts TIMESTAMP, pr_merge_ts TIMESTAMP, hours_to_merge INT64);

  CREATE OR REPLACE TABLE `YOUR_PROJECT.msr2026.pr_review_latency_sample`
    (repo_name STRING, pr_number STRING, first_review_any_ts TIMESTAMP);

  -- Loop over months (partition pruning keeps cost sane)
  FOR m IN (SELECT month_val FROM UNNEST(months) AS month_val) DO

    EXECUTE IMMEDIATE FORMAT("""
      INSERT INTO `YOUR_PROJECT.msr2026.pr_merge_latency_sample`
      SELECT LOWER(repo.name),
             JSON_EXTRACT_SCALAR(payload,'$.pull_request.number'),
             PARSE_TIMESTAMP('%%Y-%%m-%%dT%%H:%%M:%%E*S%%Ez', JSON_EXTRACT_SCALAR(payload,'$.pull_request.created_at')),
             PARSE_TIMESTAMP('%%Y-%%m-%%dT%%H:%%M:%%E*S%%Ez', JSON_EXTRACT_SCALAR(payload,'$.pull_request.merged_at')),
             TIMESTAMP_DIFF(
               PARSE_TIMESTAMP('%%Y-%%m-%%dT%%H:%%M:%%E*S%%Ez', JSON_EXTRACT_SCALAR(payload,'$.pull_request.merged_at')),
               PARSE_TIMESTAMP('%%Y-%%m-%%dT%%H:%%M:%%E*S%%Ez', JSON_EXTRACT_SCALAR(payload,'$.pull_request.created_at')),
               HOUR)
      FROM `githubarchive.month.%s`
      WHERE type='PullRequestEvent'
        AND LOWER(repo.name) IN (SELECT repo_name FROM tmp_sample)
        AND JSON_EXTRACT_SCALAR(payload,'$.pull_request.created_at') IS NOT NULL
        AND JSON_EXTRACT_SCALAR(payload,'$.pull_request.merged_at')  IS NOT NULL;
    """, m.month_val);

    EXECUTE IMMEDIATE FORMAT("""
      INSERT INTO `YOUR_PROJECT.msr2026.pr_review_latency_sample`
      WITH reviews AS (
        SELECT LOWER(repo.name) AS repo_name,
               JSON_EXTRACT_SCALAR(payload,'$.pull_request.number') AS pr_number,
               MIN(created_at) AS first_review_ts
        FROM `githubarchive.month.%s`
        WHERE type='PullRequestReviewEvent'
          AND LOWER(repo.name) IN (SELECT repo_name FROM tmp_sample)
        GROUP BY repo_name, pr_number
      ),
      comments AS (
        SELECT LOWER(repo.name) AS repo_name,
               JSON_EXTRACT_SCALAR(payload,'$.pull_request.number') AS pr_number,
               MIN(created_at) AS first_review_comment_ts
        FROM `githubarchive.month.%s`
        WHERE type='PullRequestReviewCommentEvent'
          AND LOWER(repo.name) IN (SELECT repo_name FROM tmp_sample)
        GROUP BY repo_name, pr_number
      )
      SELECT COALESCE(r.repo_name,c.repo_name), COALESCE(r.pr_number,c.pr_number),
             LEAST(r.first_review_ts,c.first_review_comment_ts)
      FROM reviews r FULL OUTER JOIN comments c
           ON r.repo_name=c.repo_name AND r.pr_number=c.pr_number;
    """, m.month_val, m.month_val);

  END FOR;

  -- Final joined table
  CREATE OR REPLACE TABLE `YOUR_PROJECT.msr2026.msr_final_sample` AS
  SELECT
    m.repo_name,
    m.pr_number,
    m.pr_open_ts,
    m.pr_merge_ts,
    m.hours_to_merge,
    r.first_review_any_ts,
    TIMESTAMP_DIFF(r.first_review_any_ts, m.pr_open_ts, HOUR) AS hours_to_first_review,
    co.has_codeowners,
    co.codeowners_created_at,
    co.owners_count,
    (co.has_codeowners AND co.codeowners_created_at <= m.pr_open_ts)  AS codeowners_before_open,
    (co.has_codeowners AND co.codeowners_created_at <= m.pr_merge_ts) AS codeowners_before_merge
  FROM `YOUR_PROJECT.msr2026.pr_merge_latency_sample` m
  LEFT JOIN `YOUR_PROJECT.msr2026.pr_review_latency_sample` r
    USING (repo_name, pr_number)
  LEFT JOIN `YOUR_PROJECT.msr2026.codeowners_meta` co
    ON LOWER(m.repo_name) = LOWER(co.repo_name);
END;
```