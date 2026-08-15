-- Structurally bounded episode composition.
WITH
-- @include progression_population_ctes_v1
SELECT
  progressionKindSet,
  IF(isSingleProgression, 'single', 'multi') AS episodeType,
  boundaryType,
  COUNT(*) AS episodeCount,
  SUM(progressionCount) AS eventCount,
  COUNTIF(sameTargetRepeatedChanges) AS sameTargetRepeatedEpisodeCount,
  COUNTIF(matureEpisode) AS matureEpisodeCount,
  COUNTIF(nextRunWithinWindow) AS nextRunCount,
  COUNTIF(matureEpisode) AS nextRunRateDenominator,
  SAFE_DIVIDE(COUNTIF(nextRunWithinWindow), COUNTIF(matureEpisode)) AS nextRunRate,
  COUNTIF(primaryPairEligible) AS sameStagePairCount,
  APPROX_QUANTILES(episodeDurationSeconds, 100)[OFFSET(25)] AS episodeDurationP25,
  APPROX_QUANTILES(episodeDurationSeconds, 100)[OFFSET(50)] AS episodeDurationMedian,
  APPROX_QUANTILES(episodeDurationSeconds, 100)[OFFSET(75)] AS episodeDurationP75
FROM classified_episodes
GROUP BY progressionKindSet, episodeType, boundaryType
ORDER BY episodeCount DESC, progressionKindSet, boundaryType;
