-- Family-level adoption, loadout, acquisition, and final ownership aggregates.
WITH
-- @include weapon_population_ctes_v1
, weapon_rows AS (
  SELECT segments.attemptId, segments.environment, segments.runId,
    weapons.rowIndex, weapons.weaponFamilyId, weapons.weaponId, weapons.uploadedAtUtc
  FROM eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_weapon_summary` AS weapons
    ON weapons.environment = segments.environment AND weapons.runId = segments.runId
  WHERE COALESCE(weapons.totalDamage, 0) > 0
    AND (@uploaded_start_utc IS NULL OR weapons.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR weapons.uploadedAtUtc < @uploaded_end_utc)
    AND weapons.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY weapons.environment, weapons.runId, weapons.rowIndex
    ORDER BY weapons.uploadedAtUtc DESC) = 1
),
final_state_rows AS (
  SELECT attempts.attemptId, attempts.gameplayOutcome,
    states.rowIndex, states.weaponFamilyId, states.weaponId, states.weaponType,
    states.baseLevel, states.effectiveLevel, states.isEvolutionResult, states.isActive,
    states.uploadedAtUtc
  FROM final_attempts AS attempts
  JOIN eligible_segments AS segments
    ON segments.environment = attempts.environment AND segments.runId = attempts.finalRunId
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_final_weapon_state` AS states
    ON states.environment = segments.environment AND states.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR states.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR states.uploadedAtUtc < @uploaded_end_utc)
    AND states.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY states.environment, states.runId, states.rowIndex
    ORDER BY states.uploadedAtUtc DESC) = 1
),
start_snapshots AS (
  SELECT segments.attemptId, segments.environment, segments.runId, snapshots.loadoutWeaponCount
  FROM eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_start_snapshot` AS snapshots
    ON snapshots.environment = segments.environment AND snapshots.runId = segments.runId
  WHERE segments.segmentIndex = 1 AND snapshots.isFromResume IS FALSE
    AND (@uploaded_start_utc IS NULL OR snapshots.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR snapshots.uploadedAtUtc < @uploaded_end_utc)
    AND snapshots.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY snapshots.environment, snapshots.runId, snapshots.rowIndex
    ORDER BY snapshots.uploadedAtUtc DESC) = 1
),
loadout_rows AS (
  SELECT starts.attemptId, starts.environment, starts.runId, starts.loadoutWeaponCount,
    loadout.rowIndex, loadout.weaponFamilyId
  FROM start_snapshots AS starts
  LEFT JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_loadout` AS loadout
    ON loadout.environment = starts.environment AND loadout.runId = starts.runId
   AND (@uploaded_start_utc IS NULL OR loadout.uploadedAtUtc >= @uploaded_start_utc)
   AND (@uploaded_end_utc IS NULL OR loadout.uploadedAtUtc < @uploaded_end_utc)
   AND loadout.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY loadout.rowIndex IS NULL OR ROW_NUMBER() OVER (
    PARTITION BY loadout.environment, loadout.runId, loadout.rowIndex ORDER BY loadout.uploadedAtUtc DESC) = 1
),
loadout_assessment AS (
  SELECT attemptId, ANY_VALUE(loadoutWeaponCount) AS expectedCount,
    COUNTIF(NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL) AS observedCount
  FROM loadout_rows GROUP BY attemptId
),
assessed_start_attempts AS (
  SELECT attemptId FROM loadout_assessment WHERE expectedCount = observedCount
),
identity_candidates AS (
  SELECT weaponId, weaponFamilyId FROM weapon_rows
  UNION ALL
  SELECT weaponId, weaponFamilyId FROM final_state_rows
),
identity_map AS (
  SELECT weaponId,
    IF(COUNT(DISTINCT NULLIF(TRIM(weaponFamilyId), '')) = 1,
      ANY_VALUE(NULLIF(TRIM(weaponFamilyId), '')), NULL) AS weaponFamilyId,
    COUNT(DISTINCT NULLIF(TRIM(weaponFamilyId), '')) AS familyCount
  FROM identity_candidates
  WHERE NULLIF(TRIM(weaponId), '') IS NOT NULL
  GROUP BY weaponId
),
selection_rows AS (
  SELECT segments.attemptId, selections.rowIndex, selections.category,
    selections.weaponFamilyId, selections.grantWeaponId,
    COALESCE(selections.elapsedTime, selections.segmentElapsedTime) AS selectionElapsed,
    selections.uploadedAtUtc
  FROM eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upgrade_selections` AS selections
    ON selections.environment = segments.environment AND selections.runId = segments.runId
  WHERE selections.category IN (0, 1, 4)
    AND (@uploaded_start_utc IS NULL OR selections.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR selections.uploadedAtUtc < @uploaded_end_utc)
    AND selections.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY selections.environment, selections.runId, selections.rowIndex
    ORDER BY selections.uploadedAtUtc DESC) = 1
),
mapped_selections AS (
  SELECT selections.*,
    CASE WHEN category = 1 THEN NULLIF(TRIM(selections.weaponFamilyId), '')
      ELSE mapping.weaponFamilyId END AS mappedFamilyId
  FROM selection_rows AS selections
  LEFT JOIN identity_map AS mapping ON mapping.weaponId = selections.grantWeaponId
),
family_universe AS (
  SELECT DISTINCT weaponFamilyId FROM weapon_rows WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
  UNION DISTINCT SELECT DISTINCT weaponFamilyId FROM final_state_rows WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
  UNION DISTINCT SELECT DISTINCT weaponFamilyId FROM loadout_rows WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
  UNION DISTINCT SELECT DISTINCT mappedFamilyId FROM mapped_selections WHERE mappedFamilyId IS NOT NULL
),
combat_stats AS (
  SELECT weaponFamilyId, COUNT(DISTINCT runId) AS combatObservedSegments,
    COUNT(DISTINCT attemptId) AS combatObservedAttempts
  FROM weapon_rows WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL GROUP BY weaponFamilyId
),
loadout_stats AS (
  SELECT loadout.weaponFamilyId, COUNT(DISTINCT loadout.attemptId) AS startingLoadoutAttempts
  FROM loadout_rows AS loadout JOIN assessed_start_attempts USING (attemptId)
  WHERE NULLIF(TRIM(loadout.weaponFamilyId), '') IS NOT NULL GROUP BY loadout.weaponFamilyId
),
selection_enriched AS (
  SELECT *,
    PERCENTILE_CONT(IF(category = 0 AND selectionElapsed >= 0, selectionElapsed, NULL), 0.25)
      OVER (PARTITION BY mappedFamilyId) AS acquisitionP25,
    PERCENTILE_CONT(IF(category = 0 AND selectionElapsed >= 0, selectionElapsed, NULL), 0.50)
      OVER (PARTITION BY mappedFamilyId) AS acquisitionMedian,
    PERCENTILE_CONT(IF(category = 0 AND selectionElapsed >= 0, selectionElapsed, NULL), 0.75)
      OVER (PARTITION BY mappedFamilyId) AS acquisitionP75
  FROM mapped_selections WHERE mappedFamilyId IS NOT NULL
),
selection_stats AS (
  SELECT mappedFamilyId AS weaponFamilyId,
    COUNT(DISTINCT IF(category = 0, attemptId, NULL)) AS newWeaponAcquisitionAttempts,
    COUNTIF(category = 1) AS upgradeSelectionCount,
    COUNTIF(category = 4) AS evolutionSelectionCount,
    ANY_VALUE(acquisitionP25) AS acquisitionP25,
    ANY_VALUE(acquisitionMedian) AS acquisitionMedian,
    ANY_VALUE(acquisitionP75) AS acquisitionP75
  FROM selection_enriched GROUP BY mappedFamilyId
),
final_state_enriched AS (
  SELECT *,
    PERCENTILE_CONT(effectiveLevel, 0.25) OVER (PARTITION BY weaponFamilyId) AS finalLevelP25,
    PERCENTILE_CONT(effectiveLevel, 0.50) OVER (PARTITION BY weaponFamilyId) AS finalLevelMedian,
    PERCENTILE_CONT(effectiveLevel, 0.75) OVER (PARTITION BY weaponFamilyId) AS finalLevelP75
  FROM final_state_rows WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
),
final_stats AS (
  SELECT weaponFamilyId, COUNT(DISTINCT attemptId) AS finalOwnedAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Clear', attemptId, NULL)) AS finalOwnedClearAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Dead', attemptId, NULL)) AS finalOwnedDeadAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Abandon', attemptId, NULL)) AS finalOwnedAbandonAttempts,
    COUNT(DISTINCT IF(isActive IS TRUE, attemptId, NULL)) AS finalActiveAttempts,
    COUNT(DISTINCT IF(isEvolutionResult IS TRUE, attemptId, NULL)) AS finalEvolvedAttempts,
    ANY_VALUE(finalLevelP25) AS finalLevelP25,
    ANY_VALUE(finalLevelMedian) AS finalLevelMedian,
    ANY_VALUE(finalLevelP75) AS finalLevelP75
  FROM final_state_enriched GROUP BY weaponFamilyId
),
denominators AS (
  SELECT
    (SELECT COUNT(*) FROM eligible_attempts) AS eligibleAttempts,
    (SELECT COUNT(*) FROM eligible_segments) AS eligibleSegments,
    (SELECT COUNT(*) FROM assessed_start_attempts) AS startLoadoutAssessedAttempts,
    (SELECT COUNT(DISTINCT attemptId) FROM final_state_rows) AS finalStateAssessedAttempts,
    (SELECT COUNTIF(mappedFamilyId IS NULL) FROM mapped_selections) AS unmappedAcquisitionRows
)
SELECT universe.weaponFamilyId,
  denominators.*,
  COALESCE(combat.combatObservedSegments, 0) AS combatObservedSegments,
  COALESCE(combat.combatObservedAttempts, 0) AS combatObservedAttempts,
  SAFE_DIVIDE(combat.combatObservedSegments, denominators.eligibleSegments) AS segmentInclusionRatio,
  SAFE_DIVIDE(combat.combatObservedAttempts, denominators.eligibleAttempts) AS attemptInclusionRatio,
  COALESCE(loadout.startingLoadoutAttempts, 0) AS startingLoadoutAttempts,
  SAFE_DIVIDE(COALESCE(loadout.startingLoadoutAttempts, 0), denominators.startLoadoutAssessedAttempts) AS startingLoadoutRatio,
  COALESCE(selections.newWeaponAcquisitionAttempts, 0) AS newWeaponAcquisitionAttempts,
  selections.acquisitionP25, selections.acquisitionMedian, selections.acquisitionP75,
  COALESCE(selections.upgradeSelectionCount, 0) AS upgradeSelectionCount,
  COALESCE(selections.evolutionSelectionCount, 0) AS evolutionSelectionCount,
  COALESCE(finals.finalOwnedAttempts, 0) AS finalOwnedAttempts,
  SAFE_DIVIDE(COALESCE(finals.finalOwnedAttempts, 0), denominators.finalStateAssessedAttempts) AS finalOwnershipRatio,
  COALESCE(finals.finalOwnedClearAttempts, 0) AS finalOwnedClearAttempts,
  COALESCE(finals.finalOwnedDeadAttempts, 0) AS finalOwnedDeadAttempts,
  COALESCE(finals.finalOwnedAbandonAttempts, 0) AS finalOwnedAbandonAttempts,
  COALESCE(finals.finalActiveAttempts, 0) AS finalActiveAttempts,
  SAFE_DIVIDE(COALESCE(finals.finalActiveAttempts, 0), COALESCE(finals.finalOwnedAttempts, 0)) AS finalActiveRatio,
  COALESCE(finals.finalEvolvedAttempts, 0) AS finalEvolvedAttempts,
  SAFE_DIVIDE(COALESCE(finals.finalEvolvedAttempts, 0), COALESCE(finals.finalOwnedAttempts, 0)) AS finalEvolvedRatio,
  finals.finalLevelP25, finals.finalLevelMedian, finals.finalLevelP75
FROM family_universe AS universe
CROSS JOIN denominators
LEFT JOIN combat_stats AS combat USING (weaponFamilyId)
LEFT JOIN loadout_stats AS loadout USING (weaponFamilyId)
LEFT JOIN selection_stats AS selections USING (weaponFamilyId)
LEFT JOIN final_stats AS finals USING (weaponFamilyId)
ORDER BY weaponFamilyId
