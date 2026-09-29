-- Restricted normalized R4 input. Reuses the canonical B-8 CTE contract.
WITH
-- @include observed_app_return_ctes_v1
SELECT
  telemetryPlayerId AS canonicalProfileId,
  JSON_VALUE(anchorKey, '$.attemptId') AS anchorId,
  segmentEndedAtUtc AS anchorEndUtc,
  ineligibleReason IS NULL AS anchorEligible,
  occurredAtUtc AS returnObservedAtUtc,
  outcome AS returnClassification,
  @telemetry_backend AS telemetryBackend,
  @environment AS environment,
  contentVersion,
  releaseId,
  TRUE AS completeSourceCoverage
FROM classified
