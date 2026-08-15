-- Canonical unordered co-exposure pairs; multi-candidate screens remain explicit.
WITH
-- @include upgrade_population_ctes_v1
, identified AS (
  SELECT DISTINCT environment, runId, exposureId, candidateKey, selectedCandidateKey, selectionCount
  FROM complete_exposure_candidates WHERE candidateKey IS NOT NULL
),
pairs AS (
  SELECT left_side.environment, left_side.runId, left_side.exposureId,
    left_side.candidateKey AS candidateA, right_side.candidateKey AS candidateB,
    left_side.selectedCandidateKey, left_side.selectionCount
  FROM identified AS left_side
  JOIN identified AS right_side
    ON right_side.environment = left_side.environment
   AND right_side.runId = left_side.runId
   AND right_side.exposureId = left_side.exposureId
   AND left_side.candidateKey < right_side.candidateKey
)
SELECT candidateA, candidateB,
  COUNT(*) AS coExposureCount,
  COUNTIF(selectedCandidateKey = candidateA) AS aSelectedCount,
  COUNTIF(selectedCandidateKey = candidateB) AS bSelectedCount,
  COUNTIF(selectedCandidateKey IS NOT NULL AND selectedCandidateKey NOT IN (candidateA, candidateB))
    AS otherSelectedCount,
  COUNTIF(selectionCount = 0 OR selectedCandidateKey IS NULL) AS unresolvedSelectionCount,
  COUNTIF(selectedCandidateKey = candidateA) AS aOverallSelectionShareCount,
  COUNT(*) AS aOverallSelectionShareDenominator,
  SAFE_DIVIDE(COUNTIF(selectedCandidateKey = candidateA), COUNT(*)) AS aOverallSelectionShare,
  COUNTIF(selectedCandidateKey = candidateB) AS bOverallSelectionShareCount,
  COUNT(*) AS bOverallSelectionShareDenominator,
  SAFE_DIVIDE(COUNTIF(selectedCandidateKey = candidateB), COUNT(*)) AS bOverallSelectionShare,
  COUNTIF(selectedCandidateKey = candidateA) AS aConditionalPreferenceCount,
  COUNTIF(selectedCandidateKey IN (candidateA, candidateB)) AS aConditionalPreferenceDenominator,
  SAFE_DIVIDE(COUNTIF(selectedCandidateKey = candidateA),
    COUNTIF(selectedCandidateKey IN (candidateA, candidateB))) AS aConditionalPreference,
  COUNTIF(selectedCandidateKey = candidateB) AS bConditionalPreferenceCount,
  COUNTIF(selectedCandidateKey IN (candidateA, candidateB)) AS bConditionalPreferenceDenominator,
  SAFE_DIVIDE(COUNTIF(selectedCandidateKey = candidateB),
    COUNTIF(selectedCandidateKey IN (candidateA, candidateB))) AS bConditionalPreference
FROM pairs
GROUP BY candidateA, candidateB
ORDER BY candidateA, candidateB
