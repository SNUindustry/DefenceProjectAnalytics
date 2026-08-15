-- Boss-specific family aggregates using BossStarted gameplay segments only.
WITH
-- @include weapon_population_ctes_v1
, transition_rows AS (
  SELECT segments.environment, segments.runId, transitions.rowIndex, transitions.transitionType,
    transitions.uploadedAtUtc
  FROM eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_transitions` AS transitions
    ON transitions.environment = segments.environment AND transitions.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR transitions.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR transitions.uploadedAtUtc < @uploaded_end_utc)
    AND transitions.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY transitions.environment, transitions.runId, transitions.rowIndex
    ORDER BY transitions.uploadedAtUtc DESC) = 1
),
boss_segments AS (
  SELECT DISTINCT environment, runId FROM transition_rows WHERE transitionType = 'BossStarted'
),
weapon_rows AS (
  SELECT segments.environment, segments.runId, weapons.rowIndex,
    weapons.weaponFamilyId, weapons.weaponId, COALESCE(weapons.totalDamage, 0) AS totalDamage,
    COALESCE(weapons.bossDamage, 0) AS bossDamage, weapons.uploadedAtUtc
  FROM boss_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_weapon_summary` AS weapons
    ON weapons.environment = segments.environment AND weapons.runId = segments.runId
  WHERE COALESCE(weapons.totalDamage, 0) > 0
    AND (@uploaded_start_utc IS NULL OR weapons.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR weapons.uploadedAtUtc < @uploaded_end_utc)
    AND weapons.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY weapons.environment, weapons.runId, weapons.rowIndex
    ORDER BY weapons.uploadedAtUtc DESC) = 1
),
boss_totals AS (SELECT SUM(bossDamage) AS allBossDamage FROM weapon_rows),
sample_rows AS (
  SELECT samples.environment, samples.runId, samples.rowIndex, samples.weaponFamilyId,
    samples.sampleValid, samples.duration, samples.bossActiveRatio, samples.bossBucket,
    samples.bossDamage, samples.uploadedAtUtc
  FROM boss_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_weapon_analytics_samples` AS samples
    ON samples.environment = segments.environment AND samples.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR samples.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR samples.uploadedAtUtc < @uploaded_end_utc)
    AND samples.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY samples.environment, samples.runId, samples.rowIndex
    ORDER BY samples.uploadedAtUtc DESC) = 1
),
valid_boss_samples AS (
  SELECT * FROM sample_rows WHERE sampleValid IS TRUE AND duration > 0 AND bossActiveRatio > 0
),
sample_stats AS (
  SELECT weaponFamilyId, COUNT(*) AS validBossSampleCount,
    SAFE_DIVIDE(SUM(bossDamage), SUM(duration)) AS bossDpsWeighted,
    COUNTIF(bossBucket = 'Mob') AS mobBucketSamples,
    COUNTIF(bossBucket = 'Mixed') AS mixedBucketSamples,
    COUNTIF(bossBucket = 'Boss') AS bossBucketSamples
  FROM valid_boss_samples WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL GROUP BY weaponFamilyId
),
weapon_stats AS (
  SELECT weaponFamilyId,
    COUNT(DISTINCT runId) AS familyBossEligibleSegments,
    SUM(totalDamage) AS totalDamage, SUM(bossDamage) AS totalBossDamage
  FROM weapon_rows WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL GROUP BY weaponFamilyId
)
SELECT stats.weaponFamilyId,
  (SELECT COUNT(*) FROM boss_segments) AS bossEligibleSegments,
  stats.familyBossEligibleSegments, stats.totalDamage, stats.totalBossDamage,
  SAFE_DIVIDE(stats.totalBossDamage, totals.allBossDamage) AS bossDamageShare,
  COALESCE(samples.validBossSampleCount, 0) AS validBossSampleCount,
  samples.bossDpsWeighted,
  COALESCE(samples.mobBucketSamples, 0) AS mobBucketSamples,
  COALESCE(samples.mixedBucketSamples, 0) AS mixedBucketSamples,
  COALESCE(samples.bossBucketSamples, 0) AS bossBucketSamples
FROM weapon_stats AS stats CROSS JOIN boss_totals AS totals
LEFT JOIN sample_stats AS samples USING (weaponFamilyId)
ORDER BY totalBossDamage DESC, weaponFamilyId
