-- One row per final attempt. This is a read-only analytical query, not a view DDL.
WITH feedback AS (
  SELECT
    environment,
    runId,
    COUNTIF(eventKind = 'Exposure') > 0 AS feedbackExposed,
    ARRAY_AGG(
      IF(eventKind = 'Response', response, NULL)
      IGNORE NULLS
      ORDER BY occurredAtUtc DESC, eventId DESC
      LIMIT 1
    )[SAFE_OFFSET(0)] AS feedbackResponse
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_feedback_events`
  GROUP BY environment, runId
)
SELECT
  attempts.environment,
  attempts.telemetryPlayerId,
  attempts.attemptId,
  attempts.runId,
  attempts.stageKey,
  attempts.gameplayOutcome,
  attempts.segmentStartedAtUtc,
  attempts.segmentEndedAtUtc,
  attempts.attemptElapsedTimeSeconds,
  attempts.appVersion,
  attempts.contentVersion,
  attempts.releaseId,
  attempts.releaseChannel,
  attempts.releaseType,
  attempts.isDevelopmentBuild,
  COALESCE(feedback.feedbackExposed, FALSE) AS feedbackExposed,
  feedback.feedbackResponse,
  upload_status.telemetryComplete
FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1` AS attempts
LEFT JOIN feedback
  ON feedback.environment = attempts.environment
 AND feedback.runId = attempts.runId
LEFT JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` AS upload_status
  ON upload_status.environment = attempts.environment
 AND upload_status.runId = attempts.runId
 AND upload_status.uploadId = attempts.uploadId
WHERE attempts.stageKey = @stage_key
  AND attempts.environment = @environment
  AND (@content_version IS NULL OR attempts.contentVersion = @content_version);

