WITH
ga_source AS (
  -- @include ga_source_union_v1
),
ga_foreground_physical AS (
  SELECT
    sourceTableDate,
    sourceTableKind,
    sourceFinalizationState,
    event_timestamp,
    event_name,
    user_id,
    user_pseudo_id,
    stream_id,
    platform,
    app_info.version AS gaAppVersion,
    ARRAY(
      SELECT DISTINCT value.string_value AS item
      FROM UNNEST(event_params)
      WHERE key = 'lifecycle_occurrence_id' AND value.string_value IS NOT NULL
      ORDER BY item
    ) AS occurrenceValues,
    ARRAY(
      SELECT DISTINCT value.string_value AS item
      FROM UNNEST(event_params)
      WHERE key = 'return_kind' AND value.string_value IS NOT NULL
      ORDER BY item
    ) AS returnKindValues,
    ARRAY(
      SELECT DISTINCT value.string_value AS item
      FROM UNNEST(event_params)
      WHERE key = 'telemetry_environment' AND value.string_value IS NOT NULL
      ORDER BY item
    ) AS environmentValues,
    ARRAY(
      SELECT DISTINCT COALESCE(value.string_value, CAST(value.int_value AS STRING)) AS item
      FROM UNNEST(event_params)
      WHERE key = 'content_version'
        AND COALESCE(value.string_value, CAST(value.int_value AS STRING)) IS NOT NULL
      ORDER BY item
    ) AS contentVersionValues,
    ARRAY(
      SELECT DISTINCT value.string_value AS item
      FROM UNNEST(event_params)
      WHERE key = 'release_id' AND value.string_value IS NOT NULL
      ORDER BY item
    ) AS releaseIdValues
  FROM ga_source
  WHERE event_name = 'app_foreground'
    AND event_timestamp >= UNIX_MICROS(@observation_start_utc)
    AND event_timestamp < UNIX_MICROS(@analysis_as_of_utc)
),
ga_extracted AS (
  SELECT *,
    occurrenceValues[SAFE_OFFSET(0)] AS lifecycleOccurrenceId,
    returnKindValues[SAFE_OFFSET(0)] AS gaReturnKind,
    environmentValues[SAFE_OFFSET(0)] AS gaEnvironment,
    SAFE_CAST(contentVersionValues[SAFE_OFFSET(0)] AS INT64) AS gaContentVersion,
    releaseIdValues[SAFE_OFFSET(0)] AS gaReleaseId,
    IFNULL(ARRAY_LENGTH(occurrenceValues), 0) = 1
      AND IFNULL(ARRAY_LENGTH(returnKindValues), 0) = 1
      AND IFNULL(ARRAY_LENGTH(environmentValues), 0) = 1
      AND IFNULL(ARRAY_LENGTH(contentVersionValues), 0) = 1
      AND IFNULL(ARRAY_LENGTH(releaseIdValues), 0) = 1 AS parameterShapeValid
  FROM ga_foreground_physical
),
ga_payloads AS (
  SELECT *,
    TO_JSON_STRING(STRUCT(
      user_id, user_pseudo_id, stream_id, platform, gaAppVersion,
      occurrenceValues, returnKindValues, environmentValues,
      contentVersionValues, releaseIdValues
    )) AS payloadJson,
    TO_JSON_STRING(STRUCT(
      event_timestamp,
      event_name,
      COALESCE(user_pseudo_id, '') AS userPseudoId,
      COALESCE(lifecycleOccurrenceId, '') AS lifecycleOccurrenceId
    )) AS gaEventKey
  FROM ga_extracted
),
ga_groups AS (
  SELECT gaEventKey,
    COUNT(*) AS physicalCount,
    COUNT(DISTINCT payloadJson) AS variantCount
  FROM ga_payloads
  GROUP BY gaEventKey
),
ga_one AS (
  SELECT * EXCEPT(payloadJson)
  FROM ga_payloads
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY gaEventKey
    ORDER BY sourceTableDate, sourceTableKind, payloadJson
  ) = 1
),
ga_normalized AS (
  SELECT g1.*, gg.physicalCount, gg.variantCount
  FROM ga_one AS g1
  JOIN ga_groups AS gg USING (gaEventKey)
),
custom_physical AS (
  SELECT c.*,
    TO_JSON_STRING((SELECT AS STRUCT c.* EXCEPT(uploadedAtUtc))) AS payloadJson
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_app_lifecycle_events` AS c
  WHERE c.environment = @environment
    AND c.uploadedAtUtc < @analysis_as_of_utc
    AND c.occurredAtUtc < @analysis_as_of_utc
),
custom_groups AS (
  SELECT environment, lifecycleOccurrenceId,
    COUNT(*) AS physicalCount,
    COUNT(DISTINCT payloadJson) AS variantCount
  FROM custom_physical
  GROUP BY environment, lifecycleOccurrenceId
),
custom_one AS (
  SELECT * EXCEPT(payloadJson)
  FROM custom_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, lifecycleOccurrenceId
    ORDER BY uploadedAtUtc, payloadJson
  ) = 1
),
custom_normalized AS (
  SELECT c.*, g.physicalCount AS customPhysicalCount,
    g.variantCount AS customVariantCount
  FROM custom_one AS c
  JOIN custom_groups AS g USING (environment, lifecycleOccurrenceId)
),
joined AS (
  SELECT
    ga.*,
    custom.lifecycleOccurrenceId IS NOT NULL AS customPresent,
    custom.telemetryPlayerId,
    custom.returnKind AS customReturnKind,
    custom.occurredAtUtc AS customOccurredAtUtc,
    custom.environment AS customEnvironment,
    custom.contentVersion AS customContentVersion,
    custom.releaseId AS customReleaseId,
    custom.isDevelopmentBuild AS customIsDevelopmentBuild,
    custom.profileAttributionStatus AS customProfileAttributionStatus,
    custom.customPhysicalCount,
    custom.customVariantCount
  FROM ga_normalized AS ga
  LEFT JOIN custom_normalized AS custom
    ON custom.environment = @environment
    AND custom.lifecycleOccurrenceId = ga.lifecycleOccurrenceId
),
base_classified AS (
  SELECT *,
    CASE
      WHEN variantCount > 1 THEN 'GaConflict'
      WHEN NULLIF(TRIM(user_id), '') IS NULL THEN 'UnmappedMissingUserId'
      WHEN NULLIF(TRIM(user_pseudo_id), '') IS NULL THEN 'UnmappedMissingPseudoId'
      WHEN NULLIF(TRIM(lifecycleOccurrenceId), '') IS NULL
        OR NOT parameterShapeValid THEN 'UnmappedMissingOccurrence'
      WHEN NOT REGEXP_CONTAINS(user_id, r'^[a-f0-9]{32}$')
        OR NOT REGEXP_CONTAINS(lifecycleOccurrenceId, r'^[a-f0-9]{32}$')
        THEN 'InvalidIdentifier'
      WHEN stream_id != @ga_stream_id OR platform != 'ANDROID'
        OR gaEnvironment != @environment THEN 'InvalidEnvironment'
      WHEN customPresent AND customVariantCount > 1 THEN 'CustomConflict'
      WHEN NOT customPresent THEN 'UnmatchedGaOccurrence'
      WHEN NOT REGEXP_CONTAINS(COALESCE(telemetryPlayerId, ''), r'^[a-f0-9]{32}$')
        THEN 'InvalidIdentifier'
      WHEN customProfileAttributionStatus != 'CanonicalProfile'
        OR customIsDevelopmentBuild IS NOT FALSE
        OR customEnvironment != @environment
        OR customReturnKind != gaReturnKind
        OR customContentVersion != gaContentVersion
        OR COALESCE(customReleaseId, '') != COALESCE(gaReleaseId, '')
        THEN 'InvalidContext'
      ELSE 'Mapped'
    END AS baseMappingStatus
  FROM joined
),
simultaneous AS (
  SELECT user_pseudo_id, event_timestamp,
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(telemetryPlayerId, user_id)))
      AS distinctProfilePairs
  FROM base_classified
  WHERE baseMappingStatus = 'Mapped'
  GROUP BY user_pseudo_id, event_timestamp
),
durable_conflicts AS (
  SELECT telemetryPlayerId, user_id
  FROM base_classified
  WHERE baseMappingStatus = 'Mapped'
  QUALIFY
    COUNT(DISTINCT telemetryPlayerId) OVER (PARTITION BY user_id) > 1
    OR COUNT(DISTINCT user_id) OVER (PARTITION BY telemetryPlayerId) > 1
),
classified AS (
  SELECT b.* EXCEPT(baseMappingStatus),
    CASE
      WHEN b.baseMappingStatus != 'Mapped' THEN b.baseMappingStatus
      WHEN s.distinctProfilePairs > 1 THEN 'TemporalMappingConflict'
      WHEN d.telemetryPlayerId IS NOT NULL THEN 'DurableIdentityConflict'
      ELSE 'Mapped'
    END AS mappingStatus
  FROM base_classified AS b
  LEFT JOIN simultaneous AS s
    ON s.user_pseudo_id = b.user_pseudo_id
    AND s.event_timestamp = b.event_timestamp
  LEFT JOIN (SELECT DISTINCT telemetryPlayerId, user_id FROM durable_conflicts) AS d
    ON d.telemetryPlayerId = b.telemetryPlayerId AND d.user_id = b.user_id
)
SELECT
  @ga_project AS gaProject,
  @ga_property_id AS gaPropertyId,
  @ga_stream_id AS gaStreamId,
  @telemetry_backend AS telemetryBackend,
  @environment AS environment,
  TIMESTAMP_MICROS(event_timestamp) AS observedAtUtc,
  telemetryPlayerId,
  user_id AS retentionBridgeId,
  user_pseudo_id AS userPseudoId,
  lifecycleOccurrenceId,
  mappingStatus,
  sourceTableDate,
  sourceTableKind,
  sourceFinalizationState,
  gaContentVersion AS contentVersion,
  gaReleaseId AS releaseId,
  customIsDevelopmentBuild AS isDevelopmentBuild,
  physicalCount,
  physicalCount - 1 AS exactDuplicateRowsRemoved
FROM classified
ORDER BY observedAtUtc, userPseudoId, lifecycleOccurrenceId
