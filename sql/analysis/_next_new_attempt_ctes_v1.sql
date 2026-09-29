run_start_physical AS (
  SELECT environment, runId, isFromResume, uploadedAtUtc, rowIndex
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_start_snapshot`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
),
run_start_deduped AS (
  SELECT environment, runId, isFromResume
  FROM run_start_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId
    ORDER BY uploadedAtUtc DESC, COALESCE(rowIndex, -1) DESC
  ) = 1
),
new_attempt_candidate_physical AS (
  SELECT
    g.environment, g.telemetryPlayerId, g.attemptId, g.runId, g.stageKey,
    g.segmentStartedAtUtc, g.contentVersion, g.releaseId, g.releaseChannel,
    g.releaseType, g.uploadedAtUtc, s.isFromResume,
    s.runId IS NOT NULL AS hasRunStartSnapshot
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_summary` AS g
  LEFT JOIN run_start_deduped AS s
    ON s.environment = g.environment AND s.runId = g.runId
  WHERE g.environment = @environment
    AND g.segmentKind = 'Gameplay'
    AND g.uploadedAtUtc < @analysis_as_of_utc
    AND g.segmentStartedAtUtc < @analysis_as_of_utc
    AND g.segmentIndex = 1
    AND NULLIF(TRIM(g.attemptId), '') IS NOT NULL
),
new_attempt_segments AS (
  SELECT
    environment, telemetryPlayerId, attemptId, runId, stageKey,
    segmentStartedAtUtc, contentVersion, releaseId, releaseChannel, releaseType,
    hasRunStartSnapshot
  FROM new_attempt_candidate_physical AS s
  WHERE s.isFromResume IS NOT TRUE
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, telemetryPlayerId, attemptId
    ORDER BY uploadedAtUtc DESC, runId DESC
  ) = 1
)
