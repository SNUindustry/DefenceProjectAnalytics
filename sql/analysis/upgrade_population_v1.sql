-- Upgrade Choice population and quality summary.
WITH
-- @include upgrade_population_ctes_v1
, attempt_summary AS (
  SELECT
    COUNT(*) AS finalAttempts,
    COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) AS uniquePlayers,
    COUNTIF(gameplayOutcome = 'Clear') AS clears,
    COUNTIF(gameplayOutcome = 'Dead') AS deaths,
    COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
    COUNTIF(gameplayOutcome IS NULL OR gameplayOutcome NOT IN ('Clear', 'Dead', 'Abandon'))
      AS unrecognizedOutcomes
  FROM final_attempts
),
detail_summary AS (
  SELECT
    COUNT(*) AS detailCandidateSegments,
    COUNTIF(scopeMatches AND telemetryComplete IS NOT NULL) AS assessedSegments,
    COUNTIF(scopeMatches AND completeAsOf) AS transportEligibleSegments,
    COUNTIF(NOT scopeMatches) AS mixedContentSegmentsExcluded,
    COUNTIF(scopeMatches AND telemetryComplete IS NOT NULL AND NOT completeAsOf)
      AS incompleteSegmentsExcluded,
    COUNTIF(scopeMatches AND telemetryComplete IS NULL) AS legacyUnassessedSegments
  FROM classified_segments
),
choice_summary AS (
  SELECT
    COUNTIF(choiceEligible) AS choiceEligibleSegments,
    COUNTIF(selectionCountAssessed AND NOT selectionCountMatches) AS selectionCountMismatchSegments,
    SUM(noSelectionExposures) AS noSelectionExposures,
    SUM(multipleSelectionExposures) AS multipleSelectionExposures,
    SUM(legacyUnlinkedSelections) AS legacyUnlinkedSelections,
    SUM(selectionsWithoutExposureMatch) AS selectionsWithoutExposureMatch,
    SUM(COALESCE(upgradeExposureOverflowCount, 0)) AS droppedExposures,
    SUM(COALESCE(upgradeCandidateOverflowCount, 0)) AS omittedCandidates
  FROM choice_segment_assessment
),
attempt_coverage_summary AS (
  SELECT
    COUNT(*) AS detailCandidateAttempts,
    COUNTIF(candidateSegments > 0 AND candidateSegments = choiceEligibleSegments)
      AS fullyChoiceCoveredAttempts,
    COUNTIF(choiceEligibleSegments > 0 AND choiceEligibleSegments < candidateSegments)
      AS partiallyCoveredAttempts
  FROM attempt_choice_coverage
),
exposure_summary AS (
  SELECT
    COUNT(*) AS observedExposures,
    COUNTIF(isTruncated) AS truncatedExposures,
    COUNTIF(isMalformed) AS malformedExposures
  FROM exposure_rollup
),
complete_summary AS (SELECT COUNT(*) AS completeExposures FROM complete_exposures),
selection_summary AS (
  SELECT
    COUNTIF(candidateMatch AND NULLIF(TRIM(exposureId), '') IS NOT NULL) AS linkedSelections
  FROM selection_validation
),
identity_summary AS (
  SELECT
    COUNTIF(candidateKey IS NULL) AS missingCandidateIdentityRows,
    COUNTIF(category IS NOT NULL AND category NOT IN (0, 1, 2, 3, 4)) AS unrecognizedCategoryRows,
    COUNT(DISTINCT candidateKey) AS candidateCount
  FROM exposure_candidate_rows
)
SELECT attempt_summary.finalAttempts, attempt_summary.uniquePlayers,
  attempt_summary.clears, attempt_summary.deaths, attempt_summary.abandons,
  attempt_summary.unrecognizedOutcomes,
  detail_summary.detailCandidateSegments, detail_summary.assessedSegments,
  detail_summary.transportEligibleSegments, detail_summary.mixedContentSegmentsExcluded,
  detail_summary.incompleteSegmentsExcluded, detail_summary.legacyUnassessedSegments,
  choice_summary.choiceEligibleSegments, choice_summary.selectionCountMismatchSegments,
  choice_summary.noSelectionExposures, choice_summary.multipleSelectionExposures,
  choice_summary.legacyUnlinkedSelections, choice_summary.selectionsWithoutExposureMatch,
  choice_summary.droppedExposures, choice_summary.omittedCandidates,
  attempt_coverage_summary.detailCandidateAttempts,
  attempt_coverage_summary.fullyChoiceCoveredAttempts,
  attempt_coverage_summary.partiallyCoveredAttempts,
  exposure_summary.observedExposures, exposure_summary.truncatedExposures,
  exposure_summary.malformedExposures, complete_summary.completeExposures,
  selection_summary.linkedSelections, identity_summary.missingCandidateIdentityRows,
  identity_summary.unrecognizedCategoryRows, identity_summary.candidateCount
FROM attempt_summary
CROSS JOIN detail_summary
CROSS JOIN choice_summary
CROSS JOIN attempt_coverage_summary
CROSS JOIN exposure_summary
CROSS JOIN complete_summary
CROSS JOIN selection_summary
CROSS JOIN identity_summary
