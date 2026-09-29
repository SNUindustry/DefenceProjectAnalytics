WITH
-- @include observed_app_return_ctes_v1
,
cohort_rows AS (
  SELECT 'overall' AS cohortType, 'All' AS cohortValue, * FROM classified
  UNION ALL
  SELECT 'outcome', COALESCE(gameplayOutcome, 'missing'), * FROM classified
  UNION ALL
  SELECT 'stage', COALESCE(NULLIF(TRIM(stageKey), ''), 'missing'), * FROM classified
)
SELECT cohortType, cohortValue,
  COUNT(*) AS anchorFinalAttempts,
  COUNTIF(ineligibleReason IS NULL) AS eligibleAnchors,
  COUNTIF(ineligibleReason IS NOT NULL) AS ineligibleAnchors,
  COUNTIF(outcome = 'ObservedAppReturn') AS observedReturnCount,
  SAFE_DIVIDE(COUNTIF(outcome = 'ObservedAppReturn'),
    COUNTIF(ineligibleReason IS NULL)) AS observedReturnRate,
  COUNTIF(returnKind = 'ColdStart') AS coldStartReturnCount,
  COUNTIF(returnKind = 'ForegroundResume') AS foregroundResumeReturnCount,
  COUNTIF(rightCensored) AS rightCensoredCount,
  SAFE_DIVIDE(COUNTIF(rightCensored), COUNTIF(ineligibleReason IS NULL))
    AS rightCensoredRate,
  IF(@threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'ReturnedWithinThreshold')) AS returnedWithinThresholdCount,
  IF(@threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'ReturnedAfterThreshold')) AS returnedAfterThresholdCount,
  IF(@threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'NoObservedReturnBeyondThreshold'))
    AS noObservedReturnBeyondThresholdCount,
  IF(@threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'RightCensored')) AS thresholdRightCensoredCount,
  IF(@threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'Ineligible')) AS thresholdIneligibleCount
FROM cohort_rows
GROUP BY cohortType, cohortValue
