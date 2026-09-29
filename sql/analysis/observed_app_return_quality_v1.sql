WITH
-- @include observed_app_return_ctes_v1
SELECT
  (SELECT COUNT(*) FROM lifecycle_physical) AS physicalRowsRead,
  (SELECT COUNT(*) FROM lifecycle_normalized WHERE variantCount = 1)
    AS logicalOccurrences,
  (SELECT COALESCE(SUM(physicalCount - 1), 0)
     FROM occurrence_groups WHERE variantCount = 1) AS exactDuplicateRowsRemoved,
  (SELECT COUNT(*) FROM occurrence_groups WHERE variantCount > 1)
    AS conflictingOccurrenceKeys,
  (SELECT COUNTIF(telemetryPlayerId IS NULL OR TRIM(telemetryPlayerId) = '')
     FROM lifecycle_normalized WHERE variantCount = 1)
    AS missingPlayerId,
  (SELECT COUNTIF(NOT validIds OR
      (telemetryPlayerId IS NOT NULL AND TRIM(telemetryPlayerId) != ''
        AND NOT validPlayer))
     FROM lifecycle_normalized WHERE variantCount = 1)
    AS invalidId,
  (SELECT COUNTIF(NOT validTime) FROM lifecycle_normalized WHERE variantCount = 1)
    AS invalidTimestamp,
  (SELECT COUNTIF(NOT validShape) FROM lifecycle_shaped WHERE variantCount = 1)
    AS timingQualityExcluded,
  (SELECT COUNTIF(profileAttributionStatus != 'CanonicalProfile')
     FROM lifecycle_normalized WHERE variantCount = 1) AS attributionExcluded,
  (SELECT COUNTIF(NOT nonQa) FROM lifecycle_normalized WHERE variantCount = 1)
    AS developmentExcluded,
  (SELECT COUNTIF(coldStartProcessReuse) FROM lifecycle_shaped WHERE variantCount = 1)
    AS coldStartProcessReuse,
  COUNT(*) AS anchorsTotal,
  COUNTIF(ineligibleReason IS NULL) AS anchorsEligible,
  COUNTIF(ineligibleReason IS NOT NULL) AS anchorsIneligible,
  COUNTIF(ineligibleReason = 'MissingLifecycleBaseline') AS missingLifecycleBaseline,
  COUNTIF(ineligibleReason = 'ProductionDevelopmentExcluded')
    AS productionDevelopmentExcluded,
  COUNTIF(runLifecycleLinkageMissing) AS runLifecycleLinkageMissing,
  COUNTIF(runLifecycleLinkageMismatch) AS runLifecycleLinkageMismatch,
  COUNTIF(sameTimeCount > 1) AS candidateTimestampTies,
  COUNTIF(ineligibleReason = 'InvalidPlayerId') AS invalidAnchorPlayerId,
  COUNTIF(ineligibleReason = 'ConflictingAttemptPlayer') AS conflictingAnchorIdentity,
  COUNTIF(ineligibleReason = 'InvalidAnchorEnd') AS invalidAnchorEnd
FROM (
  SELECT a.*, n.sameTimeCount
  FROM eligible_anchors AS a
  LEFT JOIN next_returns AS n USING (anchorKey)
)
