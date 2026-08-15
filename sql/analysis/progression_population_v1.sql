-- Progression Next-Run sample and data-quality summary.
WITH
-- @include progression_population_ctes_v1
, scoped_events AS (
  SELECT
    telemetryPlayerId, occurredAtUtc, normalizedProgressionKind, targetId,
    progressionKindRecognized, progressionKind, missingTargetIdentity,
    physicalRowCount, payloadFingerprintCount, isTransactionLinked,
    isStandalone, linkageValid, episodeKey, ambiguousTimestampBoundary
  FROM event_boundaries
  WHERE isScoped
), sample AS (
  SELECT
    COUNT(*) AS progressionEvents,
    COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) AS uniquePlayers,
    COUNT(DISTINCT normalizedProgressionKind) AS progressionKinds,
    COUNT(DISTINCT IF(NOT missingTargetIdentity,
      CONCAT(normalizedProgressionKind, '|', targetId), NULL)) AS uniqueTargets,
    COALESCE(SUM(physicalRowCount), 0) AS physicalProgressionRows,
    COUNTIF(payloadFingerprintCount > 1) AS conflictingProgressionEvents,
    COUNTIF(NULLIF(TRIM(telemetryPlayerId), '') IS NULL) AS missingPlayerIdentityEvents,
    COUNTIF(occurredAtUtc IS NULL) AS missingOccurredAtEvents,
    COUNTIF(missingTargetIdentity) AS missingTargetIdentityEvents,
    COUNTIF(NOT progressionKindRecognized) AS unrecognizedProgressionKindEvents,
    COUNTIF(isTransactionLinked) AS transactionLinkedEvents,
    COUNTIF(isStandalone) AS standaloneEvents,
    COUNTIF(isTransactionLinked AND progressionKindRecognized
      AND (linkageValid IS NOT TRUE OR progressionKind = 'EvolutionApplyOnly'))
      AS invalidTransactionLinkEvents,
    COUNTIF(isStandalone AND progressionKindRecognized AND progressionKind != 'EvolutionApplyOnly')
      AS unexpectedStandaloneEvents,
    COUNTIF(NOT progressionKindRecognized)
      AS unassessedUnrecognizedLinkageEvents,
    COUNTIF(episodeKey IS NULL) AS unboundedProgressionEvents,
    COUNT(DISTINCT IF(episodeKey IS NULL, NULLIF(TRIM(telemetryPlayerId), ''), NULL))
      AS unboundedProgressionPlayers,
    COUNTIF(ambiguousTimestampBoundary) AS ambiguousTimestampEvents
  FROM scoped_events
), episode_sample AS (
  SELECT
    COUNT(*) AS boundedEpisodes,
    COUNTIF(isSingleProgression) AS singleProgressionEpisodes,
    COUNTIF(isMultiProgression) AS multiProgressionEpisodes,
    COUNTIF(matureEpisode) AS matureEpisodes,
    COUNTIF(rightCensored) AS rightCensoredEpisodes,
    COUNTIF(previousAttemptId IS NOT NULL) AS episodesWithPreviousRun,
    COUNTIF(nextAttemptId IS NOT NULL) AS episodesWithNextRun,
    COUNTIF(primaryPairEligible) AS sameStagePairedEpisodes,
    COUNTIF(boundaryType = 'both') AS bothBoundaryEpisodes,
    COUNTIF(boundaryType = 'previousOnly') AS previousOnlyEpisodes,
    COUNTIF(boundaryType = 'nextOnly') AS nextOnlyEpisodes,
    COUNTIF(previousAttemptId IS NOT NULL AND NOT previousContextEligible)
      AS previousRunBeyondWindowEpisodes,
    COUNTIF(previousContextEligible) AS previousContextEligibleEpisodes,
    COUNTIF(previousAttemptId IS NULL) AS previousRunMissingEpisodes,
    COUNTIF(nextRunWithinWindow) AS nextRunWithinWindowEpisodes,
    COUNTIF(noNextRunWithinWindow) AS noNextRunWithinWindowEpisodes,
    COUNTIF(laterNextRunOutsideWindow) AS laterNextRunOutsideWindowEpisodes,
    COUNTIF(nextRunOutcomePending) AS nextRunOutcomePendingEpisodes,
    COALESCE(SUM(resumeContinuationsSkipped), 0) AS resumeContinuationsSkipped,
    COUNTIF(overlapsOpenAttempt) AS openAttemptEpisodesExcludedFromPairing,
    COALESCE(SUM(lifecycleTerminalsBeforeNextAttempt), 0)
      AS lifecycleTerminalsBetweenProgressionAndNextAttempt,
    COUNTIF(
      previousAttemptId IS NOT NULL AND nextAttemptId IS NOT NULL
      AND previousContextEligible AND nextRunWithinWindow AND nextOutcome IS NOT NULL
      AND (previousContentVersion != @content_version OR nextContentVersion != @content_version
        OR crossContentCoOccurringProgressionEvents > 0)
    ) AS crossContentPerformancePairsExcluded,
    COUNTIF(crossReleasePair) AS crossReleasePairs,
    COUNTIF(crossContentPrevious) AS crossContentPreviousBoundaries,
    COUNTIF(crossContentNext) AS crossContentNextBoundaries,
    COUNTIF(
      previousToProgressionSeconds < 0 OR timeToNextRunSeconds < 0
      OR previousAttemptElapsedTimeSeconds < 0 OR nextAttemptElapsedTimeSeconds < 0
    ) AS invalidTimingRows
  FROM classified_episodes
)
SELECT
  sample.progressionEvents, sample.uniquePlayers, sample.progressionKinds,
  sample.uniqueTargets, sample.physicalProgressionRows,
  sample.conflictingProgressionEvents, sample.missingPlayerIdentityEvents,
  sample.missingOccurredAtEvents, sample.missingTargetIdentityEvents,
  sample.unrecognizedProgressionKindEvents, sample.transactionLinkedEvents,
  sample.standaloneEvents, sample.invalidTransactionLinkEvents,
  sample.unexpectedStandaloneEvents, sample.unboundedProgressionEvents,
  sample.unassessedUnrecognizedLinkageEvents,
  sample.unboundedProgressionPlayers, sample.ambiguousTimestampEvents,
  episode_sample.boundedEpisodes, episode_sample.singleProgressionEpisodes,
  episode_sample.multiProgressionEpisodes, episode_sample.matureEpisodes,
  episode_sample.rightCensoredEpisodes, episode_sample.episodesWithPreviousRun,
  episode_sample.episodesWithNextRun, episode_sample.sameStagePairedEpisodes,
  episode_sample.bothBoundaryEpisodes, episode_sample.previousOnlyEpisodes,
  episode_sample.nextOnlyEpisodes, episode_sample.previousRunBeyondWindowEpisodes,
  episode_sample.previousContextEligibleEpisodes,
  episode_sample.previousRunMissingEpisodes,
  episode_sample.nextRunWithinWindowEpisodes,
  episode_sample.noNextRunWithinWindowEpisodes,
  episode_sample.laterNextRunOutsideWindowEpisodes,
  episode_sample.nextRunOutcomePendingEpisodes,
  episode_sample.resumeContinuationsSkipped,
  episode_sample.openAttemptEpisodesExcludedFromPairing,
  episode_sample.lifecycleTerminalsBetweenProgressionAndNextAttempt,
  episode_sample.crossContentPerformancePairsExcluded,
  episode_sample.crossContentPreviousBoundaries,
  episode_sample.crossContentNextBoundaries,
  episode_sample.crossReleasePairs, episode_sample.invalidTimingRows
FROM sample CROSS JOIN episode_sample;
