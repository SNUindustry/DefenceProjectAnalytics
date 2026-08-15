-- Aggregate outcome transitions and paired elapsed summaries.
WITH
-- @include progression_population_ctes_v1
, pair_rows AS (
  SELECT
    'primarySameStageSameContent' AS pairScope,
    previousOutcome, nextOutcome, previousAttemptElapsedTimeSeconds,
    nextAttemptElapsedTimeSeconds
  FROM classified_episodes
  WHERE primaryPairEligible
  UNION ALL
  SELECT
    'primarySameStageSameContentSingleProgression' AS pairScope,
    previousOutcome, nextOutcome, previousAttemptElapsedTimeSeconds,
    nextAttemptElapsedTimeSeconds
  FROM classified_episodes
  WHERE primaryPairEligible AND isSingleProgression
  UNION ALL
  SELECT
    'secondaryStageChangedSameContent' AS pairScope,
    previousOutcome, nextOutcome, previousAttemptElapsedTimeSeconds,
    nextAttemptElapsedTimeSeconds
  FROM classified_episodes
  WHERE allLinkedSameContentPairEligible AND previousStageKey != nextStageKey
  UNION ALL
  SELECT
    'secondaryCrossReleaseSameContent' AS pairScope,
    previousOutcome, nextOutcome, previousAttemptElapsedTimeSeconds,
    nextAttemptElapsedTimeSeconds
  FROM classified_episodes
  WHERE allLinkedSameContentPairEligible AND crossReleasePair
  UNION ALL
  SELECT
    'secondaryAllLinkedSameContent' AS pairScope,
    previousOutcome, nextOutcome, previousAttemptElapsedTimeSeconds,
    nextAttemptElapsedTimeSeconds
  FROM classified_episodes
  WHERE allLinkedSameContentPairEligible
), transition_rows AS (
  SELECT
    'outcomeTransition' AS rowType, pairScope,
    previousOutcome, nextOutcome,
    COUNT(*) AS transitionCount,
    SUM(COUNT(*)) OVER (PARTITION BY pairScope) AS transitionDenominator,
    SAFE_DIVIDE(COUNT(*), SUM(COUNT(*)) OVER (PARTITION BY pairScope)) AS transitionRatio,
    CAST(NULL AS INT64) AS pairedCount,
    CAST(NULL AS FLOAT64) AS previousElapsedP25,
    CAST(NULL AS FLOAT64) AS previousElapsedMedian,
    CAST(NULL AS FLOAT64) AS previousElapsedP75,
    CAST(NULL AS FLOAT64) AS nextElapsedP25,
    CAST(NULL AS FLOAT64) AS nextElapsedMedian,
    CAST(NULL AS FLOAT64) AS nextElapsedP75,
    CAST(NULL AS FLOAT64) AS deltaMean,
    CAST(NULL AS FLOAT64) AS deltaP25,
    CAST(NULL AS FLOAT64) AS deltaMedian,
    CAST(NULL AS FLOAT64) AS deltaP75,
    COUNTIF(previousOutcome = 'Clear') AS previousClears,
    COUNTIF(previousOutcome = 'Dead') AS previousDeaths,
    COUNTIF(nextOutcome = 'Clear') AS nextClears,
    COUNTIF(nextOutcome = 'Dead') AS nextDeaths
  FROM pair_rows
  GROUP BY pairScope, previousOutcome, nextOutcome
), elapsed_rows AS (
  SELECT
    'elapsedSummary' AS rowType, pairScope,
    CAST(NULL AS STRING) AS previousOutcome,
    CAST(NULL AS STRING) AS nextOutcome,
    CAST(NULL AS INT64) AS transitionCount,
    CAST(NULL AS INT64) AS transitionDenominator,
    CAST(NULL AS FLOAT64) AS transitionRatio,
    COUNTIF(previousAttemptElapsedTimeSeconds >= 0 AND nextAttemptElapsedTimeSeconds >= 0)
      AS pairedCount,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0, previousAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(25)]
      AS previousElapsedP25,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0, previousAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(50)]
      AS previousElapsedMedian,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0, previousAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(75)]
      AS previousElapsedP75,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0, nextAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(25)]
      AS nextElapsedP25,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0, nextAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(50)]
      AS nextElapsedMedian,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0, nextAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(75)]
      AS nextElapsedP75,
    AVG(IF(previousAttemptElapsedTimeSeconds >= 0 AND nextAttemptElapsedTimeSeconds >= 0,
      nextAttemptElapsedTimeSeconds - previousAttemptElapsedTimeSeconds, NULL)) AS deltaMean,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0,
      nextAttemptElapsedTimeSeconds - previousAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(25)] AS deltaP25,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0,
      nextAttemptElapsedTimeSeconds - previousAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(50)] AS deltaMedian,
    APPROX_QUANTILES(IF(previousAttemptElapsedTimeSeconds >= 0
      AND nextAttemptElapsedTimeSeconds >= 0,
      nextAttemptElapsedTimeSeconds - previousAttemptElapsedTimeSeconds, NULL), 100)[OFFSET(75)] AS deltaP75,
    COUNTIF(previousOutcome = 'Clear') AS previousClears,
    COUNTIF(previousOutcome = 'Dead') AS previousDeaths,
    COUNTIF(nextOutcome = 'Clear') AS nextClears,
    COUNTIF(nextOutcome = 'Dead') AS nextDeaths
  FROM pair_rows
  GROUP BY pairScope
)
SELECT
  rowType, pairScope, previousOutcome, nextOutcome, transitionCount,
  transitionDenominator, transitionRatio, pairedCount, previousElapsedP25,
  previousElapsedMedian, previousElapsedP75, nextElapsedP25,
  nextElapsedMedian, nextElapsedP75, deltaMean, deltaP25, deltaMedian,
  deltaP75, previousClears, previousDeaths, nextClears, nextDeaths
FROM transition_rows
UNION ALL
SELECT
  rowType, pairScope, previousOutcome, nextOutcome, transitionCount,
  transitionDenominator, transitionRatio, pairedCount, previousElapsedP25,
  previousElapsedMedian, previousElapsedP75, nextElapsedP25,
  nextElapsedMedian, nextElapsedP75, deltaMean, deltaP25, deltaMedian,
  deltaP75, previousClears, previousDeaths, nextClears, nextDeaths
FROM elapsed_rows
ORDER BY rowType, pairScope, previousOutcome, nextOutcome;
