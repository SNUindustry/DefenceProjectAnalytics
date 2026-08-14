-- Aggregate incoming damage over every eligible gameplay segment in selected attempts.
WITH attempts AS (
  SELECT environment, telemetryPlayerId, attemptId
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment AND stageKey = @stage_key AND contentVersion = @content_version
    AND (@app_version IS NULL OR appVersion = @app_version)
    AND (@release_id IS NULL OR releaseId = @release_id)
    AND (@release_channel IS NULL OR releaseChannel = @release_channel)
    AND (@release_type IS NULL OR releaseType = @release_type)
    AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
),
segments AS (
  SELECT
    s.environment, s.runId, s.uploadId, s.segmentIndex, s.uploadedAtUtc,
    s.contentVersion, s.appVersion, s.releaseId, s.releaseChannel,
    s.releaseType, s.isDevelopmentBuild
  FROM attempts a
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_gameplay_segments_v1` s
   ON s.environment = a.environment AND s.attemptId = a.attemptId
   AND s.telemetryPlayerId IS NOT DISTINCT FROM a.telemetryPlayerId
   AND (@uploaded_start_utc IS NULL OR s.uploadedAtUtc >= @uploaded_start_utc)
   AND (@uploaded_end_utc IS NULL OR s.uploadedAtUtc < @uploaded_end_utc)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY s.environment, s.runId ORDER BY s.uploadedAtUtc DESC, s.segmentIndex DESC) = 1
),
eligible AS (
  SELECT s.environment, s.runId
  FROM segments s
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` u
    ON u.environment = s.environment AND u.runId = s.runId AND u.uploadId = s.uploadId
  WHERE u.telemetryComplete IS TRUE AND s.contentVersion = @content_version
    AND (@app_version IS NULL OR s.appVersion = @app_version)
    AND (@release_id IS NULL OR s.releaseId = @release_id)
    AND (@release_channel IS NULL OR s.releaseChannel = @release_channel)
    AND (@release_type IS NULL OR s.releaseType = @release_type)
    AND (@is_development_build IS NULL OR s.isDevelopmentBuild = @is_development_build)
),
damage AS (
  SELECT
    d.environment, d.runId, d.rowIndex, d.uploadedAtUtc, d.sourceClassification,
    d.enemyDefinitionId, d.enemyTier, d.enemyClassification,
    d.totalAppliedDamage, d.hitCount, d.lethalHitCount
  FROM eligible e
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_enemy_damage_summary` d
    ON d.environment = e.environment AND d.runId = e.runId
   AND (@uploaded_start_utc IS NULL OR d.uploadedAtUtc >= @uploaded_start_utc)
   AND (@uploaded_end_utc IS NULL OR d.uploadedAtUtc < @uploaded_end_utc)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY d.environment, d.runId, d.rowIndex ORDER BY d.uploadedAtUtc DESC) = 1
),
source_rows AS (
  SELECT 'source' AS row_type,
    CASE WHEN sourceClassification IS NULL THEN 'Missing' WHEN sourceClassification = 0 THEN 'Unknown'
      WHEN sourceClassification = 1 THEN 'Enemy' WHEN sourceClassification = 2 THEN 'Environment'
      ELSE CONCAT('Unrecognized:', CAST(sourceClassification AS STRING)) END AS label,
    CAST(NULL AS INT64) AS enemy_tier, CAST(NULL AS STRING) AS enemy_classification,
    SUM(COALESCE(totalAppliedDamage, 0)) AS total_applied_damage,
    SUM(COALESCE(hitCount, 0)) AS hit_count, SUM(COALESCE(lethalHitCount, 0)) AS lethal_hit_events,
    COUNT(DISTINCT runId) AS affected_runs, COUNT(DISTINCT IF(COALESCE(lethalHitCount, 0) > 0, runId, NULL)) AS lethal_runs
  FROM damage GROUP BY label
),
enemy_rows AS (
  SELECT 'enemy' AS row_type, COALESCE(NULLIF(enemyDefinitionId, ''), 'UnknownEnemy') AS label,
    enemyTier AS enemy_tier, enemyClassification AS enemy_classification,
    SUM(COALESCE(totalAppliedDamage, 0)) AS total_applied_damage,
    SUM(COALESCE(hitCount, 0)) AS hit_count, SUM(COALESCE(lethalHitCount, 0)) AS lethal_hit_events,
    COUNT(DISTINCT runId) AS affected_runs, COUNT(DISTINCT IF(COALESCE(lethalHitCount, 0) > 0, runId, NULL)) AS lethal_runs
  FROM damage WHERE sourceClassification = 1
  GROUP BY label, enemy_tier, enemy_classification
),
combined AS (SELECT * FROM source_rows UNION ALL SELECT * FROM enemy_rows),
total AS (SELECT SUM(COALESCE(totalAppliedDamage, 0)) AS damage FROM damage)
SELECT combined.*, SAFE_DIVIDE(total_applied_damage, total.damage) AS damage_ratio
FROM combined CROSS JOIN total
ORDER BY row_type, total_applied_damage DESC, label
