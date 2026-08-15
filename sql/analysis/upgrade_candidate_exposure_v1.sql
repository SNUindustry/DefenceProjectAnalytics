-- Candidate exposure aggregates. Full breakdown is aggregate-only.
WITH
-- @include upgrade_population_ctes_v1
, observed_candidates AS (
  SELECT candidateKey,
    ANY_VALUE(category) AS categoryCode, ANY_VALUE(categoryLabel) AS category,
    ANY_VALUE(upgradeId) AS contentId, ANY_VALUE(weaponFamilyId) AS weaponFamilyId,
    ANY_VALUE(grantWeaponId) AS grantWeaponId, ANY_VALUE(cardType) AS cardType,
    ANY_VALUE(weaponSourceType) AS weaponSourceType,
    COUNT(DISTINCT CONCAT(environment, '|', runId, '|', exposureId)) AS allObservedExposureCount
  FROM exposure_candidate_rows
  WHERE candidateKey IS NOT NULL AND NULLIF(TRIM(exposureId), '') IS NOT NULL
  GROUP BY candidateKey
),
complete_enriched AS (
  SELECT candidateKey, environment, runId, exposureId, selectedCandidateKey,
    exposureElapsed,
    PERCENTILE_CONT(IF(exposureElapsed >= 0, exposureElapsed, NULL), 0.25)
      OVER (PARTITION BY candidateKey) AS exposureElapsedP25,
    PERCENTILE_CONT(IF(exposureElapsed >= 0, exposureElapsed, NULL), 0.50)
      OVER (PARTITION BY candidateKey) AS exposureElapsedMedian,
    PERCENTILE_CONT(IF(exposureElapsed >= 0, exposureElapsed, NULL), 0.75)
      OVER (PARTITION BY candidateKey) AS exposureElapsedP75
  FROM complete_exposure_candidates
  WHERE candidateKey IS NOT NULL
),
complete_stats AS (
  SELECT candidateKey,
    COUNT(DISTINCT CONCAT(environment, '|', runId, '|', exposureId)) AS completeExposureCount,
    COUNTIF(selectedCandidateKey = candidateKey) AS linkedSelectionCount,
    ANY_VALUE(exposureElapsedP25) AS exposureElapsedP25,
    ANY_VALUE(exposureElapsedMedian) AS exposureElapsedMedian,
    ANY_VALUE(exposureElapsedP75) AS exposureElapsedP75
  FROM complete_enriched GROUP BY candidateKey
),
all_selection_stats AS (
  SELECT candidateKey, COUNT(*) AS selectionCountIncludingLegacy
  FROM selection_rows WHERE candidateKey IS NOT NULL GROUP BY candidateKey
)
SELECT observed.candidateKey, observed.categoryCode, observed.category, observed.contentId,
  observed.weaponFamilyId, observed.grantWeaponId, observed.cardType, observed.weaponSourceType,
  observed.allObservedExposureCount,
  COALESCE(complete.completeExposureCount, 0) AS completeExposureCount,
  COALESCE(complete.linkedSelectionCount, 0) AS linkedSelectionCount,
  COALESCE(selections.selectionCountIncludingLegacy, 0) AS selectionCountIncludingLegacy,
  COALESCE(complete.linkedSelectionCount, 0) AS pickRateCount,
  COALESCE(complete.completeExposureCount, 0) AS pickRateDenominator,
  SAFE_DIVIDE(complete.linkedSelectionCount, complete.completeExposureCount) AS pickRate,
  complete.exposureElapsedP25, complete.exposureElapsedMedian, complete.exposureElapsedP75
FROM observed_candidates AS observed
LEFT JOIN complete_stats AS complete USING (candidateKey)
LEFT JOIN all_selection_stats AS selections USING (candidateKey)
ORDER BY candidateKey
