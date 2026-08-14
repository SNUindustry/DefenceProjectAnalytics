CREATE OR REPLACE VIEW `<firebase-project-id>.game_telemetry.telemetry_attempt_outcomes_v1` AS
SELECT *
FROM `<firebase-project-id>.game_telemetry.telemetry_run_summary`
WHERE isAttemptFinal = TRUE
  AND attemptId IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY environment, telemetryPlayerId, attemptId
  ORDER BY segmentIndex DESC, segmentEndedAtUtc DESC, uploadedAtUtc DESC, runId DESC
) = 1;
