-- Valid-sample DPS/uptime and coverage among combat-observed instance-segments.
WITH
-- @include weapon_population_ctes_v1
, weapon_instances AS (
  SELECT segments.environment, segments.runId,
    weapons.rowIndex, weapons.instanceId, weapons.weaponFamilyId, weapons.weaponId,
    weapons.uploadedAtUtc
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
sample_rows AS (
  SELECT samples.environment, samples.runId, samples.rowIndex, samples.entryIndex,
    samples.instanceId, samples.weaponFamilyId, samples.weaponId,
    samples.sampleStartElapsed, samples.duration, samples.sampleValid,
    samples.invalidReason, samples.effectiveDps, samples.mobDps, samples.bossDps,
    samples.appliedDamage, samples.mobDamage, samples.bossDamage,
    samples.uptimeRatio, samples.discardedSampleCountForEntry, samples.uploadedAtUtc
  FROM eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_weapon_analytics_samples` AS samples
    ON samples.environment = segments.environment AND samples.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR samples.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR samples.uploadedAtUtc < @uploaded_end_utc)
    AND samples.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY samples.environment, samples.runId, samples.rowIndex
    ORDER BY samples.uploadedAtUtc DESC) = 1
),
observed_samples AS (
  SELECT samples.*
  FROM sample_rows AS samples
  JOIN weapon_instances AS instances
    ON instances.environment = samples.environment AND instances.runId = samples.runId
   AND instances.instanceId IS NOT DISTINCT FROM samples.instanceId
),
grouped_instances AS (
  SELECT 'family' AS aggregationLevel, weaponFamilyId, CAST(NULL AS STRING) AS weaponId,
    environment, runId, instanceId
  FROM weapon_instances WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
  UNION ALL
  SELECT 'weaponState', weaponFamilyId, weaponId, environment, runId, instanceId
  FROM weapon_instances
  WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL AND NULLIF(TRIM(weaponId), '') IS NOT NULL
),
grouped_samples AS (
  SELECT 'family' AS aggregationLevel, weaponFamilyId, CAST(NULL AS STRING) AS weaponId, samples.* EXCEPT(weaponFamilyId, weaponId)
  FROM observed_samples AS samples WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
  UNION ALL
  SELECT 'weaponState', weaponFamilyId, weaponId, samples.* EXCEPT(weaponFamilyId, weaponId)
  FROM observed_samples AS samples
  WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL AND NULLIF(TRIM(weaponId), '') IS NOT NULL
),
valid_samples AS (
  SELECT * FROM grouped_samples WHERE sampleValid IS TRUE AND duration > 0
),
valid_enriched AS (
  SELECT *,
    PERCENTILE_CONT(effectiveDps, 0.25) OVER groupWindow AS effectiveDpsP25,
    PERCENTILE_CONT(effectiveDps, 0.50) OVER groupWindow AS effectiveDpsMedian,
    PERCENTILE_CONT(effectiveDps, 0.75) OVER groupWindow AS effectiveDpsP75,
    PERCENTILE_CONT(mobDps, 0.25) OVER groupWindow AS mobDpsP25,
    PERCENTILE_CONT(mobDps, 0.50) OVER groupWindow AS mobDpsMedian,
    PERCENTILE_CONT(mobDps, 0.75) OVER groupWindow AS mobDpsP75,
    PERCENTILE_CONT(bossDps, 0.25) OVER groupWindow AS bossDpsP25,
    PERCENTILE_CONT(bossDps, 0.50) OVER groupWindow AS bossDpsMedian,
    PERCENTILE_CONT(bossDps, 0.75) OVER groupWindow AS bossDpsP75,
    PERCENTILE_CONT(uptimeRatio, 0.25) OVER groupWindow AS uptimeP25,
    PERCENTILE_CONT(uptimeRatio, 0.50) OVER groupWindow AS uptimeMedian,
    PERCENTILE_CONT(uptimeRatio, 0.75) OVER groupWindow AS uptimeP75
  FROM valid_samples
  WINDOW groupWindow AS (PARTITION BY aggregationLevel, weaponFamilyId, weaponId)
),
instance_first_observation AS (
  SELECT aggregationLevel, weaponFamilyId, weaponId, environment, runId, instanceId,
    MIN(sampleStartElapsed) AS firstObservedElapsed
  FROM valid_samples
  WHERE sampleStartElapsed IS NOT NULL AND sampleStartElapsed >= 0
  GROUP BY aggregationLevel, weaponFamilyId, weaponId, environment, runId, instanceId
),
first_observation_enriched AS (
  SELECT *, PERCENTILE_CONT(firstObservedElapsed, 0.50) OVER (
    PARTITION BY aggregationLevel, weaponFamilyId, weaponId) AS firstObservedElapsedMedian
  FROM instance_first_observation
),
first_observation_stats AS (
  SELECT aggregationLevel, weaponFamilyId, weaponId,
    ANY_VALUE(firstObservedElapsedMedian) AS firstObservedElapsedMedian
  FROM first_observation_enriched GROUP BY aggregationLevel, weaponFamilyId, weaponId
),
instance_counts AS (
  SELECT aggregationLevel, weaponFamilyId, weaponId,
    COUNT(DISTINCT CONCAT(environment, ':', runId, ':', COALESCE(NULLIF(instanceId, ''), '__missing__'))) AS combatObservedInstanceSegments
  FROM grouped_instances GROUP BY aggregationLevel, weaponFamilyId, weaponId
),
valid_counts AS (
  SELECT aggregationLevel, weaponFamilyId, weaponId,
    COUNT(*) AS validSampleCount,
    COUNT(DISTINCT CONCAT(environment, ':', runId, ':', COALESCE(NULLIF(instanceId, ''), '__missing__'))) AS instanceSegmentsWithValidDpsSample,
    SUM(duration) AS totalValidDuration,
    SAFE_DIVIDE(SUM(appliedDamage), SUM(duration)) AS effectiveDpsWeighted,
    SAFE_DIVIDE(SUM(mobDamage), SUM(duration)) AS mobDpsWeighted,
    SAFE_DIVIDE(SUM(bossDamage), SUM(duration)) AS bossDpsWeighted,
    COUNTIF(uptimeRatio IS NOT NULL) AS uptimeObservedCount,
    COUNTIF(uptimeRatio IS NULL) AS uptimeMissingCount,
    ANY_VALUE(effectiveDpsP25) AS effectiveDpsP25,
    ANY_VALUE(effectiveDpsMedian) AS effectiveDpsMedian,
    ANY_VALUE(effectiveDpsP75) AS effectiveDpsP75,
    ANY_VALUE(mobDpsP25) AS mobDpsP25, ANY_VALUE(mobDpsMedian) AS mobDpsMedian, ANY_VALUE(mobDpsP75) AS mobDpsP75,
    ANY_VALUE(bossDpsP25) AS bossDpsP25, ANY_VALUE(bossDpsMedian) AS bossDpsMedian, ANY_VALUE(bossDpsP75) AS bossDpsP75,
    ANY_VALUE(uptimeP25) AS uptimeP25, ANY_VALUE(uptimeMedian) AS uptimeMedian, ANY_VALUE(uptimeP75) AS uptimeP75
  FROM valid_enriched GROUP BY aggregationLevel, weaponFamilyId, weaponId
),
invalid_counts AS (
  SELECT aggregationLevel, weaponFamilyId, weaponId, COUNTIF(sampleValid IS NOT TRUE OR duration IS NULL OR duration <= 0) AS invalidSampleCount
  FROM grouped_samples GROUP BY aggregationLevel, weaponFamilyId, weaponId
),
discarded_entries AS (
  SELECT aggregationLevel, weaponFamilyId, weaponId, environment, runId, entryIndex,
    MAX(COALESCE(discardedSampleCountForEntry, 0)) AS discarded
  FROM grouped_samples GROUP BY aggregationLevel, weaponFamilyId, weaponId, environment, runId, entryIndex
),
discarded_counts AS (
  SELECT aggregationLevel, weaponFamilyId, weaponId, SUM(discarded) AS discardedDpsSamples
  FROM discarded_entries GROUP BY aggregationLevel, weaponFamilyId, weaponId
),
metric_rows AS (
  SELECT 'metric' AS rowType, instances.aggregationLevel, instances.weaponFamilyId, instances.weaponId,
    CAST(NULL AS STRING) AS invalidReason, instances.combatObservedInstanceSegments,
    COALESCE(valid.instanceSegmentsWithValidDpsSample, 0) AS instanceSegmentsWithValidDpsSample,
    SAFE_DIVIDE(valid.instanceSegmentsWithValidDpsSample, instances.combatObservedInstanceSegments)
      AS dpsCoverageAmongCombatObservedInstanceSegments,
    COALESCE(valid.validSampleCount, 0) AS validSampleCount,
    COALESCE(invalid.invalidSampleCount, 0) AS invalidSampleCount,
    COALESCE(discarded.discardedDpsSamples, 0) AS discardedDpsSamples,
    valid.totalValidDuration, valid.effectiveDpsWeighted, valid.effectiveDpsP25, valid.effectiveDpsMedian, valid.effectiveDpsP75,
    valid.mobDpsWeighted, valid.mobDpsP25, valid.mobDpsMedian, valid.mobDpsP75,
    valid.bossDpsWeighted, valid.bossDpsP25, valid.bossDpsMedian, valid.bossDpsP75,
    COALESCE(valid.uptimeObservedCount, 0) AS uptimeObservedCount,
    COALESCE(valid.uptimeMissingCount, 0) AS uptimeMissingCount,
    valid.uptimeP25, valid.uptimeMedian, valid.uptimeP75, firstObserved.firstObservedElapsedMedian
  FROM instance_counts AS instances
  LEFT JOIN valid_counts AS valid
    ON valid.aggregationLevel = instances.aggregationLevel
   AND valid.weaponFamilyId = instances.weaponFamilyId
   AND valid.weaponId IS NOT DISTINCT FROM instances.weaponId
  LEFT JOIN invalid_counts AS invalid
    ON invalid.aggregationLevel = instances.aggregationLevel
   AND invalid.weaponFamilyId = instances.weaponFamilyId
   AND invalid.weaponId IS NOT DISTINCT FROM instances.weaponId
  LEFT JOIN discarded_counts AS discarded
    ON discarded.aggregationLevel = instances.aggregationLevel
   AND discarded.weaponFamilyId = instances.weaponFamilyId
   AND discarded.weaponId IS NOT DISTINCT FROM instances.weaponId
  LEFT JOIN first_observation_stats AS firstObserved
    ON firstObserved.aggregationLevel = instances.aggregationLevel
   AND firstObserved.weaponFamilyId = instances.weaponFamilyId
   AND firstObserved.weaponId IS NOT DISTINCT FROM instances.weaponId
),
invalid_reason_rows AS (
  SELECT 'invalidReason' AS rowType, aggregationLevel, weaponFamilyId, weaponId,
    COALESCE(NULLIF(invalidReason, ''), 'Missing') AS invalidReason,
    CAST(NULL AS INT64) AS combatObservedInstanceSegments,
    CAST(NULL AS INT64) AS instanceSegmentsWithValidDpsSample,
    CAST(NULL AS FLOAT64) AS dpsCoverageAmongCombatObservedInstanceSegments,
    CAST(NULL AS INT64) AS validSampleCount, COUNT(*) AS invalidSampleCount,
    CAST(NULL AS INT64) AS discardedDpsSamples, CAST(NULL AS FLOAT64) AS totalValidDuration,
    CAST(NULL AS FLOAT64) AS effectiveDpsWeighted, CAST(NULL AS FLOAT64) AS effectiveDpsP25,
    CAST(NULL AS FLOAT64) AS effectiveDpsMedian, CAST(NULL AS FLOAT64) AS effectiveDpsP75,
    CAST(NULL AS FLOAT64) AS mobDpsWeighted, CAST(NULL AS FLOAT64) AS mobDpsP25,
    CAST(NULL AS FLOAT64) AS mobDpsMedian, CAST(NULL AS FLOAT64) AS mobDpsP75,
    CAST(NULL AS FLOAT64) AS bossDpsWeighted, CAST(NULL AS FLOAT64) AS bossDpsP25,
    CAST(NULL AS FLOAT64) AS bossDpsMedian, CAST(NULL AS FLOAT64) AS bossDpsP75,
    CAST(NULL AS INT64) AS uptimeObservedCount, CAST(NULL AS INT64) AS uptimeMissingCount,
    CAST(NULL AS FLOAT64) AS uptimeP25, CAST(NULL AS FLOAT64) AS uptimeMedian,
    CAST(NULL AS FLOAT64) AS uptimeP75, CAST(NULL AS FLOAT64) AS firstObservedElapsedMedian
  FROM grouped_samples
  WHERE sampleValid IS NOT TRUE OR duration IS NULL OR duration <= 0
  GROUP BY aggregationLevel, weaponFamilyId, weaponId, invalidReason
)
SELECT * FROM metric_rows
UNION ALL
SELECT * FROM invalid_reason_rows
ORDER BY rowType, aggregationLevel, weaponFamilyId, weaponId, invalidReason
