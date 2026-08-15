-- Exact aggregate state/value transitions; no generic numeric-delta interpretation.
WITH
-- @include progression_population_ctes_v1
SELECT
  normalizedProgressionKind AS progressionKind,
  NULLIF(TRIM(targetId), '') AS targetId,
  NULLIF(TRIM(secondaryId), '') AS secondaryId,
  beforeState,
  afterState,
  beforeValue,
  afterValue,
  COUNT(*) AS eventCount,
  COUNT(DISTINCT episodeKey) AS boundedEpisodeCount,
  COUNTIF(episodeKey IS NULL) AS unboundedEventCount
FROM event_boundaries
WHERE isScoped
GROUP BY progressionKind, targetId, secondaryId,
  beforeState, afterState, beforeValue, afterValue
ORDER BY eventCount DESC, progressionKind, targetId, secondaryId,
  beforeState, afterState;
