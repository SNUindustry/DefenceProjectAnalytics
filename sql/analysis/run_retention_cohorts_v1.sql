-- Run-retention aggregate cohorts by overall, outcome, and stage.
WITH
-- @include run_retention_population_ctes_v1
,
cohorts AS (
  SELECT 'overall' AS cohortType, 'All' AS cohortValue, linkageEligible,
    structuralState, latencyRightCensored, thresholdState
  FROM classified_retention
  UNION ALL
  SELECT 'outcome', COALESCE(
    IF(gameplayOutcome IN ('Clear', 'Dead', 'Abandon'), gameplayOutcome, NULL),
    CONCAT('Unrecognized:', COALESCE(gameplayOutcome, 'null'))
  ), linkageEligible, structuralState, latencyRightCensored, thresholdState
  FROM classified_retention
  UNION ALL
  SELECT 'stage', COALESCE(NULLIF(TRIM(stageKey), ''), 'missing'), linkageEligible,
    structuralState, latencyRightCensored, thresholdState
  FROM classified_retention
)
SELECT
  cohortType, cohortValue,
  COUNT(*) AS anchorFinalAttempts,
  COUNTIF(linkageEligible) AS eligibleAnchors,
  COUNTIF(structuralState = 'NextRunObserved') AS nextRunObservedCount,
  COUNTIF(structuralState = 'NoNextRunObservedAsOf') AS noNextRunObservedAsOfCount,
  SAFE_DIVIDE(COUNTIF(structuralState = 'NextRunObserved'), COUNTIF(linkageEligible))
    AS nextRunObservedRate,
  COUNTIF(latencyRightCensored) AS latencyRightCensoredCount,
  SAFE_DIVIDE(COUNTIF(latencyRightCensored), COUNTIF(linkageEligible))
    AS latencyRightCensoredRate,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'ReturnedWithinThreshold')) AS returnedWithinThresholdCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'ReturnedAfterThreshold')) AS returnedAfterThresholdCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'LongTermNoNextRun')) AS noNextRunBeyondThresholdCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'RightCensored')) AS thresholdRightCensoredCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState IN ('ReturnedAfterThreshold', 'LongTermNoNextRun')))
    AS thresholdExceededCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState IN (
      'ReturnedWithinThreshold', 'ReturnedAfterThreshold', 'LongTermNoNextRun'
    ))) AS thresholdResolvedDenominator,
  IF(@long_term_threshold_days IS NULL, NULL,
    SAFE_DIVIDE(
      COUNTIF(thresholdState IN ('ReturnedAfterThreshold', 'LongTermNoNextRun')),
      COUNTIF(thresholdState IN (
        'ReturnedWithinThreshold', 'ReturnedAfterThreshold', 'LongTermNoNextRun'
      ))
    )) AS thresholdExceededRate
FROM cohorts
GROUP BY cohortType, cohortValue
