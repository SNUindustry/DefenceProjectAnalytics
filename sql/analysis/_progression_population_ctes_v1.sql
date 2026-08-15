progression_physical AS (
  SELECT
    environment, telemetryPlayerId, appVersion, contentVersion, releaseId,
    releaseChannel, releaseType, isDevelopmentBuild, releaseIdentityResolved,
    batchId, eventId, occurredAtUtc, linkedResultEventId, rowIndex,
    progressionKind, targetId, secondaryId, beforeState, afterState,
    beforeValue, afterValue, uploadedAtUtc,
    TO_JSON_STRING(STRUCT(
      telemetryPlayerId, appVersion, contentVersion, releaseId, releaseChannel,
      releaseType, isDevelopmentBuild, occurredAtUtc, linkedResultEventId,
      progressionKind, targetId, secondaryId, beforeState, afterState,
      beforeValue, afterValue
    )) AS payloadFingerprint
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_progression_events`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND occurredAtUtc < @analysis_as_of_utc
),
progression_duplicate_quality AS (
  SELECT
    environment, eventId,
    COUNT(*) AS physicalRowCount,
    COUNT(DISTINCT payloadFingerprint) AS payloadFingerprintCount
  FROM progression_physical
  GROUP BY environment, eventId
),
progression_deduped AS (
  SELECT
    p.environment, p.telemetryPlayerId, p.appVersion, p.contentVersion, p.releaseId,
    p.releaseChannel, p.releaseType, p.isDevelopmentBuild, p.releaseIdentityResolved,
    p.batchId, p.eventId, p.occurredAtUtc, p.linkedResultEventId, p.rowIndex,
    p.progressionKind, p.targetId, p.secondaryId, p.beforeState, p.afterState,
    p.beforeValue, p.afterValue, p.uploadedAtUtc,
    q.physicalRowCount,
    q.payloadFingerprintCount,
    progressionKind IN ('WeaponRecipe', 'EvolutionChoice', 'StatReset', 'EvolutionApplyOnly')
      AS progressionKindRecognized,
    CASE
      WHEN progressionKind IN ('WeaponRecipe', 'EvolutionChoice', 'StatReset', 'EvolutionApplyOnly')
        THEN progressionKind
      ELSE CONCAT('Unrecognized:', COALESCE(NULLIF(TRIM(progressionKind), ''), 'Missing'))
    END AS normalizedProgressionKind,
    contentVersion = @content_version
      AND (@progression_kind IS NULL OR progressionKind = @progression_kind)
      AND (@app_version IS NULL OR appVersion = @app_version)
      AND (@release_id IS NULL OR releaseId = @release_id)
      AND (@release_channel IS NULL OR releaseChannel = @release_channel)
      AND (@release_type IS NULL OR releaseType = @release_type)
      AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
      AND (@progression_occurred_start_utc IS NULL OR occurredAtUtc >= @progression_occurred_start_utc)
      AND (@progression_occurred_end_utc IS NULL OR occurredAtUtc < @progression_occurred_end_utc)
      AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
      AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
      AS isScoped
  FROM progression_physical AS p
  JOIN progression_duplicate_quality AS q USING (environment, eventId)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY p.environment, p.eventId
    ORDER BY p.uploadedAtUtc DESC, p.batchId DESC, p.rowIndex DESC
  ) = 1
),
transaction_deduped AS (
  SELECT
    environment, eventId, eventKind, resultCategory, sourceCategory,
    transactionKind, targetType, targetId, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_transaction_events`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND occurredAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, eventId
    ORDER BY uploadedAtUtc DESC, batchId DESC, rowIndex DESC
  ) = 1
),
progression_events AS (
  SELECT
    p.environment, p.telemetryPlayerId, p.appVersion, p.contentVersion, p.releaseId,
    p.releaseChannel, p.releaseType, p.isDevelopmentBuild, p.releaseIdentityResolved,
    p.batchId, p.eventId, p.occurredAtUtc, p.linkedResultEventId, p.rowIndex,
    p.progressionKind, p.targetId, p.secondaryId, p.beforeState, p.afterState,
    p.beforeValue, p.afterValue, p.uploadedAtUtc, p.physicalRowCount,
    p.payloadFingerprintCount, p.progressionKindRecognized,
    p.normalizedProgressionKind, p.isScoped,
    t.eventKind AS linkedEventKind,
    t.resultCategory AS linkedResultCategory,
    t.sourceCategory AS linkedSourceCategory,
    t.targetType AS linkedTransactionTargetType,
    NULLIF(TRIM(p.linkedResultEventId), '') IS NOT NULL AS isTransactionLinked,
    NULLIF(TRIM(p.linkedResultEventId), '') IS NULL AS isStandalone,
    CASE
      WHEN NOT p.progressionKindRecognized THEN NULL
      WHEN NULLIF(TRIM(p.linkedResultEventId), '') IS NOT NULL
        THEN t.eventKind = 'Result' AND t.resultCategory = 'Succeeded'
      WHEN p.progressionKind = 'EvolutionApplyOnly' THEN TRUE
      ELSE FALSE
    END AS linkageValid,
    NULLIF(TRIM(p.targetId), '') IS NULL AS missingTargetIdentity
  FROM progression_deduped AS p
  LEFT JOIN transaction_deduped AS t
    ON t.environment = p.environment
   AND t.eventId = p.linkedResultEventId
),
final_attempts AS (
  SELECT
    environment, telemetryPlayerId, attemptId, runId, stageKey,
    gameplayOutcome, attemptElapsedTimeSeconds, segmentEndedAtUtc,
    appVersion, contentVersion, releaseId, releaseChannel, releaseType,
    isDevelopmentBuild, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
),
gameplay_segments AS (
  SELECT
    environment, telemetryPlayerId, attemptId, runId, stageKey,
    segmentIndex, segmentStartedAtUtc, segmentEndedAtUtc,
    isAttemptFinal, gameplayOutcome, contentVersion, releaseId,
    releaseChannel, releaseType, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_gameplay_segments_v1`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId
    ORDER BY uploadedAtUtc DESC
  ) = 1
),
run_start_snapshots AS (
  SELECT environment, runId, isFromResume
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_start_snapshot`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId
    ORDER BY uploadedAtUtc DESC, rowIndex DESC
  ) = 1
),
new_attempt_starts AS (
  SELECT
    g.environment, g.telemetryPlayerId, g.attemptId, g.runId,
    g.stageKey, g.segmentStartedAtUtc, g.contentVersion, g.releaseId,
    g.releaseChannel, g.releaseType, s.isFromResume,
    f.gameplayOutcome AS finalOutcome,
    f.attemptElapsedTimeSeconds AS finalAttemptElapsedTimeSeconds,
    f.segmentEndedAtUtc AS finalSegmentEndedAtUtc
  FROM gameplay_segments AS g
  LEFT JOIN run_start_snapshots AS s USING (environment, runId)
  LEFT JOIN final_attempts AS f
    ON f.environment = g.environment
   AND f.telemetryPlayerId IS NOT DISTINCT FROM g.telemetryPlayerId
   AND f.attemptId = g.attemptId
  WHERE g.segmentIndex = 1
    AND NULLIF(TRIM(g.attemptId), '') IS NOT NULL
    AND g.segmentStartedAtUtc IS NOT NULL
    AND s.isFromResume IS NOT TRUE
),
lifecycle_terminals AS (
  SELECT environment, telemetryPlayerId, attemptId, runId, segmentEndedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_summary`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND segmentKind = 'LifecycleTerminal'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId
    ORDER BY uploadedAtUtc DESC
  ) = 1
),
previous_candidates AS (
  SELECT
    p.environment, p.eventId,
    f.attemptId AS previousAttemptId,
    f.runId AS previousRunId,
    f.stageKey AS previousStageKey,
    f.gameplayOutcome AS previousOutcome,
    f.attemptElapsedTimeSeconds AS previousAttemptElapsedTimeSeconds,
    f.segmentEndedAtUtc AS previousSegmentEndedAtUtc,
    f.contentVersion AS previousContentVersion,
    f.releaseId AS previousReleaseId,
    f.releaseChannel AS previousReleaseChannel,
    f.releaseType AS previousReleaseType
  FROM progression_events AS p
  LEFT JOIN final_attempts AS f
    ON f.environment = p.environment
   AND f.telemetryPlayerId IS NOT DISTINCT FROM p.telemetryPlayerId
   AND f.segmentEndedAtUtc < p.occurredAtUtc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY p.environment, p.eventId
    ORDER BY f.segmentEndedAtUtc DESC, f.attemptId DESC, f.runId DESC
  ) = 1
),
next_candidates AS (
  SELECT
    p.environment, p.eventId,
    n.attemptId AS nextAttemptId,
    n.runId AS nextRunId,
    n.stageKey AS nextStageKey,
    n.segmentStartedAtUtc AS nextSegmentStartedAtUtc,
    n.contentVersion AS nextContentVersion,
    n.releaseId AS nextReleaseId,
    n.releaseChannel AS nextReleaseChannel,
    n.releaseType AS nextReleaseType,
    n.finalOutcome AS nextOutcome,
    n.finalAttemptElapsedTimeSeconds AS nextAttemptElapsedTimeSeconds,
    n.finalSegmentEndedAtUtc AS nextFinalSegmentEndedAtUtc
  FROM progression_events AS p
  LEFT JOIN new_attempt_starts AS n
    ON n.environment = p.environment
   AND n.telemetryPlayerId IS NOT DISTINCT FROM p.telemetryPlayerId
   AND n.segmentStartedAtUtc > p.occurredAtUtc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY p.environment, p.eventId
    ORDER BY n.segmentStartedAtUtc, n.attemptId, n.runId
  ) = 1
),
event_boundaries_base AS (
  SELECT
    p.environment, p.telemetryPlayerId, p.appVersion, p.contentVersion, p.releaseId,
    p.releaseChannel, p.releaseType, p.isDevelopmentBuild, p.releaseIdentityResolved,
    p.batchId, p.eventId, p.occurredAtUtc, p.linkedResultEventId, p.rowIndex,
    p.progressionKind, p.targetId, p.secondaryId, p.beforeState, p.afterState,
    p.beforeValue, p.afterValue, p.uploadedAtUtc, p.physicalRowCount,
    p.payloadFingerprintCount, p.progressionKindRecognized,
    p.normalizedProgressionKind, p.isScoped, p.linkedEventKind,
    p.linkedResultCategory, p.linkedSourceCategory, p.linkedTransactionTargetType,
    p.isTransactionLinked, p.isStandalone, p.linkageValid, p.missingTargetIdentity,
    previous_candidates.previousAttemptId, previous_candidates.previousRunId,
    previous_candidates.previousStageKey, previous_candidates.previousOutcome,
    previous_candidates.previousAttemptElapsedTimeSeconds,
    previous_candidates.previousSegmentEndedAtUtc,
    previous_candidates.previousContentVersion, previous_candidates.previousReleaseId,
    previous_candidates.previousReleaseChannel, previous_candidates.previousReleaseType,
    next_candidates.nextAttemptId, next_candidates.nextRunId,
    next_candidates.nextStageKey, next_candidates.nextSegmentStartedAtUtc,
    next_candidates.nextContentVersion, next_candidates.nextReleaseId,
    next_candidates.nextReleaseChannel, next_candidates.nextReleaseType,
    next_candidates.nextOutcome, next_candidates.nextAttemptElapsedTimeSeconds,
    next_candidates.nextFinalSegmentEndedAtUtc,
    EXISTS (
      SELECT 1 FROM final_attempts AS same_time_previous
      WHERE same_time_previous.environment = p.environment
        AND same_time_previous.telemetryPlayerId IS NOT DISTINCT FROM p.telemetryPlayerId
        AND same_time_previous.segmentEndedAtUtc = p.occurredAtUtc
    ) OR EXISTS (
      SELECT 1 FROM new_attempt_starts AS same_time_next
      WHERE same_time_next.environment = p.environment
        AND same_time_next.telemetryPlayerId IS NOT DISTINCT FROM p.telemetryPlayerId
        AND same_time_next.segmentStartedAtUtc = p.occurredAtUtc
    ) AS ambiguousTimestampBoundary
  FROM progression_events AS p
  JOIN previous_candidates USING (environment, eventId)
  JOIN next_candidates USING (environment, eventId)
),
event_intervening AS (
  SELECT
    e.environment, e.eventId,
    COUNT(DISTINCT IF(g.segmentIndex > 1, g.runId, NULL)) AS resumeContinuationsSkipped,
    COUNT(DISTINCT l.runId) AS lifecycleTerminalsBeforeNextAttempt
  FROM event_boundaries_base AS e
  LEFT JOIN gameplay_segments AS g
    ON g.environment = e.environment
   AND g.telemetryPlayerId IS NOT DISTINCT FROM e.telemetryPlayerId
   AND g.segmentStartedAtUtc > e.occurredAtUtc
   AND (e.nextSegmentStartedAtUtc IS NULL OR g.segmentStartedAtUtc < e.nextSegmentStartedAtUtc)
  LEFT JOIN lifecycle_terminals AS l
    ON l.environment = e.environment
   AND l.telemetryPlayerId IS NOT DISTINCT FROM e.telemetryPlayerId
   AND l.segmentEndedAtUtc > e.occurredAtUtc
   AND (e.nextSegmentStartedAtUtc IS NULL OR l.segmentEndedAtUtc < e.nextSegmentStartedAtUtc)
  GROUP BY e.environment, e.eventId
),
event_boundaries AS (
  SELECT
    e.environment, e.telemetryPlayerId, e.appVersion, e.contentVersion, e.releaseId,
    e.releaseChannel, e.releaseType, e.isDevelopmentBuild, e.releaseIdentityResolved,
    e.batchId, e.eventId, e.occurredAtUtc, e.linkedResultEventId, e.rowIndex,
    e.progressionKind, e.targetId, e.secondaryId, e.beforeState, e.afterState,
    e.beforeValue, e.afterValue, e.uploadedAtUtc, e.physicalRowCount,
    e.payloadFingerprintCount, e.progressionKindRecognized,
    e.normalizedProgressionKind, e.isScoped, e.linkedEventKind,
    e.linkedResultCategory, e.linkedSourceCategory, e.linkedTransactionTargetType,
    e.isTransactionLinked, e.isStandalone, e.linkageValid, e.missingTargetIdentity,
    e.previousAttemptId, e.previousRunId, e.previousStageKey, e.previousOutcome,
    e.previousAttemptElapsedTimeSeconds, e.previousSegmentEndedAtUtc,
    e.previousContentVersion, e.previousReleaseId, e.previousReleaseChannel,
    e.previousReleaseType, e.nextAttemptId, e.nextRunId, e.nextStageKey,
    e.nextSegmentStartedAtUtc, e.nextContentVersion, e.nextReleaseId,
    e.nextReleaseChannel, e.nextReleaseType, e.nextOutcome,
    e.nextAttemptElapsedTimeSeconds, e.nextFinalSegmentEndedAtUtc,
    e.ambiguousTimestampBoundary,
    i.resumeContinuationsSkipped,
    i.lifecycleTerminalsBeforeNextAttempt,
    i.resumeContinuationsSkipped > 0 OR i.lifecycleTerminalsBeforeNextAttempt > 0
      AS overlapsOpenAttempt,
    CASE
      WHEN NULLIF(TRIM(e.telemetryPlayerId), '') IS NULL THEN 'unbounded'
      WHEN e.previousAttemptId IS NOT NULL AND e.nextAttemptId IS NOT NULL THEN 'both'
      WHEN e.previousAttemptId IS NOT NULL THEN 'previousOnly'
      WHEN e.nextAttemptId IS NOT NULL THEN 'nextOnly'
      ELSE 'unbounded'
    END AS boundaryType,
    CASE
      WHEN NULLIF(TRIM(e.telemetryPlayerId), '') IS NULL THEN NULL
      WHEN e.previousAttemptId IS NULL AND e.nextAttemptId IS NULL THEN NULL
      ELSE TO_HEX(SHA256(CONCAT(
        e.environment, '|', e.telemetryPlayerId, '|',
        COALESCE(e.previousAttemptId, 'NO_PREVIOUS'), '|',
        COALESCE(e.nextAttemptId, 'NO_NEXT_AS_OF')
      )))
    END AS episodeKey
  FROM event_boundaries_base AS e
  JOIN event_intervening AS i USING (environment, eventId)
),
selected_episode_keys AS (
  SELECT DISTINCT environment, episodeKey
  FROM event_boundaries
  WHERE isScoped AND episodeKey IS NOT NULL
),
selected_episode_events AS (
  SELECT
    e.environment, e.telemetryPlayerId, e.appVersion, e.contentVersion, e.releaseId,
    e.releaseChannel, e.releaseType, e.isDevelopmentBuild, e.releaseIdentityResolved,
    e.batchId, e.eventId, e.occurredAtUtc, e.linkedResultEventId, e.rowIndex,
    e.progressionKind, e.targetId, e.secondaryId, e.beforeState, e.afterState,
    e.beforeValue, e.afterValue, e.uploadedAtUtc, e.physicalRowCount,
    e.payloadFingerprintCount, e.progressionKindRecognized,
    e.normalizedProgressionKind, e.isScoped, e.linkedEventKind,
    e.linkedResultCategory, e.linkedSourceCategory, e.linkedTransactionTargetType,
    e.isTransactionLinked, e.isStandalone, e.linkageValid, e.missingTargetIdentity,
    e.previousAttemptId, e.previousRunId, e.previousStageKey, e.previousOutcome,
    e.previousAttemptElapsedTimeSeconds, e.previousSegmentEndedAtUtc,
    e.previousContentVersion, e.previousReleaseId, e.previousReleaseChannel,
    e.previousReleaseType, e.nextAttemptId, e.nextRunId, e.nextStageKey,
    e.nextSegmentStartedAtUtc, e.nextContentVersion, e.nextReleaseId,
    e.nextReleaseChannel, e.nextReleaseType, e.nextOutcome,
    e.nextAttemptElapsedTimeSeconds, e.nextFinalSegmentEndedAtUtc,
    e.ambiguousTimestampBoundary, e.resumeContinuationsSkipped,
    e.lifecycleTerminalsBeforeNextAttempt, e.overlapsOpenAttempt,
    e.boundaryType, e.episodeKey
  FROM event_boundaries AS e
  JOIN selected_episode_keys USING (environment, episodeKey)
),
episode_aggregates AS (
  SELECT
    environment, episodeKey,
    ANY_VALUE(boundaryType) AS boundaryType,
    MIN(occurredAtUtc) AS firstProgressionAtUtc,
    MAX(occurredAtUtc) AS lastProgressionAtUtc,
    COUNT(*) AS progressionCount,
    COUNTIF(isScoped) AS scopedProgressionCount,
    ANY_VALUE(IF(isScoped, releaseId, NULL)) AS scopedProgressionReleaseId,
    ANY_VALUE(IF(isScoped, releaseChannel, NULL)) AS scopedProgressionReleaseChannel,
    ANY_VALUE(IF(isScoped, releaseType, NULL)) AS scopedProgressionReleaseType,
    COUNT(DISTINCT IF(isScoped, TO_JSON_STRING(STRUCT(
      releaseId, releaseChannel, releaseType
    )), NULL)) > 1 AS mixedScopedProgressionRelease,
    COUNT(DISTINCT normalizedProgressionKind) AS progressionKindCount,
    ARRAY_TO_STRING(
      ARRAY_AGG(DISTINCT normalizedProgressionKind ORDER BY normalizedProgressionKind),
      ' + '
    ) AS progressionKindSet,
    COUNT(*) = 1 AS isSingleProgression,
    COUNT(*) > 1 AS isMultiProgression,
    COUNT(*) > COUNT(DISTINCT CONCAT(
      normalizedProgressionKind, '|', COALESCE(NULLIF(TRIM(targetId), ''), 'MISSING')
    )) AS sameTargetRepeatedChanges,
    ANY_VALUE(previousAttemptId) AS previousAttemptId,
    ANY_VALUE(previousRunId) AS previousRunId,
    ANY_VALUE(previousStageKey) AS previousStageKey,
    ANY_VALUE(previousOutcome) AS previousOutcome,
    ANY_VALUE(previousAttemptElapsedTimeSeconds) AS previousAttemptElapsedTimeSeconds,
    ANY_VALUE(previousSegmentEndedAtUtc) AS previousSegmentEndedAtUtc,
    ANY_VALUE(previousContentVersion) AS previousContentVersion,
    ANY_VALUE(previousReleaseId) AS previousReleaseId,
    ANY_VALUE(previousReleaseChannel) AS previousReleaseChannel,
    ANY_VALUE(previousReleaseType) AS previousReleaseType,
    ANY_VALUE(nextAttemptId) AS nextAttemptId,
    ANY_VALUE(nextRunId) AS nextRunId,
    ANY_VALUE(nextStageKey) AS nextStageKey,
    ANY_VALUE(nextSegmentStartedAtUtc) AS nextSegmentStartedAtUtc,
    ANY_VALUE(nextContentVersion) AS nextContentVersion,
    ANY_VALUE(nextReleaseId) AS nextReleaseId,
    ANY_VALUE(nextReleaseChannel) AS nextReleaseChannel,
    ANY_VALUE(nextReleaseType) AS nextReleaseType,
    ANY_VALUE(nextOutcome) AS nextOutcome,
    ANY_VALUE(nextAttemptElapsedTimeSeconds) AS nextAttemptElapsedTimeSeconds,
    ANY_VALUE(nextFinalSegmentEndedAtUtc) AS nextFinalSegmentEndedAtUtc,
    SUM(resumeContinuationsSkipped) AS resumeContinuationsSkipped,
    SUM(lifecycleTerminalsBeforeNextAttempt) AS lifecycleTerminalsBeforeNextAttempt,
    LOGICAL_OR(overlapsOpenAttempt) AS overlapsOpenAttempt,
    LOGICAL_OR(ambiguousTimestampBoundary) AS ambiguousTimestampBoundary,
    COUNTIF(contentVersion != @content_version) AS crossContentCoOccurringProgressionEvents
  FROM selected_episode_events
  GROUP BY environment, episodeKey
),
episodes AS (
  SELECT
    e.environment, e.episodeKey, e.boundaryType, e.firstProgressionAtUtc,
    e.lastProgressionAtUtc, e.progressionCount, e.scopedProgressionCount,
    e.scopedProgressionReleaseId, e.scopedProgressionReleaseChannel,
    e.scopedProgressionReleaseType, e.mixedScopedProgressionRelease,
    e.progressionKindCount, e.progressionKindSet, e.isSingleProgression,
    e.isMultiProgression, e.sameTargetRepeatedChanges,
    e.previousAttemptId, e.previousRunId, e.previousStageKey, e.previousOutcome,
    e.previousAttemptElapsedTimeSeconds, e.previousSegmentEndedAtUtc,
    e.previousContentVersion, e.previousReleaseId, e.previousReleaseChannel,
    e.previousReleaseType, e.nextAttemptId, e.nextRunId, e.nextStageKey,
    e.nextSegmentStartedAtUtc, e.nextContentVersion, e.nextReleaseId,
    e.nextReleaseChannel, e.nextReleaseType, e.nextOutcome,
    e.nextAttemptElapsedTimeSeconds, e.nextFinalSegmentEndedAtUtc,
    e.resumeContinuationsSkipped, e.lifecycleTerminalsBeforeNextAttempt,
    e.overlapsOpenAttempt, e.ambiguousTimestampBoundary,
    e.crossContentCoOccurringProgressionEvents,
    TIMESTAMP_DIFF(lastProgressionAtUtc, firstProgressionAtUtc, SECOND) AS episodeDurationSeconds,
    TIMESTAMP_DIFF(firstProgressionAtUtc, previousSegmentEndedAtUtc, SECOND) AS previousToProgressionSeconds,
    TIMESTAMP_DIFF(nextSegmentStartedAtUtc, lastProgressionAtUtc, SECOND) AS timeToNextRunSeconds,
    previousSegmentEndedAtUtc IS NOT NULL
      AND TIMESTAMP_DIFF(firstProgressionAtUtc, previousSegmentEndedAtUtc, SECOND)
        BETWEEN 0 AND @previous_run_max_gap_minutes * 60 AS previousContextEligible,
    nextSegmentStartedAtUtc IS NOT NULL
      AND TIMESTAMP_DIFF(nextSegmentStartedAtUtc, lastProgressionAtUtc, SECOND)
        BETWEEN 0 AND @next_run_max_gap_minutes * 60 AS nextRunWithinWindow,
    nextSegmentStartedAtUtc IS NOT NULL
      AND TIMESTAMP_DIFF(nextSegmentStartedAtUtc, lastProgressionAtUtc, SECOND)
        > @next_run_max_gap_minutes * 60 AS laterNextRunOutsideWindow,
    nextSegmentStartedAtUtc IS NULL
      AND lastProgressionAtUtc > TIMESTAMP_SUB(
        @analysis_as_of_utc, INTERVAL @next_run_max_gap_minutes MINUTE
      ) AS rightCensored,
    nextSegmentStartedAtUtc IS NOT NULL AND nextOutcome IS NULL AS nextRunOutcomePending,
    previousContentVersion IS NOT NULL
      AND previousContentVersion != @content_version AS crossContentPrevious,
    nextContentVersion IS NOT NULL
      AND nextContentVersion != @content_version AS crossContentNext,
    mixedScopedProgressionRelease
      OR (previousAttemptId IS NOT NULL AND (
        previousReleaseId IS DISTINCT FROM scopedProgressionReleaseId
        OR previousReleaseChannel IS DISTINCT FROM scopedProgressionReleaseChannel
        OR previousReleaseType IS DISTINCT FROM scopedProgressionReleaseType
      ))
      OR (nextAttemptId IS NOT NULL AND (
        nextReleaseId IS DISTINCT FROM scopedProgressionReleaseId
        OR nextReleaseChannel IS DISTINCT FROM scopedProgressionReleaseChannel
        OR nextReleaseType IS DISTINCT FROM scopedProgressionReleaseType
      ))
      AS crossReleasePair,
    (@previous_stage_key IS NULL OR previousStageKey = @previous_stage_key)
      AND (@next_stage_key IS NULL OR nextStageKey = @next_stage_key)
      AS associationStageFilterMatched
  FROM episode_aggregates AS e
),
classified_episodes AS (
  SELECT
    e.environment, e.episodeKey, e.boundaryType, e.firstProgressionAtUtc,
    e.lastProgressionAtUtc, e.progressionCount, e.scopedProgressionCount,
    e.scopedProgressionReleaseId, e.scopedProgressionReleaseChannel,
    e.scopedProgressionReleaseType, e.mixedScopedProgressionRelease,
    e.progressionKindCount, e.progressionKindSet, e.isSingleProgression,
    e.isMultiProgression, e.sameTargetRepeatedChanges,
    e.previousAttemptId, e.previousRunId, e.previousStageKey, e.previousOutcome,
    e.previousAttemptElapsedTimeSeconds, e.previousSegmentEndedAtUtc,
    e.previousContentVersion, e.previousReleaseId, e.previousReleaseChannel,
    e.previousReleaseType, e.nextAttemptId, e.nextRunId, e.nextStageKey,
    e.nextSegmentStartedAtUtc, e.nextContentVersion, e.nextReleaseId,
    e.nextReleaseChannel, e.nextReleaseType, e.nextOutcome,
    e.nextAttemptElapsedTimeSeconds, e.nextFinalSegmentEndedAtUtc,
    e.resumeContinuationsSkipped, e.lifecycleTerminalsBeforeNextAttempt,
    e.overlapsOpenAttempt, e.ambiguousTimestampBoundary,
    e.crossContentCoOccurringProgressionEvents, e.episodeDurationSeconds,
    e.previousToProgressionSeconds, e.timeToNextRunSeconds,
    e.previousContextEligible, e.nextRunWithinWindow,
    e.laterNextRunOutsideWindow, e.rightCensored, e.nextRunOutcomePending,
    e.crossContentPrevious, e.crossContentNext, e.crossReleasePair,
    e.associationStageFilterMatched,
    nextRunWithinWindow OR (NOT nextRunWithinWindow AND NOT rightCensored) AS matureEpisode,
    NOT nextRunWithinWindow AND NOT rightCensored AS noNextRunWithinWindow,
    previousAttemptId IS NOT NULL AND nextAttemptId IS NOT NULL
      AND previousContextEligible AND nextRunWithinWindow
      AND nextOutcome IS NOT NULL
      AND previousStageKey = nextStageKey
      AND previousContentVersion = @content_version
      AND nextContentVersion = @content_version
      AND crossContentCoOccurringProgressionEvents = 0
      AND NOT crossReleasePair
      AND NOT overlapsOpenAttempt
      AND associationStageFilterMatched
      AS primaryPairEligible,
    previousAttemptId IS NOT NULL AND nextAttemptId IS NOT NULL
      AND previousContextEligible AND nextRunWithinWindow
      AND nextOutcome IS NOT NULL
      AND previousContentVersion = @content_version
      AND nextContentVersion = @content_version
      AND NOT overlapsOpenAttempt
      AND associationStageFilterMatched
      AS allLinkedSameContentPairEligible
  FROM episodes AS e
)
