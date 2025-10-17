```sql
CREATE OR REPLACE TABLE `YOUR_PROJECT.msr2026.sample_repos_top2000` AS
SELECT repo_name
FROM `YOUR_PROJECT.msr2026.active_repos_pr`
ORDER BY pr_events DESC
LIMIT 2000;
```