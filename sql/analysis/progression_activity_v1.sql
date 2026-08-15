-- Scoped committed progression activity by stable kind/target dimensions.
WITH
-- @include progression_population_ctes_v1
SELECT
  e.normalizedProgressionKind AS progressionKind,
  NULLIF(TRIM(e.targetId), '') AS targetId,
  NULLIF(TRIM(e.secondaryId), '') AS secondaryId,
  IF(e.missingTargetIdentity, 'missing', 'identified') AS identityStatus,
  COUNT(*) AS eventCount,
  COUNT(DISTINCT e.episodeKey) AS boundedEpisodeCount,
  COUNTIF(e.episodeKey IS NULL) AS unboundedEventCount,
  COUNT(DISTINCT IF(c.isSingleProgression, e.episodeKey, NULL)) AS singleProgressionEpisodeCount,
  COUNT(DISTINCT IF(c.isMultiProgression, e.episodeKey, NULL)) AS multiProgressionEpisodeCount,
  COUNTIF(e.isTransactionLinked) AS transactionLinkedCount,
  COUNTIF(e.isStandalone) AS standaloneCount,
  COUNTIF(e.progressionKindRecognized AND e.linkageValid IS NOT TRUE) AS invalidLinkageCount,
  COUNTIF(NOT e.progressionKindRecognized) AS unassessedLinkageCount,
  COUNT(DISTINCT IF(c.matureEpisode, e.episodeKey, NULL)) AS matureEpisodeCount,
  COUNT(DISTINCT IF(c.nextRunWithinWindow, e.episodeKey, NULL)) AS nextRunCount,
  SAFE_DIVIDE(
    COUNT(DISTINCT IF(c.nextRunWithinWindow, e.episodeKey, NULL)),
    COUNT(DISTINCT IF(c.matureEpisode, e.episodeKey, NULL))
  ) AS nextRunRate,
  COUNT(DISTINCT IF(c.primaryPairEligible, e.episodeKey, NULL)) AS sameStagePairCount
FROM event_boundaries AS e
LEFT JOIN classified_episodes AS c USING (environment, episodeKey)
WHERE e.isScoped
GROUP BY progressionKind, targetId, secondaryId, identityStatus
ORDER BY eventCount DESC, progressionKind, targetId, secondaryId;
