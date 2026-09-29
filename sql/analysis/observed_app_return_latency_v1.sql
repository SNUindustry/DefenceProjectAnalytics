WITH
-- @include observed_app_return_ctes_v1
SELECT
  COUNTIF(outcome = 'ObservedAppReturn') AS observedCount,
  (SELECT PERCENTILE_CONT(delaySeconds, 0.50) OVER()
     FROM classified WHERE outcome = 'ObservedAppReturn' LIMIT 1) AS p50Seconds,
  (SELECT PERCENTILE_CONT(delaySeconds, 0.75) OVER()
     FROM classified WHERE outcome = 'ObservedAppReturn' LIMIT 1) AS p75Seconds,
  (SELECT PERCENTILE_CONT(delaySeconds, 0.90) OVER()
     FROM classified WHERE outcome = 'ObservedAppReturn' LIMIT 1) AS p90Seconds
FROM classified
