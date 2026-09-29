anchor_physical AS (
  SELECT
    environment, telemetryPlayerId, attemptId, runId, stageKey,
    gameplayOutcome, abandonReason, segmentKind, segmentIndex,
    segmentStartedAtUtc, segmentEndedAtUtc, attemptElapsedTimeSeconds,
    appVersion, contentVersion, releaseId, releaseChannel, releaseType,
    isDevelopmentBuild, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_summary`
  WHERE environment = @environment
    AND isAttemptFinal IS TRUE
    AND attemptId IS NOT NULL
    AND uploadedAtUtc < @analysis_as_of_utc
),
anchor_canonical AS (
  SELECT
    environment, telemetryPlayerId, attemptId, runId, stageKey,
    gameplayOutcome, abandonReason, segmentKind, segmentIndex,
    segmentStartedAtUtc, segmentEndedAtUtc, attemptElapsedTimeSeconds,
    appVersion, contentVersion, releaseId, releaseChannel, releaseType,
    isDevelopmentBuild, uploadedAtUtc
  FROM anchor_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, telemetryPlayerId, attemptId
    ORDER BY segmentIndex DESC, segmentEndedAtUtc DESC, uploadedAtUtc DESC, runId DESC
  ) = 1
),
anchor_identity_conflicts AS (
  SELECT environment, attemptId
  FROM anchor_physical
  WHERE NULLIF(TRIM(attemptId), '') IS NOT NULL
  GROUP BY environment, attemptId
  HAVING COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) > 1
),
anchors AS (
  SELECT
    TO_JSON_STRING(STRUCT(a.environment, a.telemetryPlayerId, a.attemptId)) AS anchorKey,
    a.environment, a.telemetryPlayerId, a.attemptId, a.runId, a.stageKey,
    a.gameplayOutcome, a.abandonReason, a.segmentKind, a.segmentIndex,
    a.segmentStartedAtUtc, a.segmentEndedAtUtc, a.attemptElapsedTimeSeconds,
    a.appVersion, a.contentVersion, a.releaseId, a.releaseChannel, a.releaseType,
    a.isDevelopmentBuild, a.uploadedAtUtc,
    c.attemptId IS NOT NULL AS conflictingIdentity
  FROM anchor_canonical AS a
  LEFT JOIN anchor_identity_conflicts AS c
    ON c.environment = a.environment AND c.attemptId = a.attemptId
  WHERE a.contentVersion = @content_version
    AND (@stage_key IS NULL OR a.stageKey = @stage_key)
    AND (@final_outcome IS NULL OR a.gameplayOutcome = @final_outcome)
    AND (@app_version IS NULL OR a.appVersion = @app_version)
    AND (@release_id IS NULL OR a.releaseId = @release_id)
    AND (@release_channel IS NULL OR a.releaseChannel = @release_channel)
    AND (@release_type IS NULL OR a.releaseType = @release_type)
    AND (@is_development_build IS NULL OR a.isDevelopmentBuild = @is_development_build)
    AND (@run_ended_start_utc IS NULL OR a.segmentEndedAtUtc >= @run_ended_start_utc)
    AND (@run_ended_end_utc IS NULL OR a.segmentEndedAtUtc < @run_ended_end_utc)
    AND (@uploaded_start_utc IS NULL OR a.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR a.uploadedAtUtc < @uploaded_end_utc)
),
-- @include next_new_attempt_ctes_v1
,
next_candidate_ranked AS (
  SELECT
    a.anchorKey, n.attemptId AS nextAttemptId, n.runId AS nextRunId,
    n.stageKey AS nextStageKey, n.segmentStartedAtUtc AS nextStartedAtUtc,
    n.contentVersion AS nextContentVersion, n.releaseId AS nextReleaseId,
    COUNT(*) OVER (
      PARTITION BY a.anchorKey, n.segmentStartedAtUtc
    ) AS sameStartCandidateCount,
    ROW_NUMBER() OVER (
      PARTITION BY a.anchorKey
      ORDER BY n.segmentStartedAtUtc, n.attemptId, n.runId
    ) AS candidateRank
  FROM anchors AS a
  JOIN new_attempt_segments AS n
    ON n.environment = a.environment
   AND n.telemetryPlayerId IS NOT DISTINCT FROM a.telemetryPlayerId
   AND n.attemptId != a.attemptId
   AND n.segmentStartedAtUtc > a.segmentEndedAtUtc
  WHERE NULLIF(TRIM(a.telemetryPlayerId), '') IS NOT NULL
    AND NULLIF(TRIM(a.attemptId), '') IS NOT NULL
    AND a.segmentEndedAtUtc IS NOT NULL
    AND a.segmentEndedAtUtc < @analysis_as_of_utc
    AND NOT a.conflictingIdentity
),
next_candidates AS (
  SELECT
    anchorKey, nextAttemptId, nextRunId, nextStageKey, nextStartedAtUtc,
    nextContentVersion, nextReleaseId, sameStartCandidateCount
  FROM next_candidate_ranked
  WHERE candidateRank = 1
),
next_outcomes AS (
  SELECT environment, telemetryPlayerId, attemptId, gameplayOutcome
  FROM anchor_physical
  WHERE segmentEndedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, telemetryPlayerId, attemptId
    ORDER BY segmentIndex DESC, segmentEndedAtUtc DESC, uploadedAtUtc DESC, runId DESC
  ) = 1
),
retention_anchors AS (
  SELECT
    a.anchorKey, a.environment, a.telemetryPlayerId, a.attemptId, a.runId,
    a.stageKey, a.gameplayOutcome, a.segmentEndedAtUtc, a.contentVersion,
    a.releaseId, a.releaseChannel, a.releaseType, a.uploadedAtUtc,
    a.conflictingIdentity,
    n.nextAttemptId, n.nextRunId, n.nextStageKey, n.nextStartedAtUtc,
    n.nextContentVersion, n.nextReleaseId, o.gameplayOutcome AS nextOutcome,
    n.sameStartCandidateCount,
    NULLIF(TRIM(a.telemetryPlayerId), '') IS NOT NULL
      AND NULLIF(TRIM(a.attemptId), '') IS NOT NULL
      AND a.segmentEndedAtUtc IS NOT NULL
      AND a.segmentEndedAtUtc < @analysis_as_of_utc
      AND NOT a.conflictingIdentity AS linkageEligible,
    TIMESTAMP_DIFF(n.nextStartedAtUtc, a.segmentEndedAtUtc, SECOND) AS nextRunDelaySeconds,
    TIMESTAMP_DIFF(@analysis_as_of_utc, a.segmentEndedAtUtc, SECOND) AS observationAgeSeconds,
    CASE
      WHEN @source_upload_grace_hours IS NULL THEN NULL
      ELSE TIMESTAMP_DIFF(
        TIMESTAMP_SUB(@analysis_as_of_utc, INTERVAL @source_upload_grace_hours HOUR),
        a.segmentEndedAtUtc,
        SECOND
      )
    END AS maturityAgeSeconds
  FROM anchors AS a
  LEFT JOIN next_candidates AS n USING (anchorKey)
  LEFT JOIN next_outcomes AS o
    ON o.environment = a.environment
   AND o.telemetryPlayerId IS NOT DISTINCT FROM a.telemetryPlayerId
   AND o.attemptId = n.nextAttemptId
),
classified_retention AS (
  SELECT
    r.anchorKey, r.environment, r.telemetryPlayerId, r.attemptId, r.runId,
    r.stageKey, r.gameplayOutcome, r.segmentEndedAtUtc, r.contentVersion,
    r.releaseId, r.releaseChannel, r.releaseType, r.uploadedAtUtc,
    r.conflictingIdentity,
    r.nextAttemptId, r.nextRunId, r.nextStageKey, r.nextStartedAtUtc,
    r.nextContentVersion, r.nextReleaseId, r.nextOutcome,
    r.sameStartCandidateCount, r.linkageEligible, r.nextRunDelaySeconds,
    r.observationAgeSeconds, r.maturityAgeSeconds,
    CASE
      WHEN NOT linkageEligible THEN 'Ineligible'
      WHEN nextStartedAtUtc IS NOT NULL THEN 'NextRunObserved'
      ELSE 'NoNextRunObservedAsOf'
    END AS structuralState,
    CASE
      WHEN @long_term_threshold_days IS NULL THEN NULL
      WHEN NOT linkageEligible THEN 'Ineligible'
      WHEN nextRunDelaySeconds <= @long_term_threshold_days * 86400
        THEN 'ReturnedWithinThreshold'
      WHEN nextRunDelaySeconds > @long_term_threshold_days * 86400
        THEN 'ReturnedAfterThreshold'
      WHEN maturityAgeSeconds >= @long_term_threshold_days * 86400
        THEN 'LongTermNoNextRun'
      ELSE 'RightCensored'
    END AS thresholdState,
    linkageEligible AND nextStartedAtUtc IS NULL AS latencyRightCensored,
    linkageEligible AND nextStartedAtUtc IS NOT NULL AND nextStageKey = stageKey
      AS sameStageNextRun,
    linkageEligible AND nextStartedAtUtc IS NOT NULL
      AND nextContentVersion = contentVersion AS sameContentNextRun,
    linkageEligible AND nextStartedAtUtc IS NOT NULL AND nextOutcome IS NULL
      AS nextOutcomePending
  FROM retention_anchors AS r
)
