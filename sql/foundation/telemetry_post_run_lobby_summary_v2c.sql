-- Query fixture. Replace <firebase-project-id>; supply @max_gap_minutes (README example: 30).
WITH final_runs AS (
  SELECT environment, telemetryPlayerId, runId, segmentEndedAtUtc,
    LEAD(segmentStartedAtUtc) OVER (
      PARTITION BY environment, telemetryPlayerId ORDER BY segmentStartedAtUtc, runId
    ) AS nextRunStartedAtUtc
  FROM `<firebase-project-id>.game_telemetry.telemetry_run_summary`
  WHERE segmentKind = 'Gameplay' AND isAttemptFinal = TRUE
    AND gameplayOutcome IN ('Clear', 'Dead', 'Abandon')
), lobby_events AS (
  SELECT environment, telemetryPlayerId, occurredAtUtc, eventId,
    IF(eventKind = 'TabViewed', 1, 0) AS tabView,
    IF(eventKind = 'ShopSectionViewed', 1, 0) AS sectionView,
    0 AS offerExposure, 0 AS offerSelection, 0 AS txAttempt, 0 AS txSuccess,
    0 AS txFailure, 0 AS progression, 0 AS iap
  FROM `<firebase-project-id>.game_telemetry.telemetry_lobby_activity_events`
  UNION ALL
  SELECT environment, telemetryPlayerId, occurredAtUtc, eventId, 0, 0,
    IF(eventKind = 'OfferExposure', 1, 0), IF(eventKind = 'OfferSelected', 1, 0), 0, 0, 0, 0, 0
  FROM `<firebase-project-id>.game_telemetry.telemetry_shop_events`
  UNION ALL
  SELECT environment, telemetryPlayerId, occurredAtUtc, eventId, 0, 0, 0, 0,
    IF(eventKind = 'Attempt', 1, 0),
    IF(eventKind = 'Result' AND resultCategory IN ('Succeeded', 'Duplicate'), 1, 0),
    IF(eventKind = 'Result' AND resultCategory IN ('Cancelled', 'Blocked', 'Failed'), 1, 0), 0, 0
  FROM `<firebase-project-id>.game_telemetry.telemetry_transaction_events`
  UNION ALL
  SELECT environment, telemetryPlayerId, occurredAtUtc, eventId, 0, 0, 0, 0, 0, 0, 0, 1, 0
  FROM `<firebase-project-id>.game_telemetry.telemetry_progression_events`
  UNION ALL
  SELECT environment, telemetryPlayerId, occurredAtUtc, eventId, 0, 0, 0, 0, 0, 0, 0, 0, 1
  FROM `<firebase-project-id>.game_telemetry.telemetry_iap_events`
), attributed AS (
  SELECT r.*, e.* EXCEPT(environment, telemetryPlayerId)
  FROM final_runs r
  LEFT JOIN lobby_events e
    ON e.environment = r.environment AND e.telemetryPlayerId = r.telemetryPlayerId
   AND e.occurredAtUtc > r.segmentEndedAtUtc
   AND e.occurredAtUtc <= TIMESTAMP_ADD(r.segmentEndedAtUtc, INTERVAL @max_gap_minutes MINUTE)
   AND (r.nextRunStartedAtUtc IS NULL OR e.occurredAtUtc < r.nextRunStartedAtUtc)
), lobby_summary AS (
  SELECT environment, telemetryPlayerId, runId,
    SUM(tabView) AS postRunTabViews,
    SUM(sectionView) AS postRunShopSectionViews,
    SUM(offerExposure) AS offerExposureCount,
    SUM(offerSelection) AS offerSelectionCount,
    SUM(txAttempt) AS transactionAttemptCount,
    SUM(txSuccess) AS transactionSuccessCount,
    SUM(txFailure) AS transactionFailureCount,
    SUM(progression) AS progressionChangeCount,
    SUM(iap) AS iapLifecycleCount,
    TIMESTAMP_DIFF(nextRunStartedAtUtc, segmentEndedAtUtc, SECOND) AS timeToNextRunSeconds
  FROM attributed
  GROUP BY environment, telemetryPlayerId, runId, segmentEndedAtUtc, nextRunStartedAtUtc
), feedback AS (
  SELECT environment, runId,
    COUNTIF(eventKind = 'Exposure') > 0 AS feedbackExposure,
    ARRAY_AGG(
      IF(eventKind = 'Response', response, NULL)
      IGNORE NULLS ORDER BY occurredAtUtc DESC, eventId DESC LIMIT 1
    )[SAFE_OFFSET(0)] AS feedbackResponse
  FROM `<firebase-project-id>.game_telemetry.telemetry_run_feedback_events`
  GROUP BY environment, runId
)
SELECT l.*,
  COALESCE(f.feedbackExposure, FALSE) AS feedbackExposure,
  f.feedbackResponse
FROM lobby_summary l
LEFT JOIN feedback f
  ON f.environment = l.environment AND f.runId = l.runId;
