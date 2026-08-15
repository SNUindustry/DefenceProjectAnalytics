-- Aggregate post-run progression reach and timing.
WITH
-- @include post_run_population_ctes_v1
, mature_denominators AS (
  SELECT gameplayOutcome AS anchorOutcome, COUNT(*) AS windowCount
  FROM classified_windows WHERE matureWindow GROUP BY gameplayOutcome
), progression_base AS (
  SELECT w.gameplayOutcome AS anchorOutcome, p.anchorKey, p.eventId, p.progressionKind,
    p.timeToProgressionSeconds
  FROM classified_windows AS w
  JOIN progression_windowed AS p USING (anchorKey)
  WHERE w.matureWindow AND p.sameContent
), aggregated AS (
SELECT
  p.anchorOutcome,
  CASE WHEN p.progressionKind IN ('WeaponRecipe','EvolutionChoice','StatReset','EvolutionApplyOnly')
    THEN p.progressionKind ELSE CONCAT('Unrecognized:', COALESCE(p.progressionKind, 'Missing')) END
    AS progressionKind,
  m.windowCount,
  COUNT(DISTINCT p.anchorKey) AS progressionWindows,
  COUNT(p.eventId) AS progressionEventCount,
  SAFE_DIVIDE(COUNT(DISTINCT p.anchorKey), m.windowCount) AS progressionRate
FROM progression_base AS p JOIN mature_denominators AS m USING (anchorOutcome)
GROUP BY p.anchorOutcome, p.progressionKind, m.windowCount
), percentiles AS (
SELECT DISTINCT anchorOutcome, progressionKind,
  PERCENTILE_CONT(p.timeToProgressionSeconds, 0.25) OVER (
    PARTITION BY p.anchorOutcome, p.progressionKind) AS timeToProgressionP25,
  PERCENTILE_CONT(p.timeToProgressionSeconds, 0.50) OVER (
    PARTITION BY p.anchorOutcome, p.progressionKind) AS timeToProgressionMedian,
  PERCENTILE_CONT(p.timeToProgressionSeconds, 0.75) OVER (
    PARTITION BY p.anchorOutcome, p.progressionKind) AS timeToProgressionP75
FROM progression_base AS p
)
SELECT a.anchorOutcome, a.progressionKind, a.windowCount, a.progressionWindows,
  a.progressionEventCount, a.progressionRate, p.timeToProgressionP25,
  p.timeToProgressionMedian, p.timeToProgressionP75
FROM aggregated AS a JOIN percentiles AS p USING (anchorOutcome, progressionKind)
ORDER BY anchorOutcome, progressionKind
