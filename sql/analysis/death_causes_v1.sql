-- Aggregate final lethal source, enemy, and environment effect distributions.
WITH deaths AS (
  SELECT
    finalDeathSourceClassification AS source_code,
    finalDeathEnemyDefinitionId AS enemy_id,
    finalDeathEnemyTier AS enemy_tier,
    finalDeathEnemyClassification AS enemy_classification,
    IF(finalDeathEffectIdAvailable IS TRUE, finalDeathEffectId, NULL) AS effect_id
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment AND stageKey = @stage_key AND contentVersion = @content_version
    AND gameplayOutcome = 'Dead'
    AND (@app_version IS NULL OR appVersion = @app_version)
    AND (@release_id IS NULL OR releaseId = @release_id)
    AND (@release_channel IS NULL OR releaseChannel = @release_channel)
    AND (@release_type IS NULL OR releaseType = @release_type)
    AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
),
totals AS (SELECT COUNT(*) AS deaths FROM deaths),
sources AS (
  SELECT
    'source' AS row_type,
    CASE WHEN source_code IS NULL THEN 'Missing' WHEN source_code = 0 THEN 'Unknown'
      WHEN source_code = 1 THEN 'Enemy' WHEN source_code = 2 THEN 'Environment'
      ELSE CONCAT('Unrecognized:', CAST(source_code AS STRING)) END AS label,
    CAST(NULL AS INT64) AS enemy_tier,
    CAST(NULL AS STRING) AS enemy_classification,
    COUNT(*) AS count
  FROM deaths GROUP BY label
),
enemies AS (
  SELECT 'enemy' AS row_type, COALESCE(NULLIF(enemy_id, ''), 'UnknownEnemy') AS label,
    enemy_tier, enemy_classification, COUNT(*) AS count
  FROM deaths WHERE source_code = 1
  GROUP BY label, enemy_tier, enemy_classification
),
effects AS (
  SELECT 'environmentEffect' AS row_type, effect_id AS label,
    CAST(NULL AS INT64) AS enemy_tier, CAST(NULL AS STRING) AS enemy_classification, COUNT(*) AS count
  FROM deaths WHERE source_code = 2 AND effect_id IS NOT NULL
  GROUP BY effect_id
)
SELECT row_type, label, enemy_tier, enemy_classification, count, totals.deaths AS denominator,
  SAFE_DIVIDE(count, totals.deaths) AS ratio
FROM (SELECT * FROM sources UNION ALL SELECT * FROM enemies UNION ALL SELECT * FROM effects)
CROSS JOIN totals
ORDER BY row_type, count DESC, label
