final_attempts AS (
  SELECT
    environment, telemetryPlayerId, attemptId, runId AS finalRunId,
    gameplayOutcome, attemptElapsedTimeSeconds, segmentEndedAtUtc,
    appVersion, contentVersion, releaseId, releaseChannel, releaseType,
    isDevelopmentBuild, releaseIdentityResolved, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment
    AND stageKey = @stage_key
    AND contentVersion = @content_version
    AND (@app_version IS NULL OR appVersion = @app_version)
    AND (@release_id IS NULL OR releaseId = @release_id)
    AND (@release_channel IS NULL OR releaseChannel = @release_channel)
    AND (@release_type IS NULL OR releaseType = @release_type)
    AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
    AND (@segment_ended_start_utc IS NULL OR segmentEndedAtUtc >= @segment_ended_start_utc)
    AND (@segment_ended_end_utc IS NULL OR segmentEndedAtUtc < @segment_ended_end_utc)
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
    AND uploadedAtUtc < @analysis_as_of_utc
),
candidate_segments AS (
  SELECT
    attempts.environment, attempts.telemetryPlayerId, attempts.attemptId,
    attempts.finalRunId, attempts.gameplayOutcome, attempts.attemptElapsedTimeSeconds,
    segments.runId, segments.uploadId, segments.segmentIndex,
    segments.contentVersion, segments.appVersion, segments.releaseId,
    segments.releaseChannel, segments.releaseType, segments.isDevelopmentBuild,
    segments.uploadedAtUtc
  FROM final_attempts AS attempts
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_gameplay_segments_v1` AS segments
    ON segments.environment = attempts.environment
   AND segments.attemptId = attempts.attemptId
   AND segments.telemetryPlayerId IS NOT DISTINCT FROM attempts.telemetryPlayerId
  WHERE (@uploaded_start_utc IS NULL OR segments.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR segments.uploadedAtUtc < @uploaded_end_utc)
    AND segments.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY segments.environment, segments.runId
    ORDER BY segments.uploadedAtUtc DESC, segments.segmentIndex DESC
  ) = 1
),
classified_segments AS (
  SELECT
    candidate.*,
    candidate.contentVersion = @content_version
      AND (@app_version IS NULL OR candidate.appVersion = @app_version)
      AND (@release_id IS NULL OR candidate.releaseId = @release_id)
      AND (@release_channel IS NULL OR candidate.releaseChannel = @release_channel)
      AND (@release_type IS NULL OR candidate.releaseType = @release_type)
      AND (@is_development_build IS NULL OR candidate.isDevelopmentBuild = @is_development_build)
      AS scopeMatches,
    status.telemetryComplete,
    status.completedAtUtc,
    status.telemetryComplete IS TRUE AND status.completedAtUtc < @analysis_as_of_utc AS completeAsOf
  FROM candidate_segments AS candidate
  LEFT JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` AS status
    ON status.environment = candidate.environment
   AND status.runId = candidate.runId
   AND status.uploadId = candidate.uploadId
),
attempt_coverage AS (
  SELECT
    environment, telemetryPlayerId, attemptId,
    COUNT(*) AS candidateSegments,
    COUNTIF(scopeMatches AND completeAsOf) AS eligibleSegments
  FROM classified_segments
  GROUP BY environment, telemetryPlayerId, attemptId
),
eligible_attempts AS (
  SELECT environment, telemetryPlayerId, attemptId
  FROM attempt_coverage
  WHERE candidateSegments > 0 AND eligibleSegments = candidateSegments
),
eligible_segments AS (
  SELECT classified.*
  FROM classified_segments AS classified
  JOIN eligible_attempts AS attempts
    ON attempts.environment = classified.environment
   AND attempts.attemptId = classified.attemptId
   AND attempts.telemetryPlayerId IS NOT DISTINCT FROM classified.telemetryPlayerId
  WHERE classified.scopeMatches AND classified.completeAsOf
)
