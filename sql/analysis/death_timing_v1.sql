-- Fixed death-time buckets with exact continuous percentiles.
WITH deaths AS (
  SELECT
    attemptElapsedTimeSeconds AS attempt_time,
    CASE
      WHEN finalDeathElapsedTime IS NOT NULL
       AND segmentStartElapsedTimeSeconds IS NOT NULL
       AND finalDeathElapsedTime - segmentStartElapsedTimeSeconds >= 0
      THEN finalDeathElapsedTime - segmentStartElapsedTimeSeconds
    END AS final_segment_time
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment
    AND stageKey = @stage_key
    AND contentVersion = @content_version
    AND gameplayOutcome = 'Dead'
    AND (@app_version IS NULL OR appVersion = @app_version)
    AND (@release_id IS NULL OR releaseId = @release_id)
    AND (@release_channel IS NULL OR releaseChannel = @release_channel)
    AND (@release_type IS NULL OR releaseType = @release_type)
    AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
),
valid AS (
  SELECT * FROM deaths WHERE attempt_time IS NOT NULL AND attempt_time >= 0
),
attempt_percentile_rows AS (
  SELECT
    PERCENTILE_CONT(attempt_time, 0.10) OVER () AS p10,
    PERCENTILE_CONT(attempt_time, 0.25) OVER () AS p25,
    PERCENTILE_CONT(attempt_time, 0.50) OVER () AS p50,
    PERCENTILE_CONT(attempt_time, 0.75) OVER () AS p75,
    PERCENTILE_CONT(attempt_time, 0.90) OVER () AS p90
  FROM valid
),
segment_percentile_rows AS (
  SELECT
    PERCENTILE_CONT(final_segment_time, 0.10) OVER () AS p10,
    PERCENTILE_CONT(final_segment_time, 0.25) OVER () AS p25,
    PERCENTILE_CONT(final_segment_time, 0.50) OVER () AS p50,
    PERCENTILE_CONT(final_segment_time, 0.75) OVER () AS p75,
    PERCENTILE_CONT(final_segment_time, 0.90) OVER () AS p90
  FROM deaths WHERE final_segment_time IS NOT NULL
),
buckets AS (
  SELECT * FROM UNNEST([
    STRUCT(0 AS bucket_order, '0-30s' AS bucket, 0.0 AS lower_bound, 30.0 AS upper_bound),
    (1, '30-60s', 30.0, 60.0),
    (2, '1-2m', 60.0, 120.0),
    (3, '2-5m', 120.0, 300.0),
    (4, '5-10m', 300.0, 600.0),
    (5, '10m+', 600.0, CAST(NULL AS FLOAT64))
  ])
),
stats AS (
  SELECT
    COUNT(*) AS death_count,
    COUNTIF(attempt_time IS NULL OR attempt_time < 0) AS invalid_death_time_rows,
    COUNTIF(attempt_time IS NOT NULL AND attempt_time >= 0) AS timed_deaths,
    COUNTIF(final_segment_time IS NOT NULL) AS valid_final_segment_times,
    (SELECT ANY_VALUE(p10) FROM attempt_percentile_rows) AS attempt_p10,
    (SELECT ANY_VALUE(p25) FROM attempt_percentile_rows) AS attempt_p25,
    (SELECT ANY_VALUE(p50) FROM attempt_percentile_rows) AS attempt_p50,
    (SELECT ANY_VALUE(p75) FROM attempt_percentile_rows) AS attempt_p75,
    (SELECT ANY_VALUE(p90) FROM attempt_percentile_rows) AS attempt_p90,
    (SELECT ANY_VALUE(p10) FROM segment_percentile_rows) AS final_segment_p10,
    (SELECT ANY_VALUE(p25) FROM segment_percentile_rows) AS final_segment_p25,
    (SELECT ANY_VALUE(p50) FROM segment_percentile_rows) AS final_segment_p50,
    (SELECT ANY_VALUE(p75) FROM segment_percentile_rows) AS final_segment_p75,
    (SELECT ANY_VALUE(p90) FROM segment_percentile_rows) AS final_segment_p90
  FROM deaths
)
SELECT
  buckets.bucket_order,
  buckets.bucket,
  COUNTIF(valid.attempt_time >= buckets.lower_bound AND (buckets.upper_bound IS NULL OR valid.attempt_time < buckets.upper_bound)) AS count,
  stats.timed_deaths AS denominator,
  SAFE_DIVIDE(COUNTIF(valid.attempt_time >= buckets.lower_bound AND (buckets.upper_bound IS NULL OR valid.attempt_time < buckets.upper_bound)), stats.timed_deaths) AS ratio,
  stats.* EXCEPT(timed_deaths)
FROM buckets
CROSS JOIN stats
LEFT JOIN valid ON TRUE
GROUP BY bucket_order, bucket, lower_bound, upper_bound, denominator, death_count, invalid_death_time_rows, valid_final_segment_times,
  attempt_p10, attempt_p25, attempt_p50, attempt_p75, attempt_p90,
  final_segment_p10, final_segment_p25, final_segment_p50, final_segment_p75, final_segment_p90
ORDER BY bucket_order
