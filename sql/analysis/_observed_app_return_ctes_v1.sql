anchor_physical AS (
  SELECT environment, telemetryPlayerId, attemptId, runId, stageKey,
    gameplayOutcome, segmentKind, segmentIndex, segmentEndedAtUtc,
    appVersion, contentVersion, releaseId, releaseChannel, releaseType,
    isDevelopmentBuild, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_summary`
  WHERE environment = @environment
    AND isAttemptFinal IS TRUE
    AND attemptId IS NOT NULL
    AND uploadedAtUtc < @analysis_as_of_utc
),
anchor_canonical AS (
  SELECT * FROM anchor_physical
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
  SELECT TO_JSON_STRING(STRUCT(a.environment, a.telemetryPlayerId, a.attemptId)) AS anchorKey,
    a.*, c.attemptId IS NOT NULL AS conflictingIdentity
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
    AND (@run_ended_start_utc IS NULL OR a.segmentEndedAtUtc >= @run_ended_start_utc)
    AND (@run_ended_end_utc IS NULL OR a.segmentEndedAtUtc < @run_ended_end_utc)
    AND (@uploaded_start_utc IS NULL OR a.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR a.uploadedAtUtc < @uploaded_end_utc)
),
lifecycle_physical AS (
  SELECT l.*,
    TO_JSON_STRING((SELECT AS STRUCT l.* EXCEPT(uploadedAtUtc))) AS payloadJson
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_app_lifecycle_events` AS l
  WHERE l.environment = @environment AND l.uploadedAtUtc < @analysis_as_of_utc
),
occurrence_groups AS (
  SELECT environment, lifecycleOccurrenceId,
    COUNT(*) AS physicalCount, COUNT(DISTINCT payloadJson) AS variantCount
  FROM lifecycle_physical
  GROUP BY environment, lifecycleOccurrenceId
),
lifecycle_one AS (
  SELECT * EXCEPT(payloadJson) FROM lifecycle_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, lifecycleOccurrenceId
    ORDER BY uploadedAtUtc ASC, payloadJson ASC
  ) = 1
),
lifecycle_normalized AS (
  SELECT l.*, g.physicalCount, g.variantCount,
    REGEXP_CONTAINS(COALESCE(l.telemetryPlayerId, ''), r'^[a-f0-9]{32}$') AS validPlayer,
    REGEXP_CONTAINS(COALESCE(l.appProcessSessionId, ''), r'^[a-f0-9]{32}$')
      AND REGEXP_CONTAINS(COALESCE(l.lifecycleOccurrenceId, ''), r'^[a-f0-9]{32}$') AS validIds,
    l.occurredAtUtc IS NOT NULL AND l.occurredAtUtc < @analysis_as_of_utc AS validTime,
    @environment != 'Production' OR l.isDevelopmentBuild IS FALSE AS nonQa
  FROM lifecycle_one AS l
  JOIN occurrence_groups AS g USING (environment, lifecycleOccurrenceId)
),
lifecycle_shaped AS (
  SELECT l.*,
    CASE
      WHEN l.returnKind = 'ColdStart' THEN
        l.timingQuality = 'NotApplicable'
        AND l.previousBackgroundAtUtc IS NULL
        AND l.backgroundDurationSeconds IS NULL
      WHEN l.returnKind = 'ForegroundResume' THEN
        l.timingQuality = 'Valid'
        AND l.previousBackgroundAtUtc IS NOT NULL
        AND l.backgroundDurationSeconds IS NOT NULL
        AND NOT IS_NAN(l.backgroundDurationSeconds)
        AND NOT IS_INF(l.backgroundDurationSeconds)
        AND l.backgroundDurationSeconds >= 0
        AND l.previousBackgroundAtUtc <= l.occurredAtUtc
      ELSE FALSE
    END AS validShape,
    l.returnKind = 'ColdStart' AND EXISTS (
      SELECT 1 FROM lifecycle_normalized AS earlier
      WHERE earlier.environment = l.environment
        AND earlier.telemetryPlayerId = l.telemetryPlayerId
        AND earlier.appProcessSessionId = l.appProcessSessionId
        AND earlier.lifecycleOccurrenceId != l.lifecycleOccurrenceId
        AND earlier.occurredAtUtc < l.occurredAtUtc
        AND earlier.variantCount = 1
    ) AS coldStartProcessReuse
  FROM lifecycle_normalized AS l
),
eligible_lifecycle AS (
  SELECT * FROM lifecycle_shaped
  WHERE variantCount = 1 AND validPlayer AND validIds AND validTime AND nonQa
    AND profileAttributionStatus = 'CanonicalProfile'
    AND validShape AND NOT coldStartProcessReuse
),
run_start_one AS (
  SELECT environment, runId, appProcessSessionId, latestForegroundOccurrenceId
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_start_snapshot`
  WHERE environment = @environment AND uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId
    ORDER BY uploadedAtUtc DESC, COALESCE(rowIndex, -1) DESC
  ) = 1
),
anchor_assessed AS (
  SELECT a.*,
    EXISTS (
      SELECT 1 FROM eligible_lifecycle AS l
      WHERE l.environment = a.environment
        AND l.telemetryPlayerId = a.telemetryPlayerId
        AND l.occurredAtUtc <= a.segmentEndedAtUtc
    ) AS hasLifecycleBaseline,
    s.runId IS NULL OR s.appProcessSessionId IS NULL
      OR s.latestForegroundOccurrenceId IS NULL AS runLifecycleLinkageMissing,
    s.runId IS NOT NULL AND s.appProcessSessionId IS NOT NULL
      AND s.latestForegroundOccurrenceId IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM eligible_lifecycle AS linked
        WHERE linked.environment = a.environment
          AND linked.lifecycleOccurrenceId = s.latestForegroundOccurrenceId
          AND linked.appProcessSessionId = s.appProcessSessionId
          AND linked.telemetryPlayerId = a.telemetryPlayerId
          AND linked.occurredAtUtc <= a.segmentEndedAtUtc
      ) AS runLifecycleLinkageMismatch
  FROM anchors AS a
  LEFT JOIN run_start_one AS s
    ON s.environment = a.environment AND s.runId = a.runId
),
eligible_anchors AS (
  SELECT *,
    CASE
      WHEN @environment = 'Production' AND isDevelopmentBuild IS NOT FALSE
        THEN 'ProductionDevelopmentExcluded'
      WHEN NOT REGEXP_CONTAINS(COALESCE(telemetryPlayerId, ''), r'^[a-f0-9]{32}$')
        THEN 'InvalidPlayerId'
      WHEN NULLIF(TRIM(attemptId), '') IS NULL THEN 'MissingAttemptId'
      WHEN segmentEndedAtUtc IS NULL OR segmentEndedAtUtc >= @analysis_as_of_utc
        THEN 'InvalidAnchorEnd'
      WHEN conflictingIdentity THEN 'ConflictingAttemptPlayer'
      WHEN NOT hasLifecycleBaseline THEN 'MissingLifecycleBaseline'
      ELSE NULL
    END AS ineligibleReason
  FROM anchor_assessed
),
return_candidates AS (
  SELECT a.anchorKey, l.lifecycleOccurrenceId, l.returnKind, l.occurredAtUtc,
    COUNT(*) OVER (PARTITION BY a.anchorKey, l.occurredAtUtc) AS sameTimeCount,
    ROW_NUMBER() OVER (
      PARTITION BY a.anchorKey
      ORDER BY l.occurredAtUtc,
        CASE l.returnKind WHEN 'ColdStart' THEN 0 ELSE 1 END,
        l.lifecycleOccurrenceId
    ) AS candidateRank
  FROM eligible_anchors AS a
  JOIN eligible_lifecycle AS l
    ON l.environment = a.environment AND l.telemetryPlayerId = a.telemetryPlayerId
    AND l.occurredAtUtc > a.segmentEndedAtUtc
    AND (l.returnKind = 'ColdStart'
      OR l.previousBackgroundAtUtc > a.segmentEndedAtUtc)
  WHERE a.ineligibleReason IS NULL
),
next_returns AS (
  SELECT * FROM return_candidates WHERE candidateRank = 1
),
classified AS (
  SELECT a.anchorKey, a.stageKey, a.gameplayOutcome, a.telemetryPlayerId,
    a.contentVersion, a.releaseId,
    a.ineligibleReason, a.runLifecycleLinkageMissing, a.runLifecycleLinkageMismatch,
    a.isDevelopmentBuild, a.segmentEndedAtUtc, n.returnKind, n.occurredAtUtc,
    n.sameTimeCount,
    IF(a.ineligibleReason IS NULL AND n.occurredAtUtc IS NULL, TRUE, FALSE)
      AS rightCensored,
    IF(n.occurredAtUtc IS NULL, NULL,
      TIMESTAMP_DIFF(n.occurredAtUtc, a.segmentEndedAtUtc, MICROSECOND) / 1000000.0)
      AS delaySeconds,
    CASE
      WHEN a.ineligibleReason IS NOT NULL THEN 'Ineligible'
      WHEN n.occurredAtUtc IS NOT NULL THEN 'ObservedAppReturn'
      ELSE 'NoObservedReturnAsOf'
    END AS outcome,
    CASE
      WHEN @threshold_days IS NULL THEN NULL
      WHEN a.ineligibleReason IS NOT NULL THEN 'Ineligible'
      WHEN n.occurredAtUtc IS NOT NULL
        AND TIMESTAMP_DIFF(n.occurredAtUtc, a.segmentEndedAtUtc, MICROSECOND)
          <= @threshold_days * 86400000000 THEN 'ReturnedWithinThreshold'
      WHEN n.occurredAtUtc IS NOT NULL THEN 'ReturnedAfterThreshold'
      WHEN TIMESTAMP_SUB(@analysis_as_of_utc, INTERVAL @source_upload_grace_hours HOUR)
        >= TIMESTAMP_ADD(a.segmentEndedAtUtc, INTERVAL @threshold_days DAY)
        THEN 'NoObservedReturnBeyondThreshold'
      ELSE 'RightCensored'
    END AS thresholdState
  FROM eligible_anchors AS a
  LEFT JOIN next_returns AS n USING (anchorKey)
)
