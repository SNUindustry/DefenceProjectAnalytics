-- Aggregate final-attempt, fully-covered detail, and weapon-summary quality.
WITH
-- @include weapon_population_ctes_v1
, weapon_rows AS (
  SELECT
    segments.environment, segments.runId, segments.attemptId,
    weapons.rowIndex, weapons.instanceId, weapons.weaponFamilyId,
    weapons.totalDamage, weapons.damageShare, weapons.uploadedAtUtc
  FROM eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_weapon_summary` AS weapons
    ON weapons.environment = segments.environment AND weapons.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR weapons.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR weapons.uploadedAtUtc < @uploaded_end_utc)
    AND weapons.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY weapons.environment, weapons.runId, weapons.rowIndex
    ORDER BY weapons.uploadedAtUtc DESC
  ) = 1
),
segment_damage AS (
  SELECT environment, runId, SUM(COALESCE(totalDamage, 0)) AS totalDamage
  FROM weapon_rows GROUP BY environment, runId
),
weapon_quality AS (
  SELECT
    COUNT(DISTINCT weapon.attemptId) AS weaponSummaryObservedAttempts,
    COUNT(DISTINCT NULLIF(TRIM(weapon.weaponFamilyId), '')) AS weaponFamilyCount,
    COUNTIF(NULLIF(TRIM(weapon.weaponFamilyId), '') IS NULL) AS missingWeaponIdentityRows,
    COUNTIF(weapon.damageShare > 0 AND ABS(SAFE_DIVIDE(weapon.totalDamage, weapon.damageShare) - totals.totalDamage)
      > GREATEST(0.000001, ABS(totals.totalDamage) * 0.01)) AS inconsistentDamageShareRows
  FROM weapon_rows AS weapon
  JOIN segment_damage AS totals USING (environment, runId)
),
attempt_summary AS (
  SELECT
    COUNT(*) AS finalAttempts,
    COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) AS uniquePlayers,
    COUNTIF(gameplayOutcome = 'Clear') AS clears,
    COUNTIF(gameplayOutcome = 'Dead') AS deaths,
    COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
    COUNTIF(gameplayOutcome IS NULL OR gameplayOutcome NOT IN ('Clear', 'Dead', 'Abandon')) AS unrecognizedOutcomes,
    COUNTIF(releaseIdentityResolved IS NOT TRUE OR contentVersion = 0 OR NULLIF(TRIM(releaseId), '') IS NULL) AS unresolvedReleaseRows
  FROM final_attempts
),
detail_summary AS (
  SELECT
    COUNT(*) AS detailCandidateSegments,
    COUNT(DISTINCT attemptId) AS detailObservedAttempts,
    COUNTIF(NOT scopeMatches) AS mixedContentSegmentsExcluded,
    COUNTIF(scopeMatches AND telemetryComplete IS NOT NULL AND NOT completeAsOf) AS incompleteSegmentsExcluded,
    COUNTIF(scopeMatches AND telemetryComplete IS NULL) AS legacyUnassessedSegments,
    COUNTIF(scopeMatches AND telemetryComplete IS NOT NULL) AS assessedSegments,
    COUNTIF(scopeMatches AND completeAsOf) AS completeSegments
  FROM classified_segments
),
coverage_summary AS (
  SELECT
    COUNT(*) AS detailCandidateAttempts,
    COUNTIF(candidateSegments > 0 AND eligibleSegments = candidateSegments) AS detailEligibleAttempts,
    COUNTIF(eligibleSegments > 0 AND eligibleSegments < candidateSegments) AS partiallyCoveredAttempts
  FROM attempt_coverage
),
eligible_summary AS (SELECT COUNT(*) AS eligibleSegments FROM eligible_segments)
SELECT
  attempt_summary.*,
  detail_summary.*,
  coverage_summary.*,
  eligible_summary.eligibleSegments,
  weapon_quality.*
FROM attempt_summary
CROSS JOIN detail_summary
CROSS JOIN coverage_summary
CROSS JOIN eligible_summary
CROSS JOIN weapon_quality
