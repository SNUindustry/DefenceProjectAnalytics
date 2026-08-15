-- Next-new-attempt engagement and outcome aggregates.
WITH
-- @include post_run_population_ctes_v1
, aggregated AS (
SELECT
  gameplayOutcome AS anchorOutcome,
  COUNTIF(matureWindow) AS matureWindows,
  COUNTIF(rightCensored) AS rightCensoredWindows,
  COUNTIF(matureWindow AND resolvedByNextRunWithinWindow) AS nextRunWithinWindow,
  COUNTIF(matureNoNextRunWithinWindow) AS noNextRunWithinWindow,
  COUNTIF(laterNextRunOutsideWindow) AS laterNextRunOutsideWindow,
  COUNTIF(matureWindow) AS nextRunRateDenominator,
  SAFE_DIVIDE(COUNTIF(matureWindow AND resolvedByNextRunWithinWindow), COUNTIF(matureWindow))
    AS nextRunRate,
  COUNTIF(matureWindow AND sameStageRetry) AS sameStageRetryCount,
  COUNTIF(matureWindow AND resolvedByNextRunWithinWindow) AS sameStageRetryDenominator,
  SAFE_DIVIDE(COUNTIF(matureWindow AND sameStageRetry),
    COUNTIF(matureWindow AND resolvedByNextRunWithinWindow)) AS sameStageRetryRate,
  COUNTIF(matureWindow AND sameContentNextRun) AS sameContentNextRunCount,
  COUNTIF(matureWindow AND resolvedByNextRunWithinWindow) AS sameContentNextRunDenominator,
  SAFE_DIVIDE(COUNTIF(matureWindow AND sameContentNextRun),
    COUNTIF(matureWindow AND resolvedByNextRunWithinWindow)) AS sameContentNextRunRate,
  COUNTIF(matureWindow AND resolvedByNextRunWithinWindow AND nextOutcome = 'Clear') AS nextClears,
  COUNTIF(matureWindow AND resolvedByNextRunWithinWindow AND nextOutcome = 'Dead') AS nextDeaths,
  COUNTIF(matureWindow AND resolvedByNextRunWithinWindow AND nextOutcome = 'Abandon') AS nextAbandons,
  COUNTIF(matureWindow AND nextOutcomePending) AS nextOutcomePending
FROM classified_windows
GROUP BY gameplayOutcome
), percentiles AS (
SELECT DISTINCT gameplayOutcome AS anchorOutcome,
  PERCENTILE_CONT(IF(matureWindow AND resolvedByNextRunWithinWindow, timeToNextRunSeconds, NULL), 0.25)
    OVER (PARTITION BY gameplayOutcome) AS timeToNextRunP25,
  PERCENTILE_CONT(IF(matureWindow AND resolvedByNextRunWithinWindow, timeToNextRunSeconds, NULL), 0.50)
    OVER (PARTITION BY gameplayOutcome) AS timeToNextRunMedian,
  PERCENTILE_CONT(IF(matureWindow AND resolvedByNextRunWithinWindow, timeToNextRunSeconds, NULL), 0.75)
    OVER (PARTITION BY gameplayOutcome) AS timeToNextRunP75,
  PERCENTILE_CONT(IF(matureWindow AND resolvedByNextRunWithinWindow, timeToNextRunSeconds, NULL), 0.90)
    OVER (PARTITION BY gameplayOutcome) AS timeToNextRunP90
FROM classified_windows
)
SELECT a.anchorOutcome, a.matureWindows, a.rightCensoredWindows,
  a.nextRunWithinWindow, a.noNextRunWithinWindow, a.laterNextRunOutsideWindow,
  a.nextRunRateDenominator, a.nextRunRate, a.sameStageRetryCount,
  a.sameStageRetryDenominator, a.sameStageRetryRate, a.sameContentNextRunCount,
  a.sameContentNextRunDenominator, a.sameContentNextRunRate, a.nextClears,
  a.nextDeaths, a.nextAbandons, a.nextOutcomePending, p.timeToNextRunP25,
  p.timeToNextRunMedian, p.timeToNextRunP75, p.timeToNextRunP90
FROM aggregated AS a JOIN percentiles AS p USING (anchorOutcome)
ORDER BY anchorOutcome
