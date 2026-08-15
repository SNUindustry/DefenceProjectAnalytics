-- Episode-level immediate next-run engagement; structural boundaries are already resolved.
WITH
-- @include progression_population_ctes_v1
, episode_kinds AS (
  SELECT DISTINCT e.environment, e.episodeKey, e.normalizedProgressionKind AS progressionKind
  FROM selected_episode_events AS e
  WHERE e.isScoped
), dimensions AS (
  SELECT
    'overall' AS dimension, 'All' AS progressionKind,
    c.environment, c.episodeKey, c.boundaryType, c.matureEpisode,
    c.rightCensored, c.nextRunWithinWindow, c.noNextRunWithinWindow,
    c.laterNextRunOutsideWindow, c.previousAttemptId, c.nextAttemptId,
    c.previousStageKey, c.nextStageKey, c.associationStageFilterMatched,
    c.timeToNextRunSeconds, c.previousContextEligible,
    c.previousToProgressionSeconds
  FROM classified_episodes AS c
  UNION ALL
  SELECT
    'progressionKind', k.progressionKind,
    c.environment, c.episodeKey, c.boundaryType, c.matureEpisode,
    c.rightCensored, c.nextRunWithinWindow, c.noNextRunWithinWindow,
    c.laterNextRunOutsideWindow, c.previousAttemptId, c.nextAttemptId,
    c.previousStageKey, c.nextStageKey, c.associationStageFilterMatched,
    c.timeToNextRunSeconds, c.previousContextEligible,
    c.previousToProgressionSeconds
  FROM classified_episodes AS c
  JOIN episode_kinds AS k USING (environment, episodeKey)
)
SELECT
  dimension,
  progressionKind,
  COUNT(*) AS boundedEpisodeCount,
  COUNTIF(boundaryType = 'both') AS bothBoundaryEpisodeCount,
  COUNTIF(boundaryType = 'previousOnly') AS previousOnlyEpisodeCount,
  COUNTIF(boundaryType = 'nextOnly') AS nextOnlyEpisodeCount,
  COUNTIF(matureEpisode) AS matureEpisodeCount,
  COUNTIF(rightCensored) AS rightCensoredEpisodeCount,
  COUNTIF(nextRunWithinWindow) AS nextRunCount,
  COUNTIF(noNextRunWithinWindow) AS noNextRunWithinWindowCount,
  COUNTIF(laterNextRunOutsideWindow) AS laterNextRunOutsideWindowCount,
  COUNTIF(matureEpisode) AS nextRunRateDenominator,
  SAFE_DIVIDE(COUNTIF(nextRunWithinWindow), COUNTIF(matureEpisode)) AS nextRunRate,
  COUNTIF(previousAttemptId IS NOT NULL AND nextAttemptId IS NOT NULL
    AND previousStageKey = nextStageKey AND associationStageFilterMatched) AS sameStageCount,
  COUNTIF(previousAttemptId IS NOT NULL AND nextAttemptId IS NOT NULL
    AND associationStageFilterMatched) AS sameStageDenominator,
  SAFE_DIVIDE(
    COUNTIF(previousAttemptId IS NOT NULL AND nextAttemptId IS NOT NULL
      AND previousStageKey = nextStageKey AND associationStageFilterMatched),
    COUNTIF(previousAttemptId IS NOT NULL AND nextAttemptId IS NOT NULL
      AND associationStageFilterMatched)
  ) AS sameStageRate,
  APPROX_QUANTILES(IF(nextRunWithinWindow, timeToNextRunSeconds, NULL), 100)[OFFSET(25)]
    AS timeToNextRunP25,
  APPROX_QUANTILES(IF(nextRunWithinWindow, timeToNextRunSeconds, NULL), 100)[OFFSET(50)]
    AS timeToNextRunMedian,
  APPROX_QUANTILES(IF(nextRunWithinWindow, timeToNextRunSeconds, NULL), 100)[OFFSET(75)]
    AS timeToNextRunP75,
  APPROX_QUANTILES(IF(nextRunWithinWindow, timeToNextRunSeconds, NULL), 100)[OFFSET(90)]
    AS timeToNextRunP90,
  APPROX_QUANTILES(IF(previousContextEligible, previousToProgressionSeconds, NULL), 100)[OFFSET(25)]
    AS previousToProgressionP25,
  APPROX_QUANTILES(IF(previousContextEligible, previousToProgressionSeconds, NULL), 100)[OFFSET(50)]
    AS previousToProgressionMedian,
  APPROX_QUANTILES(IF(previousContextEligible, previousToProgressionSeconds, NULL), 100)[OFFSET(75)]
    AS previousToProgressionP75,
  APPROX_QUANTILES(IF(previousContextEligible, previousToProgressionSeconds, NULL), 100)[OFFSET(90)]
    AS previousToProgressionP90
FROM dimensions
GROUP BY dimension, progressionKind
ORDER BY dimension, progressionKind;
