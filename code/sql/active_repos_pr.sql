```sql
-- Create active_repos_pr
DECLARE months ARRAY<STRING> DEFAULT [
  '202401','202402','202403','202404','202405','202406','202407','202408','202409',
  '202410','202411','202412','202501','202502','202503','202504','202505','202506',
  '202507','202508','202509'
];

CREATE OR REPLACE TABLE `YOUR_PROJECT.msr2026.active_repos_pr` AS
SELECT
  LOWER(repo.name) AS repo_name,
  COUNTIF(JSON_EXTRACT_SCALAR(payload,'$.action')='opened') AS pr_events
FROM `githubarchive.month.*`
WHERE _TABLE_SUFFIX IN UNNEST(months)
  AND type='PullRequestEvent'
GROUP BY repo_name
HAVING pr_events > 0
ORDER BY pr_events DESC;
```