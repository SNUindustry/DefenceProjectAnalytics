-- Run-retention sample, threshold facts, and data quality.
WITH
-- @include run_retention_population_ctes_v1
SELECT
  COUNT(*) AS anchorFinalAttempts,
  COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) AS uniquePlayers,
  COUNTIF(gameplayOutcome = 'Clear') AS clears,
  COUNTIF(gameplayOutcome = 'Dead') AS deaths,
  COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
  COUNTIF(gameplayOutcome NOT IN ('Clear', 'Dead', 'Abandon') OR gameplayOutcome IS NULL)
    AS unrecognizedOutcomes,
  COUNTIF(linkageEligible) AS eligibleAnchors,
  COUNTIF(NOT linkageEligible) AS ineligibleAnchors,
  COUNTIF(structuralState = 'NextRunObserved') AS nextRunObserved,
  COUNTIF(structuralState = 'NoNextRunObservedAsOf') AS noNextRunObservedAsOf,
  COUNTIF(latencyRightCensored) AS latencyRightCensoredAnchors,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'RightCensored')) AS thresholdRightCensoredAnchors,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'ReturnedWithinThreshold')) AS returnedWithinThresholdCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'ReturnedAfterThreshold')) AS returnedAfterThresholdCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState = 'LongTermNoNextRun')) AS noNextRunBeyondThresholdCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState IN ('ReturnedAfterThreshold', 'LongTermNoNextRun')))
    AS thresholdExceededCount,
  IF(@long_term_threshold_days IS NULL, NULL,
    COUNTIF(thresholdState IN (
      'ReturnedWithinThreshold', 'ReturnedAfterThreshold', 'LongTermNoNextRun'
    ))) AS thresholdResolvedDenominator,
  (SELECT COUNT(*)
   FROM anchor_physical AS physical
   JOIN anchors AS selected
     ON selected.environment = physical.environment
    AND selected.telemetryPlayerId IS NOT DISTINCT FROM physical.telemetryPlayerId
    AND selected.attemptId = physical.attemptId) AS physicalAnchorRows,
  COUNT(*) AS dedupedAnchorRows,
  COUNTIF(NULLIF(TRIM(telemetryPlayerId), '') IS NULL) AS missingPlayerIdentityAnchors,
  COUNTIF(segmentEndedAtUtc IS NULL) AS missingAnchorEndRows,
  COUNTIF(conflictingIdentity) AS conflictingIdentityAnchors,
  COUNTIF(segmentEndedAtUtc >= @analysis_as_of_utc OR observationAgeSeconds < 0)
    AS invalidTimingAnchors,
  (SELECT COUNT(*) FROM new_attempt_candidate_physical) AS physicalNextCandidateRows,
  (SELECT COUNT(*) FROM new_attempt_segments) AS dedupedNextCandidateRows,
  (SELECT COUNTIF(isFromResume IS TRUE) FROM new_attempt_candidate_physical)
    AS resumeContinuationsExcluded,
  (SELECT COUNTIF(NOT hasRunStartSnapshot) FROM new_attempt_candidate_physical)
    AS missingRunStartSnapshotRows,
  COUNTIF(sameStartCandidateCount > 1) AS ambiguousNextAttemptOrderGroups,
  COUNTIF(linkageEligible AND nextStartedAtUtc IS NOT NULL
    AND nextStageKey IS DISTINCT FROM stageKey) AS crossStageNextAttempts,
  COUNTIF(linkageEligible AND nextStartedAtUtc IS NOT NULL
    AND nextContentVersion IS DISTINCT FROM contentVersion) AS crossContentNextAttempts,
  COUNTIF(linkageEligible AND nextStartedAtUtc IS NOT NULL
    AND nextReleaseId IS DISTINCT FROM releaseId) AS crossReleaseNextAttempts,
  COUNTIF(nextOutcomePending) AS nextOutcomePendingAttempts
FROM classified_retention
