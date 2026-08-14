-- Query fixture. Replace <firebase-project-id> and bind @max_gap_minutes when embedding.
-- The max-gap parameter is intentionally exposed to callers; this query does not define sessions.
SELECT environment, telemetryPlayerId, segmentStartedAtUtc AS occurredAtUtc,
       'RunStarted' AS eventType, gameplayOutcome AS eventSubType, runId AS eventId, runId, attemptId, 0 AS rowIndex
FROM `<firebase-project-id>.game_telemetry.telemetry_run_summary`
WHERE segmentStartedAtUtc IS NOT NULL
UNION ALL
SELECT environment, telemetryPlayerId, segmentEndedAtUtc, 'RunEnded', gameplayOutcome, runId, runId, attemptId, 0
FROM `<firebase-project-id>.game_telemetry.telemetry_run_summary`
WHERE segmentEndedAtUtc IS NOT NULL
UNION ALL
SELECT environment, telemetryPlayerId, occurredAtUtc, 'Feedback', eventKind, eventId, runId, attemptId, 0
FROM `<firebase-project-id>.game_telemetry.telemetry_run_feedback_events`
UNION ALL
SELECT environment, telemetryPlayerId, occurredAtUtc, 'LobbyActivity', eventKind, eventId, NULL, NULL, rowIndex
FROM `<firebase-project-id>.game_telemetry.telemetry_lobby_activity_events`
UNION ALL
SELECT environment, telemetryPlayerId, occurredAtUtc, 'Transaction', eventKind, eventId, NULL, NULL, rowIndex
FROM `<firebase-project-id>.game_telemetry.telemetry_transaction_events`
UNION ALL
SELECT environment, telemetryPlayerId, occurredAtUtc, 'Shop', eventKind, eventId, NULL, NULL, rowIndex
FROM `<firebase-project-id>.game_telemetry.telemetry_shop_events`
UNION ALL
SELECT environment, telemetryPlayerId, occurredAtUtc, 'Progression', progressionKind, eventId, NULL, NULL, rowIndex
FROM `<firebase-project-id>.game_telemetry.telemetry_progression_events`
UNION ALL
SELECT environment, telemetryPlayerId, occurredAtUtc, 'IAP', eventKind, eventId, NULL, NULL, rowIndex
FROM `<firebase-project-id>.game_telemetry.telemetry_iap_events`
ORDER BY environment, telemetryPlayerId, occurredAtUtc,
  CASE eventType WHEN 'RunStarted' THEN 0 WHEN 'RunEnded' THEN 9 ELSE 5 END,
  rowIndex, eventId;
