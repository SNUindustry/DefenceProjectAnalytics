-- Exposure-time context with a 30-second preceding-snapshot contract.
WITH
-- @include upgrade_population_ctes_v1
, exposure_base AS (
  SELECT DISTINCT environment, runId, exposureId, candidateKey, exposureElapsed,
    selectedCandidateKey, requestSource
  FROM complete_exposure_candidates WHERE candidateKey IS NOT NULL
),
snapshot_match AS (
  SELECT base.environment, base.runId, base.exposureId, base.candidateKey,
    base.exposureElapsed, base.selectedCandidateKey, base.requestSource,
    snapshots.elapsedTime AS snapshotElapsed,
    snapshots.playerLevel, snapshots.phaseIndex AS snapshotPhaseIndex,
    snapshots.floorNumber AS snapshotFloorNumber, snapshots.waveNumber AS snapshotWaveNumber,
    snapshots.bossActive AS snapshotBossActive, snapshots.ownedWeaponCount,
    snapshots.hpRatio, snapshots.zoneIndex, snapshots.zoneType,
    snapshots.upgradeSelectionCount
  FROM exposure_base AS base
  LEFT JOIN snapshot_rows AS snapshots
    ON snapshots.environment = base.environment AND snapshots.runId = base.runId
   AND snapshots.elapsedTime <= base.exposureElapsed
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY base.environment, base.runId, base.exposureId, base.candidateKey
    ORDER BY snapshots.elapsedTime DESC, snapshots.snapshotIndex DESC, snapshots.rowIndex DESC
  ) = 1
),
transition_rows AS (
  SELECT transitions.environment, transitions.runId, transitions.rowIndex,
    transitions.transitionIndex, transitions.transitionType, transitions.phaseIndex,
    transitions.floorNumber, transitions.waveNumber, transitions.elapsedTime,
    transitions.uploadedAtUtc
  FROM choice_eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_transitions` AS transitions
    ON transitions.environment = segments.environment AND transitions.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR transitions.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR transitions.uploadedAtUtc < @uploaded_end_utc)
    AND transitions.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY transitions.environment, transitions.runId, transitions.rowIndex
    ORDER BY transitions.uploadedAtUtc DESC
  ) = 1
),
transition_match AS (
  SELECT base.environment, base.runId, base.exposureId, base.candidateKey,
    transitions.phaseIndex, transitions.floorNumber, transitions.waveNumber,
    transitions.elapsedTime AS transitionElapsed
  FROM exposure_base AS base
  LEFT JOIN transition_rows AS transitions
    ON transitions.environment = base.environment AND transitions.runId = base.runId
   AND transitions.elapsedTime <= base.exposureElapsed
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY base.environment, base.runId, base.exposureId, base.candidateKey
    ORDER BY transitions.elapsedTime DESC, transitions.transitionIndex DESC, transitions.rowIndex DESC
  ) = 1
),
boss_transition_match AS (
  SELECT base.environment, base.runId, base.exposureId, base.candidateKey,
    transitions.transitionType AS bossTransitionType
  FROM exposure_base AS base
  LEFT JOIN transition_rows AS transitions
    ON transitions.environment = base.environment AND transitions.runId = base.runId
   AND transitions.elapsedTime <= base.exposureElapsed
   AND transitions.transitionType IN ('BossStarted', 'BossResumedActive', 'BossEnded')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY base.environment, base.runId, base.exposureId, base.candidateKey
    ORDER BY transitions.elapsedTime DESC, transitions.transitionIndex DESC, transitions.rowIndex DESC
  ) = 1
),
context_values AS (
  SELECT snapshots.environment, snapshots.runId, snapshots.exposureId,
    snapshots.candidateKey, snapshots.exposureElapsed, snapshots.selectedCandidateKey,
    snapshots.requestSource, snapshots.snapshotElapsed, snapshots.playerLevel,
    snapshots.snapshotPhaseIndex, snapshots.snapshotFloorNumber,
    snapshots.snapshotWaveNumber, snapshots.snapshotBossActive,
    snapshots.ownedWeaponCount, snapshots.hpRatio, snapshots.zoneIndex,
    snapshots.zoneType, snapshots.upgradeSelectionCount,
    snapshots.exposureElapsed - snapshots.snapshotElapsed AS snapshotLagSeconds,
    snapshots.snapshotElapsed IS NOT NULL
      AND snapshots.exposureElapsed - snapshots.snapshotElapsed BETWEEN 0 AND @max_context_snapshot_lag_seconds
      AS snapshotFresh,
    transition_match.phaseIndex AS transitionPhaseIndex,
    transition_match.floorNumber AS transitionFloorNumber,
    transition_match.waveNumber AS transitionWaveNumber,
    boss_transition_match.bossTransitionType
  FROM snapshot_match AS snapshots
  LEFT JOIN transition_match
    USING (environment, runId, exposureId, candidateKey)
  LEFT JOIN boss_transition_match
    USING (environment, runId, exposureId, candidateKey)
),
dimension_rows AS (
  SELECT candidateKey, selectedCandidateKey = candidateKey AS selected,
    dimension.dimension, dimension.dimensionValue, dimension.attributionSource
  FROM context_values,
  UNNEST([
    STRUCT('playerLevel' AS dimension,
      IF(snapshotFresh, CAST(playerLevel AS STRING), NULL) AS dimensionValue,
      IF(snapshotFresh, 'snapshot', 'missing') AS attributionSource),
    STRUCT('phaseIndex',
      CAST(IF(snapshotFresh, snapshotPhaseIndex, transitionPhaseIndex) AS STRING),
      CASE WHEN snapshotFresh AND snapshotPhaseIndex IS NOT NULL THEN 'snapshot'
        WHEN transitionPhaseIndex IS NOT NULL THEN 'transition' ELSE 'missing' END),
    STRUCT('floorNumber',
      CAST(IF(snapshotFresh, snapshotFloorNumber, transitionFloorNumber) AS STRING),
      CASE WHEN snapshotFresh AND snapshotFloorNumber IS NOT NULL THEN 'snapshot'
        WHEN transitionFloorNumber IS NOT NULL THEN 'transition' ELSE 'missing' END),
    STRUCT('waveNumber',
      CAST(IF(snapshotFresh, snapshotWaveNumber, transitionWaveNumber) AS STRING),
      CASE WHEN snapshotFresh AND snapshotWaveNumber IS NOT NULL THEN 'snapshot'
        WHEN transitionWaveNumber IS NOT NULL THEN 'transition' ELSE 'missing' END),
    STRUCT('bossActive',
      CAST(CASE WHEN snapshotFresh THEN snapshotBossActive
        WHEN bossTransitionType IN ('BossStarted', 'BossResumedActive') THEN TRUE
        WHEN bossTransitionType = 'BossEnded' THEN FALSE ELSE NULL END AS STRING),
      CASE WHEN snapshotFresh AND snapshotBossActive IS NOT NULL THEN 'snapshot'
        WHEN bossTransitionType IS NOT NULL THEN 'transition' ELSE 'missing' END),
    STRUCT('ownedWeaponCount',
      IF(snapshotFresh, CAST(ownedWeaponCount AS STRING), NULL),
      IF(snapshotFresh, 'snapshot', 'missing')),
    STRUCT('elapsedTimeBucket',
      CASE WHEN exposureElapsed IS NULL OR exposureElapsed < 0 THEN NULL
        WHEN exposureElapsed < 30 THEN '0-30s' WHEN exposureElapsed < 60 THEN '30-60s'
        WHEN exposureElapsed < 120 THEN '1-2m' WHEN exposureElapsed < 300 THEN '2-5m'
        WHEN exposureElapsed < 600 THEN '5-10m' ELSE '10m+' END,
      'exact')
  ]) AS dimension
),
context_aggregate AS (
  SELECT 'context' AS rowType, candidateKey, dimension, dimensionValue, attributionSource,
    COUNT(*) AS exposureCount, COUNTIF(selected) AS selectionCount,
    COUNTIF(selected) AS pickRateCount, COUNT(*) AS pickRateDenominator,
    SAFE_DIVIDE(COUNTIF(selected), COUNT(*)) AS pickRate,
    CAST(NULL AS INT64) AS approximateContextRows,
    CAST(NULL AS INT64) AS staleContextRows,
    CAST(NULL AS INT64) AS missingContextRows,
    CAST(NULL AS FLOAT64) AS snapshotLagP50,
    CAST(NULL AS FLOAT64) AS snapshotLagP75,
    CAST(NULL AS FLOAT64) AS snapshotLagP90
  FROM dimension_rows
  WHERE dimensionValue IS NOT NULL
  GROUP BY candidateKey, dimension, dimensionValue, attributionSource
),
lag_enriched AS (
  SELECT environment, runId, exposureId, candidateKey, exposureElapsed,
    selectedCandidateKey, requestSource, snapshotElapsed, playerLevel,
    snapshotPhaseIndex, snapshotFloorNumber, snapshotWaveNumber,
    snapshotBossActive, ownedWeaponCount, hpRatio, zoneIndex, zoneType,
    upgradeSelectionCount, snapshotLagSeconds, snapshotFresh,
    transitionPhaseIndex, transitionFloorNumber, transitionWaveNumber,
    bossTransitionType,
    PERCENTILE_CONT(IF(snapshotLagSeconds >= 0, snapshotLagSeconds, NULL), 0.50) OVER () AS p50,
    PERCENTILE_CONT(IF(snapshotLagSeconds >= 0, snapshotLagSeconds, NULL), 0.75) OVER () AS p75,
    PERCENTILE_CONT(IF(snapshotLagSeconds >= 0, snapshotLagSeconds, NULL), 0.90) OVER () AS p90
  FROM context_values
),
quality_row AS (
  SELECT 'quality' AS rowType, CAST(NULL AS STRING) AS candidateKey,
    CAST(NULL AS STRING) AS dimension, CAST(NULL AS STRING) AS dimensionValue,
    CAST(NULL AS STRING) AS attributionSource,
    CAST(NULL AS INT64) AS exposureCount, CAST(NULL AS INT64) AS selectionCount,
    CAST(NULL AS INT64) AS pickRateCount, CAST(NULL AS INT64) AS pickRateDenominator,
    CAST(NULL AS FLOAT64) AS pickRate,
    COUNTIF(snapshotFresh OR transitionPhaseIndex IS NOT NULL OR bossTransitionType IS NOT NULL)
      AS approximateContextRows,
    COUNTIF(snapshotElapsed IS NOT NULL AND NOT snapshotFresh) AS staleContextRows,
    COUNTIF(NOT snapshotFresh AND transitionPhaseIndex IS NULL AND bossTransitionType IS NULL)
      AS missingContextRows,
    ANY_VALUE(p50) AS snapshotLagP50, ANY_VALUE(p75) AS snapshotLagP75,
    ANY_VALUE(p90) AS snapshotLagP90
  FROM lag_enriched
)
SELECT rowType, candidateKey, dimension, dimensionValue, attributionSource,
  exposureCount, selectionCount, pickRateCount, pickRateDenominator, pickRate,
  approximateContextRows, staleContextRows, missingContextRows,
  snapshotLagP50, snapshotLagP75, snapshotLagP90
FROM context_aggregate
UNION ALL
SELECT rowType, candidateKey, dimension, dimensionValue, attributionSource,
  exposureCount, selectionCount, pickRateCount, pickRateDenominator, pickRate,
  approximateContextRows, staleContextRows, missingContextRows,
  snapshotLagP50, snapshotLagP75, snapshotLagP90
FROM quality_row
ORDER BY rowType, candidateKey, dimension, dimensionValue
