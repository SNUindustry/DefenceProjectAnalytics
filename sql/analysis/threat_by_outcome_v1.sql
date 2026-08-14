-- Dead/Clear run-segment descriptive threat summaries over eligible detail runs.
WITH attempts AS (
  SELECT environment, telemetryPlayerId, attemptId, gameplayOutcome
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment AND stageKey = @stage_key AND contentVersion = @content_version
    AND gameplayOutcome IN ('Dead', 'Clear')
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
    s.releaseType, s.isDevelopmentBuild, a.gameplayOutcome AS finalOutcome
  FROM attempts a JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_gameplay_segments_v1` s
    ON s.environment = a.environment AND s.attemptId = a.attemptId
   AND s.telemetryPlayerId IS NOT DISTINCT FROM a.telemetryPlayerId
   AND (@uploaded_start_utc IS NULL OR s.uploadedAtUtc >= @uploaded_start_utc)
   AND (@uploaded_end_utc IS NULL OR s.uploadedAtUtc < @uploaded_end_utc)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY s.environment, s.runId ORDER BY s.uploadedAtUtc DESC, s.segmentIndex DESC) = 1
),
eligible AS (
  SELECT s.environment, s.runId, s.finalOutcome
  FROM segments s JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` u
    ON u.environment = s.environment AND u.runId = s.runId AND u.uploadId = s.uploadId
  WHERE u.telemetryComplete IS TRUE AND s.contentVersion = @content_version
    AND (@app_version IS NULL OR s.appVersion = @app_version)
    AND (@release_id IS NULL OR s.releaseId = @release_id)
    AND (@release_channel IS NULL OR s.releaseChannel = @release_channel)
    AND (@release_type IS NULL OR s.releaseType = @release_type)
    AND (@is_development_build IS NULL OR s.isDevelopmentBuild = @is_development_build)
),
observations AS (
  SELECT e.finalOutcome AS outcome, e.runId,
    CAST(t.nearDeathCount AS FLOAT64) AS nearDeathCount,
    t.lowHpDuration, t.insideDangerZoneDuration,
    t.avgDangerDistance, t.minDangerDistance,
    CAST(t.surroundedMoments AS FLOAT64) AS surroundedMoments,
    t.avgEnemyDensity
  FROM eligible e LEFT JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_threat_summary` t
    ON t.environment = e.environment AND t.runId = e.runId
   AND (@uploaded_start_utc IS NULL OR t.uploadedAtUtc >= @uploaded_start_utc)
   AND (@uploaded_end_utc IS NULL OR t.uploadedAtUtc < @uploaded_end_utc)
  QUALIFY t.runId IS NULL OR ROW_NUMBER() OVER (PARTITION BY t.environment, t.runId ORDER BY t.uploadedAtUtc DESC) = 1
),
long_values AS (
  SELECT outcome, runId, metric, value
  FROM observations
  UNPIVOT INCLUDE NULLS(value FOR metric IN (
    nearDeathCount, lowHpDuration, insideDangerZoneDuration, avgDangerDistance,
    minDangerDistance, surroundedMoments, avgEnemyDensity
  ))
),
percentile_rows AS (
  SELECT outcome, metric, runId, value,
    PERCENTILE_CONT(value, 0.25) OVER (PARTITION BY outcome, metric) AS p25,
    PERCENTILE_CONT(value, 0.50) OVER (PARTITION BY outcome, metric) AS median,
    PERCENTILE_CONT(value, 0.75) OVER (PARTITION BY outcome, metric) AS p75
  FROM long_values
)
SELECT outcome, metric, COUNT(*) AS eligible_run_count, COUNT(value) AS observed_count,
  COUNT(*) - COUNT(value) AS missing_count, AVG(value) AS mean,
  ANY_VALUE(p25) AS p25, ANY_VALUE(median) AS median, ANY_VALUE(p75) AS p75
FROM percentile_rows
GROUP BY outcome, metric
ORDER BY outcome, metric
