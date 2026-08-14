CREATE OR REPLACE VIEW `<firebase-project-id>.game_telemetry.telemetry_upload_status_v1` AS
WITH aggregate AS (
SELECT
  environment,
  runId,
  uploadId,
  MAX(chunkCount) AS expectedChunkCount,
  COUNT(DISTINCT chunkIndex) AS receivedChunkCount,
  COUNT(DISTINCT IF(chunkKind = 'Core', chunkIndex, NULL)) = 1
    AND MIN(IF(chunkKind = 'Core', chunkIndex, NULL)) = 0 AS coreReceived,
  COUNT(DISTINCT chunkCount) = 1
    AND COUNT(DISTINCT transportVersion) = 1
    AND COUNT(DISTINCT CONCAT(CAST(chunkIndex AS STRING), ':', chunkKind)) = COUNT(DISTINCT chunkIndex)
    AND MIN(chunkIndex) = 0
    AND MAX(chunkIndex) = MAX(chunkCount) - 1 AS metadataConsistent,
  MIN(archivedAtUtc) AS firstReceivedAtUtc,
  MAX(mappedAtUtc) AS lastMappedAtUtc
FROM `<firebase-project-id>.game_telemetry.telemetry_upload_chunks`
GROUP BY environment, runId, uploadId
)
SELECT
  environment,
  runId,
  uploadId,
  expectedChunkCount,
  receivedChunkCount,
  coreReceived,
  metadataConsistent,
  firstReceivedAtUtc,
  IF(receivedChunkCount = expectedChunkCount AND coreReceived AND metadataConsistent,
    lastMappedAtUtc, NULL) AS completedAtUtc,
  receivedChunkCount = expectedChunkCount
    AND coreReceived
    AND metadataConsistent AS telemetryComplete
FROM aggregate;
