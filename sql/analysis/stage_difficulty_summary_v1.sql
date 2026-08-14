-- One aggregate row for the requested final-attempt scope and detail coverage.
WITH final_attempts AS (
  SELECT
    environment, telemetryPlayerId, attemptId, gameplayOutcome,
    attemptElapsedTimeSeconds, contentVersion, releaseId, releaseIdentityResolved
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment
    AND stageKey = @stage_key
    AND contentVersion = @content_version
    AND (@app_version IS NULL OR appVersion = @app_version)
    AND (@release_id IS NULL OR releaseId = @release_id)
    AND (@release_channel IS NULL OR releaseChannel = @release_channel)
    AND (@release_type IS NULL OR releaseType = @release_type)
    AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
),
candidate_segments AS (
  SELECT
    attempts.attemptId,
    segments.runId,
    segments.uploadId,
    segments.contentVersion,
    segments.appVersion,
    segments.releaseId,
    segments.releaseChannel,
    segments.releaseType,
    segments.isDevelopmentBuild,
    segments.uploadedAtUtc
  FROM final_attempts AS attempts
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_gameplay_segments_v1` AS segments
    ON segments.environment = attempts.environment
   AND segments.attemptId = attempts.attemptId
   AND segments.telemetryPlayerId IS NOT DISTINCT FROM attempts.telemetryPlayerId
   AND (@uploaded_start_utc IS NULL OR segments.uploadedAtUtc >= @uploaded_start_utc)
   AND (@uploaded_end_utc IS NULL OR segments.uploadedAtUtc < @uploaded_end_utc)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY segments.environment, segments.runId
    ORDER BY segments.uploadedAtUtc DESC, segments.segmentIndex DESC
  ) = 1
),
classified_segments AS (
  SELECT
    candidate.*,
    candidate.contentVersion = @content_version
      AND (@app_version IS NULL OR candidate.appVersion = @app_version)
      AND (@release_id IS NULL OR candidate.releaseId = @release_id)
      AND (@release_channel IS NULL OR candidate.releaseChannel = @release_channel)
      AND (@release_type IS NULL OR candidate.releaseType = @release_type)
      AND (@is_development_build IS NULL OR candidate.isDevelopmentBuild = @is_development_build)
      AS scope_matches,
    status.telemetryComplete
  FROM candidate_segments AS candidate
  LEFT JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` AS status
    ON status.environment = @environment
   AND status.runId = candidate.runId
   AND status.uploadId = candidate.uploadId
),
attempt_coverage AS (
  SELECT
    attemptId,
    COUNT(*) AS candidate_runs,
    COUNTIF(scope_matches AND telemetryComplete IS TRUE) AS eligible_runs
  FROM classified_segments
  GROUP BY attemptId
),
duration_rows AS (
  SELECT
    PERCENTILE_CONT(attemptElapsedTimeSeconds, 0.25) OVER () AS p25,
    PERCENTILE_CONT(attemptElapsedTimeSeconds, 0.50) OVER () AS p50,
    PERCENTILE_CONT(attemptElapsedTimeSeconds, 0.75) OVER () AS p75,
    PERCENTILE_CONT(attemptElapsedTimeSeconds, 0.90) OVER () AS p90
  FROM final_attempts
  WHERE attemptElapsedTimeSeconds IS NOT NULL AND attemptElapsedTimeSeconds >= 0
),
duration_summary AS (
  SELECT ANY_VALUE(p25) AS p25, ANY_VALUE(p50) AS p50, ANY_VALUE(p75) AS p75, ANY_VALUE(p90) AS p90
  FROM duration_rows
),
attempt_summary AS (
  SELECT
    COUNT(*) AS final_attempts,
    COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) AS unique_players,
    COUNTIF(gameplayOutcome = 'Clear') AS clears,
    COUNTIF(gameplayOutcome = 'Dead') AS deaths,
    COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
    COUNTIF(gameplayOutcome IS NULL OR gameplayOutcome NOT IN ('Clear', 'Dead', 'Abandon')) AS unrecognized_outcomes,
    COUNTIF(attemptElapsedTimeSeconds IS NOT NULL AND attemptElapsedTimeSeconds >= 0) AS valid_survival_rows,
    COUNTIF(attemptElapsedTimeSeconds IS NULL OR attemptElapsedTimeSeconds < 0) AS invalid_survival_rows,
    COUNTIF(releaseIdentityResolved IS NOT TRUE OR contentVersion = 0 OR NULLIF(TRIM(releaseId), '') IS NULL) AS unresolved_release_rows
  FROM final_attempts
),
detail_summary AS (
  SELECT
    COUNTIF(scope_matches AND telemetryComplete IS TRUE) AS eligible_runs,
    COUNT(DISTINCT IF(scope_matches AND telemetryComplete IS TRUE, attemptId, NULL)) AS distinct_attempts,
    COUNTIF(NOT scope_matches) AS mixed_content_detail_rows,
    COUNTIF(scope_matches AND telemetryComplete IS FALSE) AS excluded_incomplete_detail_rows,
    COUNTIF(scope_matches AND telemetryComplete IS NULL) AS unassessed_legacy_detail_rows,
    COUNTIF(scope_matches AND telemetryComplete IS NOT NULL) AS assessed_detail_rows,
    COUNTIF(scope_matches AND telemetryComplete IS TRUE) AS complete_detail_rows
  FROM classified_segments
),
partial_summary AS (
  SELECT COUNTIF(eligible_runs > 0 AND eligible_runs < candidate_runs) AS partially_covered_attempts
  FROM attempt_coverage
)
SELECT
  attempt_summary.*,
  SAFE_DIVIDE(clears, clears + deaths) AS clear_rate,
  duration_summary.p25 AS survival_p25,
  duration_summary.p50 AS survival_p50,
  duration_summary.p75 AS survival_p75,
  duration_summary.p90 AS survival_p90,
  detail_summary.*,
  partial_summary.partially_covered_attempts,
  SAFE_DIVIDE(complete_detail_rows, assessed_detail_rows) AS telemetry_complete_rate,
  SAFE_DIVIDE(eligible_runs, (SELECT COUNT(*) FROM classified_segments)) AS detail_coverage_rate
FROM attempt_summary
CROSS JOIN duration_summary
CROSS JOIN detail_summary
CROSS JOIN partial_summary
