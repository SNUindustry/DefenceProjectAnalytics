-- Aggregate selected terminal snapshot state for complete final Dead runs.
WITH deaths AS (
  SELECT
    environment, runId, uploadId, attemptElapsedTimeSeconds, finalDeathElapsedTime
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
eligible AS (
  SELECT d.* FROM deaths d
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` u
    ON u.environment = d.environment AND u.runId = d.runId AND u.uploadId = d.uploadId
  WHERE u.telemetryComplete IS TRUE
),
snapshot_rows AS (
  SELECT
    environment, runId, rowIndex, snapshotIndex, reason, elapsedTime, uploadedAtUtc,
    playerLevel, hpRatio, currentHp, maxHp, currentExp, maxExp, activeEnemyCount,
    ownedWeaponCount, upgradeSelectionCount, difficultyY, bossActive
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_player_snapshots`
  WHERE environment = @environment
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY environment, runId, rowIndex ORDER BY uploadedAtUtc DESC) = 1
),
candidate AS (
  SELECT d.runId, s.* EXCEPT(runId, environment),
    ROW_NUMBER() OVER (
      PARTITION BY d.environment, d.runId
      ORDER BY CASE WHEN s.reason = 'run_end' THEN 0 WHEN s.elapsedTime <= d.finalDeathElapsedTime THEN 1 ELSE 2 END,
        s.elapsedTime DESC, s.snapshotIndex DESC, s.rowIndex DESC
    ) AS selection_rank
  FROM eligible d
  JOIN snapshot_rows s
    ON s.environment = d.environment AND s.runId = d.runId
  WHERE s.reason = 'run_end'
     OR (d.finalDeathElapsedTime IS NOT NULL AND s.elapsedTime <= d.finalDeathElapsedTime)
     OR (d.attemptElapsedTimeSeconds IS NOT NULL AND s.elapsedTime <= d.attemptElapsedTimeSeconds)
),
selected AS (SELECT * FROM candidate WHERE selection_rank = 1),
coverage AS (
  SELECT (SELECT COUNT(*) FROM eligible) AS eligible_deaths, COUNT(*) AS selected_snapshots FROM selected
),
long_values AS (
  SELECT runId, metric, value
  FROM (
    SELECT runId,
      CAST(playerLevel AS FLOAT64) AS playerLevel,
      hpRatio, currentHp, maxHp, SAFE_DIVIDE(currentExp, maxExp) AS expRatio,
      CAST(activeEnemyCount AS FLOAT64) AS activeEnemyCount,
      CAST(ownedWeaponCount AS FLOAT64) AS ownedWeaponCount,
      CAST(upgradeSelectionCount AS FLOAT64) AS upgradeSelectionCount,
      difficultyY, IF(bossActive IS NULL, NULL, IF(bossActive, 1.0, 0.0)) AS bossActive
    FROM selected
  )
  UNPIVOT INCLUDE NULLS(value FOR metric IN (
    playerLevel, hpRatio, currentHp, maxHp, expRatio, activeEnemyCount,
    ownedWeaponCount, upgradeSelectionCount, difficultyY, bossActive
  ))
),
percentile_rows AS (
  SELECT metric, runId, value,
    PERCENTILE_CONT(value, 0.25) OVER (PARTITION BY metric) AS p25,
    PERCENTILE_CONT(value, 0.50) OVER (PARTITION BY metric) AS median,
    PERCENTILE_CONT(value, 0.75) OVER (PARTITION BY metric) AS p75
  FROM long_values
)
SELECT metric, coverage.eligible_deaths, coverage.selected_snapshots,
  COUNT(value) AS observed_count, coverage.selected_snapshots - COUNT(value) AS missing_count,
  AVG(value) AS mean, ANY_VALUE(p25) AS p25, ANY_VALUE(median) AS median, ANY_VALUE(p75) AS p75,
  COUNTIF(value = 1) AS true_count
FROM percentile_rows CROSS JOIN coverage
GROUP BY metric, eligible_deaths, selected_snapshots
ORDER BY metric
