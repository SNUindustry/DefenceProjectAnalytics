-- Aggregate phase/floor/wave/zone attribution for complete final Dead runs.
WITH deaths AS (
  SELECT
    environment, runId, uploadId, attemptElapsedTimeSeconds, finalDeathElapsedTime,
    finalDeathPhaseIndex, finalDeathFloorNumber, finalDeathBossActive
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
eligible_deaths AS (
  SELECT d.*
  FROM deaths d
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` u
    ON u.environment = d.environment AND u.runId = d.runId AND u.uploadId = d.uploadId
  WHERE u.telemetryComplete IS TRUE
),
snapshot_rows AS (
  SELECT
    environment, runId, rowIndex, snapshotIndex, reason, elapsedTime,
    phaseIndex, waveNumber, floorNumber, zoneIndex, zoneType, bossActive, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_player_snapshots`
  WHERE environment = @environment
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY environment, runId, rowIndex ORDER BY uploadedAtUtc DESC) = 1
),
snapshot_candidates AS (
  SELECT
    d.runId,
    s.phaseIndex, s.waveNumber, s.floorNumber, s.zoneIndex, s.zoneType, s.bossActive,
    ROW_NUMBER() OVER (
      PARTITION BY d.environment, d.runId
      ORDER BY
        CASE
          WHEN s.reason = 'run_end' THEN 0
          WHEN s.elapsedTime <= d.finalDeathElapsedTime THEN 1
          ELSE 2
        END,
        s.elapsedTime DESC, s.snapshotIndex DESC, s.rowIndex DESC
    ) AS rank
  FROM eligible_deaths d
  JOIN snapshot_rows s
    ON s.environment = d.environment AND s.runId = d.runId
  WHERE s.reason = 'run_end'
     OR (d.finalDeathElapsedTime IS NOT NULL AND s.elapsedTime <= d.finalDeathElapsedTime)
     OR (d.attemptElapsedTimeSeconds IS NOT NULL AND s.elapsedTime <= d.attemptElapsedTimeSeconds)
),
selected_snapshot AS (SELECT * EXCEPT(rank) FROM snapshot_candidates WHERE rank = 1),
transition_rows AS (
  SELECT
    environment, runId, rowIndex, transitionIndex, elapsedTime,
    phaseIndex, waveNumber, floorNumber, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_transitions`
  WHERE environment = @environment
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY environment, runId, rowIndex ORDER BY uploadedAtUtc DESC) = 1
),
transition_candidates AS (
  SELECT
    d.runId, t.phaseIndex, t.waveNumber, t.floorNumber,
    ROW_NUMBER() OVER (
      PARTITION BY d.environment, d.runId
      ORDER BY t.elapsedTime DESC, t.transitionIndex DESC, t.rowIndex DESC
    ) AS rank
  FROM eligible_deaths d
  JOIN transition_rows t
    ON t.environment = d.environment AND t.runId = d.runId
  WHERE d.finalDeathElapsedTime IS NOT NULL AND t.elapsedTime <= d.finalDeathElapsedTime
),
selected_transition AS (SELECT * EXCEPT(rank) FROM transition_candidates WHERE rank = 1),
attributed AS (
  SELECT
    d.runId,
    COALESCE(d.finalDeathPhaseIndex, s.phaseIndex, t.phaseIndex) AS phase_value,
    d.finalDeathPhaseIndex IS NOT NULL AS phase_exact,
    COALESCE(d.finalDeathFloorNumber, s.floorNumber, t.floorNumber) AS floor_value,
    d.finalDeathFloorNumber IS NOT NULL AS floor_exact,
    COALESCE(s.waveNumber, t.waveNumber) AS wave_value,
    s.waveNumber IS NOT NULL OR t.waveNumber IS NOT NULL AS wave_approximate,
    s.zoneIndex AS zone_index, s.zoneType AS zone_type,
    COALESCE(d.finalDeathBossActive, s.bossActive) AS boss_value,
    d.finalDeathBossActive IS NOT NULL AS boss_exact
  FROM eligible_deaths d
  LEFT JOIN selected_snapshot s USING (runId)
  LEFT JOIN selected_transition t USING (runId)
),
distribution_rows AS (
  SELECT 'phase' AS dimension, CAST(phase_value AS STRING) AS value,
    COUNTIF(phase_exact) AS exact_count, COUNTIF(NOT phase_exact) AS approximate_count
  FROM attributed WHERE phase_value IS NOT NULL GROUP BY value
  UNION ALL
  SELECT 'floor', CAST(floor_value AS STRING), COUNTIF(floor_exact), COUNTIF(NOT floor_exact)
  FROM attributed WHERE floor_value IS NOT NULL GROUP BY floor_value
  UNION ALL
  SELECT 'wave', CAST(wave_value AS STRING), 0, COUNT(*)
  FROM attributed WHERE wave_value IS NOT NULL GROUP BY wave_value
  UNION ALL
  SELECT 'zone', CONCAT(COALESCE(zone_type, 'Unknown'), ':', CAST(zone_index AS STRING)), 0, COUNT(*)
  FROM attributed WHERE zone_index IS NOT NULL GROUP BY zone_type, zone_index
  UNION ALL
  SELECT 'boss', CAST(boss_value AS STRING), COUNTIF(boss_exact), COUNTIF(NOT boss_exact)
  FROM attributed WHERE boss_value IS NOT NULL GROUP BY boss_value
),
total AS (SELECT COUNT(*) AS denominator FROM attributed)
SELECT dimension, value, exact_count, approximate_count,
  exact_count + approximate_count AS count, total.denominator,
  SAFE_DIVIDE(exact_count + approximate_count, total.denominator) AS ratio
FROM distribution_rows CROSS JOIN total
ORDER BY dimension, count DESC, value
