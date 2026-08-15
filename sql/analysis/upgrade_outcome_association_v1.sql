-- Observational selected versus exposed-not-selected final-attempt associations.
WITH
-- @include upgrade_population_ctes_v1
, eligible_candidate_events AS (
  SELECT DISTINCT candidates.environment, candidates.telemetryPlayerId, candidates.attemptId,
    candidates.exposureId, candidates.candidateKey, candidates.exposureElapsed,
    candidates.selectedCandidateKey, candidates.selectedSelectionElapsed,
    candidates.selectionCount
  FROM complete_exposure_candidates AS candidates
  JOIN fully_choice_covered_attempts AS eligible
    ON eligible.environment = candidates.environment
   AND eligible.attemptId = candidates.attemptId
   AND eligible.telemetryPlayerId IS NOT DISTINCT FROM candidates.telemetryPlayerId
  WHERE candidates.candidateKey IS NOT NULL
),
candidate_attempt_events AS (
  SELECT environment, telemetryPlayerId, attemptId, candidateKey,
    MIN(exposureElapsed) AS firstExposureElapsed,
    MIN(IF(selectedCandidateKey = candidateKey, selectedSelectionElapsed, NULL)) AS firstSelectionElapsed,
    COUNTIF(selectedCandidateKey = candidateKey) > 0 AS selected,
    COUNTIF(selectedCandidateKey IS NOT NULL AND selectedCandidateKey != candidateKey) > 0
      AS alternativeSelected
  FROM eligible_candidate_events
  GROUP BY environment, telemetryPlayerId, attemptId, candidateKey
),
cohorts AS (
  SELECT events.candidateKey, events.selected,
    NOT events.selected AND events.alternativeSelected AS alternativeSelected,
    NOT events.selected AND NOT events.alternativeSelected AS noSelectionOnly,
    attempts.gameplayOutcome, attempts.attemptElapsedTimeSeconds,
    attempts.attemptElapsedTimeSeconds - events.firstExposureElapsed AS remainingFromFirstExposure,
    IF(events.selected,
      attempts.attemptElapsedTimeSeconds - events.firstSelectionElapsed, NULL) AS remainingFromFirstSelection
  FROM candidate_attempt_events AS events
  JOIN final_attempts AS attempts
    ON attempts.environment = events.environment
   AND attempts.attemptId = events.attemptId
   AND attempts.telemetryPlayerId IS NOT DISTINCT FROM events.telemetryPlayerId
),
cohort_enriched AS (
  SELECT candidateKey, selected, alternativeSelected, noSelectionOnly,
    gameplayOutcome, attemptElapsedTimeSeconds, remainingFromFirstExposure,
    remainingFromFirstSelection,
    PERCENTILE_CONT(IF(attemptElapsedTimeSeconds >= 0, attemptElapsedTimeSeconds, NULL), 0.25)
      OVER cohortWindow AS elapsedP25,
    PERCENTILE_CONT(IF(attemptElapsedTimeSeconds >= 0, attemptElapsedTimeSeconds, NULL), 0.50)
      OVER cohortWindow AS elapsedMedian,
    PERCENTILE_CONT(IF(attemptElapsedTimeSeconds >= 0, attemptElapsedTimeSeconds, NULL), 0.75)
      OVER cohortWindow AS elapsedP75,
    PERCENTILE_CONT(IF(remainingFromFirstExposure >= 0, remainingFromFirstExposure, NULL), 0.25)
      OVER cohortWindow AS remainingExposureP25,
    PERCENTILE_CONT(IF(remainingFromFirstExposure >= 0, remainingFromFirstExposure, NULL), 0.50)
      OVER cohortWindow AS remainingExposureMedian,
    PERCENTILE_CONT(IF(remainingFromFirstExposure >= 0, remainingFromFirstExposure, NULL), 0.75)
      OVER cohortWindow AS remainingExposureP75,
    PERCENTILE_CONT(IF(remainingFromFirstSelection >= 0, remainingFromFirstSelection, NULL), 0.25)
      OVER selectionWindow AS remainingSelectionP25,
    PERCENTILE_CONT(IF(remainingFromFirstSelection >= 0, remainingFromFirstSelection, NULL), 0.50)
      OVER selectionWindow AS remainingSelectionMedian,
    PERCENTILE_CONT(IF(remainingFromFirstSelection >= 0, remainingFromFirstSelection, NULL), 0.75)
      OVER selectionWindow AS remainingSelectionP75
  FROM cohorts
  WINDOW cohortWindow AS (PARTITION BY candidateKey, selected),
    selectionWindow AS (PARTITION BY candidateKey)
),
cohort_stats AS (
  SELECT candidateKey, selected,
    COUNT(*) AS finalAttempts,
    COUNTIF(gameplayOutcome = 'Clear') AS clears,
    COUNTIF(gameplayOutcome = 'Dead') AS deaths,
    COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
    COUNTIF(gameplayOutcome IS NULL OR gameplayOutcome NOT IN ('Clear', 'Dead', 'Abandon'))
      AS unrecognizedOutcomes,
    SAFE_DIVIDE(COUNTIF(gameplayOutcome = 'Clear'), COUNTIF(gameplayOutcome IN ('Clear', 'Dead')))
      AS clearRate,
    COUNTIF(alternativeSelected) AS alternativeSelectedAttempts,
    COUNTIF(noSelectionOnly) AS noSelectionOnlyAttempts,
    COUNTIF(attemptElapsedTimeSeconds IS NULL OR attemptElapsedTimeSeconds < 0
      OR remainingFromFirstExposure IS NULL OR remainingFromFirstExposure < 0
      OR (selected AND (remainingFromFirstSelection IS NULL OR remainingFromFirstSelection < 0)))
      AS invalidOutcomeTimeRows,
    ANY_VALUE(elapsedP25) AS elapsedP25, ANY_VALUE(elapsedMedian) AS elapsedMedian,
    ANY_VALUE(elapsedP75) AS elapsedP75,
    ANY_VALUE(remainingExposureP25) AS remainingExposureP25,
    ANY_VALUE(remainingExposureMedian) AS remainingExposureMedian,
    ANY_VALUE(remainingExposureP75) AS remainingExposureP75,
    ANY_VALUE(remainingSelectionP25) AS remainingSelectionP25,
    ANY_VALUE(remainingSelectionMedian) AS remainingSelectionMedian,
    ANY_VALUE(remainingSelectionP75) AS remainingSelectionP75
  FROM cohort_enriched GROUP BY candidateKey, selected
)
SELECT candidateKey,
  COALESCE(MAX(IF(selected, finalAttempts, NULL)), 0) AS selectedAttempts,
  COALESCE(MAX(IF(selected, clears, NULL)), 0) AS selectedClears,
  COALESCE(MAX(IF(selected, deaths, NULL)), 0) AS selectedDeaths,
  COALESCE(MAX(IF(selected, abandons, NULL)), 0) AS selectedAbandons,
  COALESCE(MAX(IF(selected, unrecognizedOutcomes, NULL)), 0) AS selectedUnrecognizedOutcomes,
  MAX(IF(selected, clearRate, NULL)) AS selectedClearRate,
  MAX(IF(selected, elapsedP25, NULL)) AS selectedElapsedP25,
  MAX(IF(selected, elapsedMedian, NULL)) AS selectedElapsedMedian,
  MAX(IF(selected, elapsedP75, NULL)) AS selectedElapsedP75,
  MAX(IF(selected, remainingExposureP25, NULL)) AS selectedRemainingFromExposureP25,
  MAX(IF(selected, remainingExposureMedian, NULL)) AS selectedRemainingFromExposureMedian,
  MAX(IF(selected, remainingExposureP75, NULL)) AS selectedRemainingFromExposureP75,
  MAX(IF(selected, remainingSelectionP25, NULL)) AS selectedRemainingOutcomeSecondsP25,
  MAX(IF(selected, remainingSelectionMedian, NULL)) AS selectedRemainingOutcomeSecondsMedian,
  MAX(IF(selected, remainingSelectionP75, NULL)) AS selectedRemainingOutcomeSecondsP75,
  COALESCE(MAX(IF(NOT selected, finalAttempts, NULL)), 0) AS exposedNotSelectedAttempts,
  COALESCE(MAX(IF(NOT selected, alternativeSelectedAttempts, NULL)), 0) AS alternativeSelectedAttempts,
  COALESCE(MAX(IF(NOT selected, noSelectionOnlyAttempts, NULL)), 0) AS noSelectionOnlyAttempts,
  COALESCE(MAX(IF(NOT selected, clears, NULL)), 0) AS exposedNotSelectedClears,
  COALESCE(MAX(IF(NOT selected, deaths, NULL)), 0) AS exposedNotSelectedDeaths,
  COALESCE(MAX(IF(NOT selected, abandons, NULL)), 0) AS exposedNotSelectedAbandons,
  COALESCE(MAX(IF(NOT selected, unrecognizedOutcomes, NULL)), 0)
    AS exposedNotSelectedUnrecognizedOutcomes,
  MAX(IF(NOT selected, clearRate, NULL)) AS exposedNotSelectedClearRate,
  MAX(IF(NOT selected, elapsedP25, NULL)) AS exposedNotSelectedElapsedP25,
  MAX(IF(NOT selected, elapsedMedian, NULL)) AS exposedNotSelectedElapsedMedian,
  MAX(IF(NOT selected, elapsedP75, NULL)) AS exposedNotSelectedElapsedP75,
  MAX(IF(NOT selected, remainingExposureP25, NULL)) AS exposedNotSelectedRemainingFromExposureP25,
  MAX(IF(NOT selected, remainingExposureMedian, NULL)) AS exposedNotSelectedRemainingFromExposureMedian,
  MAX(IF(NOT selected, remainingExposureP75, NULL)) AS exposedNotSelectedRemainingFromExposureP75,
  100 * (MAX(IF(selected, clearRate, NULL)) - MAX(IF(NOT selected, clearRate, NULL)))
    AS clearRateDifferencePp,
  SUM(invalidOutcomeTimeRows) AS invalidOutcomeTimeRows
FROM cohort_stats
GROUP BY candidateKey
HAVING exposedNotSelectedAttempts = alternativeSelectedAttempts + noSelectionOnlyAttempts
ORDER BY candidateKey
