-- Descriptive present/absent final-attempt associations; never causal effects.
WITH
-- @include weapon_population_ctes_v1
, detail_attempts AS (
  SELECT final.*
  FROM final_attempts AS final
  JOIN eligible_attempts AS eligible
    ON eligible.environment = final.environment AND eligible.attemptId = final.attemptId
   AND eligible.telemetryPlayerId IS NOT DISTINCT FROM final.telemetryPlayerId
),
weapon_rows AS (
  SELECT segments.attemptId, weapons.rowIndex, weapons.weaponFamilyId, weapons.uploadedAtUtc
  FROM eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_weapon_summary` AS weapons
    ON weapons.environment = segments.environment AND weapons.runId = segments.runId
  WHERE COALESCE(weapons.totalDamage, 0) > 0
    AND (@uploaded_start_utc IS NULL OR weapons.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR weapons.uploadedAtUtc < @uploaded_end_utc)
    AND weapons.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY weapons.environment, weapons.runId, weapons.rowIndex
    ORDER BY weapons.uploadedAtUtc DESC) = 1
),
combat_presence AS (
  SELECT DISTINCT weaponFamilyId, attemptId FROM weapon_rows
  WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
),
combat_families AS (SELECT DISTINCT weaponFamilyId FROM combat_presence),
combat_cohorts AS (
  SELECT families.weaponFamilyId, 'combatObserved' AS associationDefinition,
    presence.attemptId IS NOT NULL AS present,
    attempts.gameplayOutcome, attempts.attemptElapsedTimeSeconds
  FROM combat_families AS families CROSS JOIN detail_attempts AS attempts
  LEFT JOIN combat_presence AS presence
    ON presence.weaponFamilyId = families.weaponFamilyId AND presence.attemptId = attempts.attemptId
),
final_state_rows AS (
  SELECT attempts.attemptId, attempts.gameplayOutcome, attempts.attemptElapsedTimeSeconds,
    states.rowIndex, states.weaponFamilyId, states.uploadedAtUtc
  FROM final_attempts AS attempts
  JOIN eligible_segments AS segments
    ON segments.environment = attempts.environment AND segments.runId = attempts.finalRunId
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_final_weapon_state` AS states
    ON states.environment = segments.environment AND states.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR states.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR states.uploadedAtUtc < @uploaded_end_utc)
    AND states.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (PARTITION BY states.environment, states.runId, states.rowIndex
    ORDER BY states.uploadedAtUtc DESC) = 1
),
final_assessed_attempts AS (
  SELECT DISTINCT attemptId, gameplayOutcome, attemptElapsedTimeSeconds FROM final_state_rows
),
final_presence AS (
  SELECT DISTINCT weaponFamilyId, attemptId FROM final_state_rows
  WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
),
final_families AS (SELECT DISTINCT weaponFamilyId FROM final_presence),
final_cohorts AS (
  SELECT families.weaponFamilyId, 'finalOwned' AS associationDefinition,
    presence.attemptId IS NOT NULL AS present,
    attempts.gameplayOutcome, attempts.attemptElapsedTimeSeconds
  FROM final_families AS families CROSS JOIN final_assessed_attempts AS attempts
  LEFT JOIN final_presence AS presence
    ON presence.weaponFamilyId = families.weaponFamilyId AND presence.attemptId = attempts.attemptId
),
cohorts AS (SELECT * FROM combat_cohorts UNION ALL SELECT * FROM final_cohorts),
cohort_enriched AS (
  SELECT *,
    PERCENTILE_CONT(IF(attemptElapsedTimeSeconds >= 0, attemptElapsedTimeSeconds, NULL), 0.25)
      OVER cohortWindow AS elapsedP25,
    PERCENTILE_CONT(IF(attemptElapsedTimeSeconds >= 0, attemptElapsedTimeSeconds, NULL), 0.50)
      OVER cohortWindow AS elapsedMedian,
    PERCENTILE_CONT(IF(attemptElapsedTimeSeconds >= 0, attemptElapsedTimeSeconds, NULL), 0.75)
      OVER cohortWindow AS elapsedP75
  FROM cohorts
  WINDOW cohortWindow AS (PARTITION BY associationDefinition, weaponFamilyId, present)
),
cohort_stats AS (
  SELECT associationDefinition, weaponFamilyId, present,
    COUNT(*) AS finalAttempts,
    COUNTIF(gameplayOutcome = 'Clear') AS clears,
    COUNTIF(gameplayOutcome = 'Dead') AS deaths,
    COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
    COUNTIF(gameplayOutcome IS NULL OR gameplayOutcome NOT IN ('Clear', 'Dead', 'Abandon')) AS unrecognizedOutcomes,
    SAFE_DIVIDE(COUNTIF(gameplayOutcome = 'Clear'),
      COUNTIF(gameplayOutcome IN ('Clear', 'Dead'))) AS clearRate,
    ANY_VALUE(elapsedP25) AS elapsedP25, ANY_VALUE(elapsedMedian) AS elapsedMedian,
    ANY_VALUE(elapsedP75) AS elapsedP75
  FROM cohort_enriched GROUP BY associationDefinition, weaponFamilyId, present
)
SELECT associationDefinition, weaponFamilyId,
  COALESCE(MAX(IF(present, finalAttempts, NULL)), 0) AS presentFinalAttempts,
  COALESCE(MAX(IF(present, clears, NULL)), 0) AS presentClears,
  COALESCE(MAX(IF(present, deaths, NULL)), 0) AS presentDeaths,
  COALESCE(MAX(IF(present, abandons, NULL)), 0) AS presentAbandons,
  COALESCE(MAX(IF(present, unrecognizedOutcomes, NULL)), 0) AS presentUnrecognizedOutcomes,
  MAX(IF(present, clearRate, NULL)) AS presentClearRate,
  MAX(IF(present, elapsedP25, NULL)) AS presentElapsedP25,
  MAX(IF(present, elapsedMedian, NULL)) AS presentElapsedMedian,
  MAX(IF(present, elapsedP75, NULL)) AS presentElapsedP75,
  COALESCE(MAX(IF(NOT present, finalAttempts, NULL)), 0) AS absentFinalAttempts,
  COALESCE(MAX(IF(NOT present, clears, NULL)), 0) AS absentClears,
  COALESCE(MAX(IF(NOT present, deaths, NULL)), 0) AS absentDeaths,
  COALESCE(MAX(IF(NOT present, abandons, NULL)), 0) AS absentAbandons,
  COALESCE(MAX(IF(NOT present, unrecognizedOutcomes, NULL)), 0) AS absentUnrecognizedOutcomes,
  MAX(IF(NOT present, clearRate, NULL)) AS absentClearRate,
  MAX(IF(NOT present, elapsedP25, NULL)) AS absentElapsedP25,
  MAX(IF(NOT present, elapsedMedian, NULL)) AS absentElapsedMedian,
  MAX(IF(NOT present, elapsedP75, NULL)) AS absentElapsedP75,
  100 * (MAX(IF(present, clearRate, NULL)) - MAX(IF(NOT present, clearRate, NULL))) AS clearRateDifferencePp
FROM cohort_stats
GROUP BY associationDefinition, weaponFamilyId
ORDER BY associationDefinition, weaponFamilyId
