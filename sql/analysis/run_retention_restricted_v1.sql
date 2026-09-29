-- Restricted normalized R4 input. Reuses the canonical B-7 CTE contract.
WITH
-- @include run_retention_population_ctes_v1
SELECT
  telemetryPlayerId AS canonicalProfileId,
  attemptId AS anchorId,
  segmentEndedAtUtc AS anchorEndUtc,
  linkageEligible AS anchorEligible,
  nextStartedAtUtc AS returnObservedAtUtc,
  structuralState AS returnClassification,
  @telemetry_backend AS telemetryBackend,
  environment,
  contentVersion,
  releaseId,
  TRUE AS completeSourceCoverage
FROM classified_retention
