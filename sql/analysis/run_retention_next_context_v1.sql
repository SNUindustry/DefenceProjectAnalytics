-- Run-retention latency distributions and next-attempt context.
WITH
-- @include run_retention_population_ctes_v1
SELECT
  COUNTIF(linkageEligible AND nextStartedAtUtc IS NOT NULL) AS observedNextCount,
  COUNTIF(latencyRightCensored) AS censoredCount,
  APPROX_QUANTILES(IF(linkageEligible, nextRunDelaySeconds, NULL), 100)[OFFSET(50)]
    AS nextRunDelayP50,
  APPROX_QUANTILES(IF(linkageEligible, nextRunDelaySeconds, NULL), 100)[OFFSET(75)]
    AS nextRunDelayP75,
  APPROX_QUANTILES(IF(linkageEligible, nextRunDelaySeconds, NULL), 100)[OFFSET(90)]
    AS nextRunDelayP90,
  APPROX_QUANTILES(IF(linkageEligible, nextRunDelaySeconds, NULL), 100)[OFFSET(95)]
    AS nextRunDelayP95,
  APPROX_QUANTILES(IF(latencyRightCensored, observationAgeSeconds, NULL), 100)[OFFSET(50)]
    AS censoredObservationAgeP50,
  APPROX_QUANTILES(IF(latencyRightCensored, observationAgeSeconds, NULL), 100)[OFFSET(75)]
    AS censoredObservationAgeP75,
  APPROX_QUANTILES(IF(latencyRightCensored, observationAgeSeconds, NULL), 100)[OFFSET(90)]
    AS censoredObservationAgeP90,
  APPROX_QUANTILES(IF(latencyRightCensored, observationAgeSeconds, NULL), 100)[OFFSET(95)]
    AS censoredObservationAgeP95,
  COUNTIF(sameStageNextRun) AS sameStageNextRunCount,
  COUNTIF(sameContentNextRun) AS sameContentNextRunCount,
  COUNTIF(linkageEligible AND nextStartedAtUtc IS NOT NULL
    AND nextStageKey IS DISTINCT FROM stageKey) AS crossStageNextAttempts,
  COUNTIF(linkageEligible AND nextStartedAtUtc IS NOT NULL
    AND nextContentVersion IS DISTINCT FROM contentVersion) AS crossContentNextAttempts,
  COUNTIF(linkageEligible AND nextStartedAtUtc IS NOT NULL
    AND nextReleaseId IS DISTINCT FROM releaseId) AS crossReleaseNextAttempts,
  COUNTIF(nextOutcomePending) AS nextOutcomePendingAttempts
FROM classified_retention
