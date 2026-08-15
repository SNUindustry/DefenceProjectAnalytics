-- Resume-aware family and weapon-state combat aggregates.
WITH
-- @include weapon_population_ctes_v1
, weapon_rows AS (
  SELECT segments.attemptId, segments.environment, segments.runId,
    weapons.rowIndex, weapons.instanceId, weapons.weaponFamilyId, weapons.weaponId,
    COALESCE(weapons.totalDamage, 0) AS totalDamage,
    COALESCE(weapons.bossDamage, 0) AS bossDamage,
    COALESCE(weapons.hits, 0) AS hits, weapons.uploadedAtUtc
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
attempt_denominators AS (
  SELECT attemptId, SUM(totalDamage) AS allWeaponDamage
  FROM weapon_rows GROUP BY attemptId
),
family_attempt AS (
  SELECT weapon.weaponFamilyId, weapon.attemptId,
    SUM(weapon.totalDamage) AS damage, SUM(weapon.bossDamage) AS bossDamage,
    SUM(weapon.hits) AS hits, SAFE_DIVIDE(SUM(weapon.totalDamage), ANY_VALUE(denom.allWeaponDamage)) AS damageShare
  FROM weapon_rows AS weapon JOIN attempt_denominators AS denom USING (attemptId)
  WHERE NULLIF(TRIM(weapon.weaponFamilyId), '') IS NOT NULL
  GROUP BY weapon.weaponFamilyId, weapon.attemptId
),
family_enriched AS (
  SELECT *,
    AVG(damage) OVER (PARTITION BY weaponFamilyId) AS damageMean,
    PERCENTILE_CONT(damage, 0.25) OVER (PARTITION BY weaponFamilyId) AS damageP25,
    PERCENTILE_CONT(damage, 0.50) OVER (PARTITION BY weaponFamilyId) AS damageMedian,
    PERCENTILE_CONT(damage, 0.75) OVER (PARTITION BY weaponFamilyId) AS damageP75,
    PERCENTILE_CONT(damage, 0.90) OVER (PARTITION BY weaponFamilyId) AS damageP90,
    AVG(bossDamage) OVER (PARTITION BY weaponFamilyId) AS bossDamageMean,
    PERCENTILE_CONT(bossDamage, 0.25) OVER (PARTITION BY weaponFamilyId) AS bossDamageP25,
    PERCENTILE_CONT(bossDamage, 0.50) OVER (PARTITION BY weaponFamilyId) AS bossDamageMedian,
    PERCENTILE_CONT(bossDamage, 0.75) OVER (PARTITION BY weaponFamilyId) AS bossDamageP75,
    PERCENTILE_CONT(bossDamage, 0.90) OVER (PARTITION BY weaponFamilyId) AS bossDamageP90,
    AVG(hits) OVER (PARTITION BY weaponFamilyId) AS hitsMean,
    PERCENTILE_CONT(hits, 0.25) OVER (PARTITION BY weaponFamilyId) AS hitsP25,
    PERCENTILE_CONT(hits, 0.50) OVER (PARTITION BY weaponFamilyId) AS hitsMedian,
    PERCENTILE_CONT(hits, 0.75) OVER (PARTITION BY weaponFamilyId) AS hitsP75,
    PERCENTILE_CONT(hits, 0.90) OVER (PARTITION BY weaponFamilyId) AS hitsP90,
    AVG(damageShare) OVER (PARTITION BY weaponFamilyId) AS damageShareMean,
    PERCENTILE_CONT(damageShare, 0.25) OVER (PARTITION BY weaponFamilyId) AS damageShareP25,
    PERCENTILE_CONT(damageShare, 0.50) OVER (PARTITION BY weaponFamilyId) AS damageShareMedian,
    PERCENTILE_CONT(damageShare, 0.75) OVER (PARTITION BY weaponFamilyId) AS damageShareP75,
    PERCENTILE_CONT(damageShare, 0.90) OVER (PARTITION BY weaponFamilyId) AS damageShareP90
  FROM family_attempt
),
family_counts AS (
  SELECT weaponFamilyId,
    COUNT(DISTINCT attemptId) AS combatObservedAttempts,
    COUNT(DISTINCT runId) AS combatObservedSegments,
    COUNT(DISTINCT CONCAT(environment, ':', runId, ':', COALESCE(NULLIF(instanceId, ''), CONCAT('row:', CAST(rowIndex AS STRING))))) AS combatObservedInstanceSegments,
    SUM(totalDamage) AS totalDamage, SUM(bossDamage) AS totalBossDamage, SUM(hits) AS totalHits
  FROM weapon_rows WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL GROUP BY weaponFamilyId
),
family_output AS (
  SELECT 'family' AS aggregationLevel, counts.weaponFamilyId, CAST(NULL AS STRING) AS weaponId,
    (SELECT COUNT(*) FROM eligible_attempts) AS eligibleAttempts,
    counts.* EXCEPT(weaponFamilyId),
    ANY_VALUE(enriched.damageMean) AS damageMean, ANY_VALUE(enriched.damageP25) AS damageP25,
    ANY_VALUE(enriched.damageMedian) AS damageMedian, ANY_VALUE(enriched.damageP75) AS damageP75,
    ANY_VALUE(enriched.damageP90) AS damageP90,
    ANY_VALUE(enriched.bossDamageMean) AS bossDamageMean, ANY_VALUE(enriched.bossDamageP25) AS bossDamageP25,
    ANY_VALUE(enriched.bossDamageMedian) AS bossDamageMedian, ANY_VALUE(enriched.bossDamageP75) AS bossDamageP75,
    ANY_VALUE(enriched.bossDamageP90) AS bossDamageP90,
    ANY_VALUE(enriched.hitsMean) AS hitsMean, ANY_VALUE(enriched.hitsP25) AS hitsP25,
    ANY_VALUE(enriched.hitsMedian) AS hitsMedian, ANY_VALUE(enriched.hitsP75) AS hitsP75,
    ANY_VALUE(enriched.hitsP90) AS hitsP90,
    ANY_VALUE(enriched.damageShareMean) AS damageShareMean, ANY_VALUE(enriched.damageShareP25) AS damageShareP25,
    ANY_VALUE(enriched.damageShareMedian) AS damageShareMedian, ANY_VALUE(enriched.damageShareP75) AS damageShareP75,
    ANY_VALUE(enriched.damageShareP90) AS damageShareP90
  FROM family_counts AS counts JOIN family_enriched AS enriched USING (weaponFamilyId)
  GROUP BY counts.weaponFamilyId, counts.combatObservedAttempts, counts.combatObservedSegments,
    counts.combatObservedInstanceSegments, counts.totalDamage, counts.totalBossDamage, counts.totalHits
),
state_attempt AS (
  SELECT weapon.weaponFamilyId, weapon.weaponId, weapon.attemptId,
    SUM(weapon.totalDamage) AS damage, SUM(weapon.bossDamage) AS bossDamage, SUM(weapon.hits) AS hits,
    SAFE_DIVIDE(SUM(weapon.totalDamage), ANY_VALUE(denom.allWeaponDamage)) AS damageShare
  FROM weapon_rows AS weapon JOIN attempt_denominators AS denom USING (attemptId)
  WHERE NULLIF(TRIM(weapon.weaponFamilyId), '') IS NOT NULL AND NULLIF(TRIM(weapon.weaponId), '') IS NOT NULL
  GROUP BY weapon.weaponFamilyId, weapon.weaponId, weapon.attemptId
),
state_enriched AS (
  SELECT *,
    AVG(damage) OVER stateWindow AS damageMean,
    PERCENTILE_CONT(damage, 0.25) OVER stateWindow AS damageP25,
    PERCENTILE_CONT(damage, 0.50) OVER stateWindow AS damageMedian,
    PERCENTILE_CONT(damage, 0.75) OVER stateWindow AS damageP75,
    PERCENTILE_CONT(damage, 0.90) OVER stateWindow AS damageP90,
    AVG(bossDamage) OVER stateWindow AS bossDamageMean,
    PERCENTILE_CONT(bossDamage, 0.25) OVER stateWindow AS bossDamageP25,
    PERCENTILE_CONT(bossDamage, 0.50) OVER stateWindow AS bossDamageMedian,
    PERCENTILE_CONT(bossDamage, 0.75) OVER stateWindow AS bossDamageP75,
    PERCENTILE_CONT(bossDamage, 0.90) OVER stateWindow AS bossDamageP90,
    AVG(hits) OVER stateWindow AS hitsMean,
    PERCENTILE_CONT(hits, 0.25) OVER stateWindow AS hitsP25,
    PERCENTILE_CONT(hits, 0.50) OVER stateWindow AS hitsMedian,
    PERCENTILE_CONT(hits, 0.75) OVER stateWindow AS hitsP75,
    PERCENTILE_CONT(hits, 0.90) OVER stateWindow AS hitsP90,
    AVG(damageShare) OVER stateWindow AS damageShareMean,
    PERCENTILE_CONT(damageShare, 0.25) OVER stateWindow AS damageShareP25,
    PERCENTILE_CONT(damageShare, 0.50) OVER stateWindow AS damageShareMedian,
    PERCENTILE_CONT(damageShare, 0.75) OVER stateWindow AS damageShareP75,
    PERCENTILE_CONT(damageShare, 0.90) OVER stateWindow AS damageShareP90
  FROM state_attempt WINDOW stateWindow AS (PARTITION BY weaponFamilyId, weaponId)
),
state_counts AS (
  SELECT weaponFamilyId, weaponId,
    COUNT(DISTINCT attemptId) AS combatObservedAttempts, COUNT(DISTINCT runId) AS combatObservedSegments,
    COUNT(DISTINCT CONCAT(environment, ':', runId, ':', COALESCE(NULLIF(instanceId, ''), CONCAT('row:', CAST(rowIndex AS STRING))))) AS combatObservedInstanceSegments,
    SUM(totalDamage) AS totalDamage, SUM(bossDamage) AS totalBossDamage, SUM(hits) AS totalHits
  FROM weapon_rows
  WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL AND NULLIF(TRIM(weaponId), '') IS NOT NULL
  GROUP BY weaponFamilyId, weaponId
),
state_output AS (
  SELECT 'weaponState' AS aggregationLevel, counts.weaponFamilyId, counts.weaponId,
    (SELECT COUNT(*) FROM eligible_attempts) AS eligibleAttempts,
    counts.* EXCEPT(weaponFamilyId, weaponId),
    ANY_VALUE(enriched.damageMean) AS damageMean, ANY_VALUE(enriched.damageP25) AS damageP25,
    ANY_VALUE(enriched.damageMedian) AS damageMedian, ANY_VALUE(enriched.damageP75) AS damageP75,
    ANY_VALUE(enriched.damageP90) AS damageP90,
    ANY_VALUE(enriched.bossDamageMean) AS bossDamageMean, ANY_VALUE(enriched.bossDamageP25) AS bossDamageP25,
    ANY_VALUE(enriched.bossDamageMedian) AS bossDamageMedian, ANY_VALUE(enriched.bossDamageP75) AS bossDamageP75,
    ANY_VALUE(enriched.bossDamageP90) AS bossDamageP90,
    ANY_VALUE(enriched.hitsMean) AS hitsMean, ANY_VALUE(enriched.hitsP25) AS hitsP25,
    ANY_VALUE(enriched.hitsMedian) AS hitsMedian, ANY_VALUE(enriched.hitsP75) AS hitsP75,
    ANY_VALUE(enriched.hitsP90) AS hitsP90,
    ANY_VALUE(enriched.damageShareMean) AS damageShareMean, ANY_VALUE(enriched.damageShareP25) AS damageShareP25,
    ANY_VALUE(enriched.damageShareMedian) AS damageShareMedian, ANY_VALUE(enriched.damageShareP75) AS damageShareP75,
    ANY_VALUE(enriched.damageShareP90) AS damageShareP90
  FROM state_counts AS counts JOIN state_enriched AS enriched USING (weaponFamilyId, weaponId)
  GROUP BY counts.weaponFamilyId, counts.weaponId, counts.combatObservedAttempts,
    counts.combatObservedSegments, counts.combatObservedInstanceSegments,
    counts.totalDamage, counts.totalBossDamage, counts.totalHits
)
SELECT * FROM family_output
UNION ALL
SELECT * FROM state_output
ORDER BY aggregationLevel, weaponFamilyId, weaponId
