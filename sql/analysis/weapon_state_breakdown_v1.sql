-- Aggregate final weapon state and exact effective-level distributions.
WITH
-- @include weapon_population_ctes_v1
, final_state_rows AS (
  SELECT attempts.attemptId, attempts.gameplayOutcome,
    states.rowIndex, states.weaponFamilyId, states.weaponId, states.weaponType,
    states.baseLevel, states.effectiveLevel, states.isEvolutionResult, states.isActive,
    states.uploadedAtUtc
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
assessed AS (SELECT COUNT(DISTINCT attemptId) AS finalStateAssessedAttempts FROM final_state_rows),
state_rows AS (
  SELECT 'state' AS rowType, weaponFamilyId, weaponId, weaponType,
    CAST(NULL AS INT64) AS effectiveLevel, isEvolutionResult, isActive,
    COUNT(*) AS stateRows, COUNT(DISTINCT attemptId) AS finalOwnedAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Clear', attemptId, NULL)) AS clearAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Dead', attemptId, NULL)) AS deadAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Abandon', attemptId, NULL)) AS abandonAttempts,
    MIN(baseLevel) AS minBaseLevel, MAX(baseLevel) AS maxBaseLevel
  FROM final_state_rows
  WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
  GROUP BY weaponFamilyId, weaponId, weaponType, isEvolutionResult, isActive
),
level_rows AS (
  SELECT 'level' AS rowType, weaponFamilyId, weaponId, weaponType,
    effectiveLevel, isEvolutionResult, isActive,
    COUNT(*) AS stateRows, COUNT(DISTINCT attemptId) AS finalOwnedAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Clear', attemptId, NULL)) AS clearAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Dead', attemptId, NULL)) AS deadAttempts,
    COUNT(DISTINCT IF(gameplayOutcome = 'Abandon', attemptId, NULL)) AS abandonAttempts,
    MIN(baseLevel) AS minBaseLevel, MAX(baseLevel) AS maxBaseLevel
  FROM final_state_rows
  WHERE NULLIF(TRIM(weaponFamilyId), '') IS NOT NULL
  GROUP BY weaponFamilyId, weaponId, weaponType, effectiveLevel, isEvolutionResult, isActive
)
SELECT result.*, assessed.finalStateAssessedAttempts,
  SAFE_DIVIDE(result.finalOwnedAttempts, assessed.finalStateAssessedAttempts) AS finalOwnershipRatio
FROM (SELECT * FROM state_rows UNION ALL SELECT * FROM level_rows) AS result
CROSS JOIN assessed
ORDER BY rowType, weaponFamilyId, weaponId, weaponType, effectiveLevel
