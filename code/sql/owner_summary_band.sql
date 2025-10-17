-- Only governed PRs where CODEOWNERS existed before the first review
SELECT
  CASE
    WHEN owners_count = 1 THEN '1 Owner'
    WHEN owners_count = 2 THEN '2 Owners'
    WHEN owners_count BETWEEN 3 AND 6 THEN '3–6 Owners'
    WHEN owners_count BETWEEN 7 AND 10 THEN '7–10 Owners'
    ELSE '>10 Owners'
  END AS owners_band,
  COUNT(*) AS pr_count,
  COUNT(DISTINCT repo_name) AS repo_count,
  APPROX_QUANTILES(hours_to_first_review, 2)[OFFSET(1)] AS median_first_review_h,
  APPROX_QUANTILES(hours_to_merge, 2)[OFFSET(1)] AS median_merge_h,
  -- optional: predictability
  APPROX_QUANTILES(hours_to_merge, 4)[OFFSET(3)] - APPROX_QUANTILES(hours_to_merge, 4)[OFFSET(1)] AS iqr_merge_h
FROM `YOUR_PROJECT.msr2026.msr_final_sample`
WHERE has_codeowners = TRUE
  AND hours_to_first_review IS NOT NULL
  AND hours_to_merge IS NOT NULL
  AND codeowners_created_at IS NOT NULL
  AND codeowners_created_at <= first_review_any_ts      -- 👈 key check: existed before review
GROUP BY owners_band
ORDER BY MIN(owners_count);