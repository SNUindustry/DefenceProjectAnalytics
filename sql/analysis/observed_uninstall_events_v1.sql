WITH
ga_source AS (
  -- @include ga_source_union_v1
),
app_remove_physical AS (
  SELECT
    sourceTableDate,
    sourceTableKind,
    sourceFinalizationState,
    event_date,
    event_timestamp,
    event_name,
    event_previous_timestamp,
    event_bundle_sequence_id,
    event_server_timestamp_offset,
    event_original_occurrence_timestamp,
    batch_event_index,
    batch_ordering_id,
    user_id,
    user_pseudo_id,
    stream_id,
    platform,
    app_info.version AS gaAppVersion,
    event_params
  FROM ga_source
  WHERE event_name = 'app_remove'
    AND (
      event_timestamp IS NULL
      OR (
        event_timestamp >= UNIX_MICROS(@observation_start_utc)
        AND event_timestamp < UNIX_MICROS(@analysis_as_of_utc)
      )
    )
),
keyed AS (
  SELECT *,
    TO_JSON_STRING(STRUCT(
      event_timestamp,
      event_name,
      COALESCE(user_pseudo_id, '') AS userPseudoId,
      COALESCE(event_bundle_sequence_id, -1) AS eventBundleSequenceId,
      COALESCE(batch_event_index, -1) AS batchEventIndex,
      COALESCE(batch_ordering_id, -1) AS batchOrderingId
    )) AS gaEventKey,
    TO_JSON_STRING(STRUCT(
      event_timestamp,
      event_date,
      event_previous_timestamp,
      event_bundle_sequence_id,
      event_server_timestamp_offset,
      event_original_occurrence_timestamp,
      batch_event_index,
      batch_ordering_id,
      user_id,
      user_pseudo_id,
      stream_id,
      platform,
      gaAppVersion,
      event_params
    )) AS payloadJson
  FROM app_remove_physical
),
event_groups AS (
  SELECT
    gaEventKey,
    COUNT(*) AS physicalCount,
    COUNT(DISTINCT payloadJson) AS variantCount
  FROM keyed
  GROUP BY gaEventKey
),
one_event AS (
  SELECT * EXCEPT(payloadJson)
  FROM keyed
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY gaEventKey
    ORDER BY sourceTableDate, sourceTableKind, payloadJson
  ) = 1
),
normalized AS (
  SELECT one_event.*, event_groups.physicalCount, event_groups.variantCount
  FROM one_event
  JOIN event_groups USING (gaEventKey)
)
SELECT
  @ga_project AS gaProject,
  @ga_property_id AS gaPropertyId,
  @ga_stream_id AS gaStreamId,
  @telemetry_backend AS telemetryBackend,
  @environment AS environment,
  CASE
    WHEN event_timestamp BETWEEN 1 AND 253402300799999999
      THEN TIMESTAMP_MICROS(event_timestamp)
    ELSE NULL
  END AS eventTimestampUtc,
  user_pseudo_id AS userPseudoId,
  IF(variantCount = 1, user_id, NULL) AS gaUserId,
  CASE
    WHEN variantCount > 1 THEN 'GaDuplicateConflict'
    WHEN event_timestamp IS NULL
      OR event_timestamp <= 0
      OR stream_id != @ga_stream_id
      OR platform != 'ANDROID'
      THEN 'InvalidGaEvent'
    WHEN NULLIF(TRIM(user_pseudo_id), '') IS NULL
      THEN 'UnmappedMissingPseudoId'
    WHEN user_pseudo_id != TRIM(user_pseudo_id)
      OR LENGTH(user_pseudo_id) > 256
      THEN 'InvalidGaEvent'
    ELSE 'Valid'
  END AS rawEventStatus,
  sourceTableDate,
  sourceTableKind,
  sourceFinalizationState,
  event_bundle_sequence_id AS eventBundleSequenceId,
  batch_event_index AS batchEventIndex,
  batch_ordering_id AS batchOrderingId,
  physicalCount,
  variantCount,
  physicalCount - variantCount AS exactDuplicateRowsRemoved
FROM normalized
ORDER BY eventTimestampUtc, userPseudoId, eventBundleSequenceId,
  batchEventIndex, batchOrderingId
