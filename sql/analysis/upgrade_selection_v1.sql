-- Candidate selection, repeated exposure, position, and exposure-size aggregates.
WITH
-- @include upgrade_population_ctes_v1
, candidate_attempts AS (
  SELECT candidateKey, attemptId,
    COUNT(DISTINCT exposureId) AS exposureCount,
    COUNTIF(selectedCandidateKey = candidateKey) AS selectionCount
  FROM complete_exposure_candidates
  WHERE candidateKey IS NOT NULL
  GROUP BY candidateKey, attemptId
),
candidate_stats AS (
  SELECT candidateKey,
    COUNT(*) AS exposedAttempts,
    COUNTIF(selectionCount > 0) AS selectedAttempts,
    COUNTIF(exposureCount > 1) AS repeatedExposureAttempts,
    COUNTIF(selectionCount > 1) AS repeatedSelectionAttempts
  FROM candidate_attempts GROUP BY candidateKey
),
candidate_exposures AS (
  SELECT candidateKey,
    COUNT(DISTINCT CONCAT(environment, '|', runId, '|', exposureId)) AS completeExposureCount,
    COUNTIF(selectedCandidateKey = candidateKey) AS linkedSelectionCount
  FROM complete_exposure_candidates
  WHERE candidateKey IS NOT NULL GROUP BY candidateKey
),
candidate_rows AS (
  SELECT 'candidate' AS rowType, stats.candidateKey,
    CAST(NULL AS STRING) AS dimension, CAST(NULL AS STRING) AS dimensionValue,
    exposures.completeExposureCount AS exposureCount,
    exposures.linkedSelectionCount AS selectionCount,
    exposures.linkedSelectionCount AS pickRateCount,
    exposures.completeExposureCount AS pickRateDenominator,
    SAFE_DIVIDE(exposures.linkedSelectionCount, exposures.completeExposureCount) AS pickRate,
    stats.exposedAttempts, stats.selectedAttempts,
    stats.repeatedExposureAttempts, stats.repeatedSelectionAttempts
  FROM candidate_stats AS stats JOIN candidate_exposures AS exposures USING (candidateKey)
),
breakdown_source AS (
  SELECT candidateKey, selectedCandidateKey,
    CAST(candidateIndex AS STRING) AS candidateIndex,
    CAST(presentedCandidateCount AS STRING) AS presentedCandidateCount
  FROM complete_exposure_candidates WHERE candidateKey IS NOT NULL
),
breakdown_rows AS (
  SELECT 'breakdown' AS rowType, candidateKey, dimension, dimensionValue,
    COUNT(*) AS exposureCount,
    COUNTIF(selectedCandidateKey = candidateKey) AS selectionCount,
    COUNTIF(selectedCandidateKey = candidateKey) AS pickRateCount,
    COUNT(*) AS pickRateDenominator,
    SAFE_DIVIDE(COUNTIF(selectedCandidateKey = candidateKey), COUNT(*)) AS pickRate,
    CAST(NULL AS INT64) AS exposedAttempts, CAST(NULL AS INT64) AS selectedAttempts,
    CAST(NULL AS INT64) AS repeatedExposureAttempts,
    CAST(NULL AS INT64) AS repeatedSelectionAttempts
  FROM breakdown_source
  UNPIVOT(dimensionValue FOR dimension IN (candidateIndex, presentedCandidateCount))
  GROUP BY candidateKey, dimension, dimensionValue
)
SELECT rowType, candidateKey, dimension, dimensionValue, exposureCount, selectionCount,
  pickRateCount, pickRateDenominator, pickRate, exposedAttempts, selectedAttempts,
  repeatedExposureAttempts, repeatedSelectionAttempts
FROM candidate_rows
UNION ALL
SELECT rowType, candidateKey, dimension, dimensionValue, exposureCount, selectionCount,
  pickRateCount, pickRateDenominator, pickRate, exposedAttempts, selectedAttempts,
  repeatedExposureAttempts, repeatedSelectionAttempts
FROM breakdown_rows
ORDER BY rowType, candidateKey, dimension, dimensionValue
