-- Aggregate-only cohort and release identity profile for B-6 comparison metadata.
WITH requested_sides AS (
  SELECT 'baseline' AS side, @baseline_content_version AS contentVersion
  UNION ALL
  SELECT 'candidate', @candidate_content_version
),
requested_domains AS (
  SELECT 'stageDifficulty' AS domain, 'finalAttempt' AS populationType UNION ALL
  SELECT 'weaponPerformance', 'finalAttempt' UNION ALL
  SELECT 'upgradeChoice', 'finalAttempt' UNION ALL
  SELECT 'progressionNextRun', 'progressionEvent' UNION ALL
  SELECT 'postRunBehavior', 'finalAttempt'
),
final_attempts AS (
  SELECT
    r.side,
    f.contentVersion,
    f.segmentEndedAtUtc AS observedAtUtc,
    f.appVersion,
    f.releaseId,
    f.releaseChannel,
    f.releaseType,
    f.catalogHash,
    f.releaseIdentityResolved
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1` AS f
  JOIN requested_sides AS r USING (contentVersion)
  WHERE f.environment = @environment
    AND (@stage_key IS NULL OR f.stageKey = @stage_key)
    AND (@app_version IS NULL OR f.appVersion = @app_version)
    AND (@release_channel IS NULL OR f.releaseChannel = @release_channel)
    AND (@release_type IS NULL OR f.releaseType = @release_type)
    AND (@is_development_build IS NULL OR f.isDevelopmentBuild = @is_development_build)
    AND (@uploaded_start_utc IS NULL OR f.uploadedAtUtc >= @uploaded_start_utc)
    AND f.uploadedAtUtc < @uploaded_end_utc
),
progression_events AS (
  SELECT
    r.side,
    p.contentVersion,
    p.occurredAtUtc AS observedAtUtc,
    p.appVersion,
    p.releaseId,
    p.releaseChannel,
    p.releaseType,
    p.catalogHash,
    p.releaseIdentityResolved
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_progression_events` AS p
  JOIN requested_sides AS r USING (contentVersion)
  WHERE p.environment = @environment
    AND (@app_version IS NULL OR p.appVersion = @app_version)
    AND (@release_channel IS NULL OR p.releaseChannel = @release_channel)
    AND (@release_type IS NULL OR p.releaseType = @release_type)
    AND (@is_development_build IS NULL OR p.isDevelopmentBuild = @is_development_build)
    AND (@uploaded_start_utc IS NULL OR p.uploadedAtUtc >= @uploaded_start_utc)
    AND p.uploadedAtUtc < @uploaded_end_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY p.environment, p.eventId
    ORDER BY p.uploadedAtUtc DESC, p.batchId DESC, p.rowIndex DESC
  ) = 1
),
population_rows AS (
  SELECT side, 'finalAttempt' AS populationType, observedAtUtc, appVersion, releaseId,
         releaseChannel, releaseType, catalogHash, releaseIdentityResolved
  FROM final_attempts
  UNION ALL
  SELECT side, 'progressionEvent', observedAtUtc, appVersion, releaseId,
         releaseChannel, releaseType, catalogHash, releaseIdentityResolved
  FROM progression_events
),
aggregated AS (
  SELECT
    side,
    populationType,
    COUNT(*) AS observedRowCount,
    MIN(observedAtUtc) AS observedAtUtcMin,
    MAX(observedAtUtc) AS observedAtUtcMax,
    ARRAY_AGG(DISTINCT appVersion IGNORE NULLS ORDER BY appVersion) AS appVersions,
    ARRAY_AGG(DISTINCT releaseId IGNORE NULLS ORDER BY releaseId) AS releaseIds,
    ARRAY_AGG(DISTINCT releaseChannel IGNORE NULLS ORDER BY releaseChannel) AS releaseChannels,
    ARRAY_AGG(DISTINCT releaseType IGNORE NULLS ORDER BY releaseType) AS releaseTypes,
    ARRAY_AGG(DISTINCT catalogHash IGNORE NULLS ORDER BY catalogHash) AS catalogHashes,
    COUNTIF(releaseIdentityResolved IS NOT TRUE) AS unresolvedReleaseRows
  FROM population_rows
  GROUP BY side, populationType
)
SELECT
  s.side,
  d.domain,
  COALESCE(a.observedRowCount, 0) AS observedRowCount,
  a.observedAtUtcMin,
  a.observedAtUtcMax,
  COALESCE(a.appVersions, ARRAY<STRING>[]) AS appVersions,
  COALESCE(a.releaseIds, ARRAY<STRING>[]) AS releaseIds,
  COALESCE(a.releaseChannels, ARRAY<STRING>[]) AS releaseChannels,
  COALESCE(a.releaseTypes, ARRAY<STRING>[]) AS releaseTypes,
  COALESCE(a.catalogHashes, ARRAY<STRING>[]) AS catalogHashes,
  COALESCE(a.unresolvedReleaseRows, 0) AS unresolvedReleaseRows
FROM requested_sides AS s
CROSS JOIN requested_domains AS d
LEFT JOIN aggregated AS a
  ON a.side = s.side
 AND a.populationType = d.populationType
ORDER BY s.side, d.domain
