-- Canonical unordered progression-kind pairs within bounded episodes.
WITH
-- @include progression_population_ctes_v1
, kind_arrays AS (
  SELECT
    environment, episodeKey,
    ARRAY_AGG(DISTINCT normalizedProgressionKind ORDER BY normalizedProgressionKind) AS kinds
  FROM selected_episode_events
  GROUP BY environment, episodeKey
), pairs AS (
  SELECT environment, episodeKey, kindA, kindB
  FROM kind_arrays,
  UNNEST(kinds) AS kindA WITH OFFSET AS aOffset,
  UNNEST(kinds) AS kindB WITH OFFSET AS bOffset
  WHERE aOffset < bOffset
), denominator AS (
  SELECT COUNTIF(isMultiProgression) AS multiProgressionEpisodes
  FROM classified_episodes
)
SELECT
  kindA,
  kindB,
  COUNT(DISTINCT episodeKey) AS coOccurrenceEpisodeCount,
  denominator.multiProgressionEpisodes AS shareDenominator,
  SAFE_DIVIDE(COUNT(DISTINCT episodeKey), denominator.multiProgressionEpisodes)
    AS shareOfMultiProgressionEpisodes
FROM pairs CROSS JOIN denominator
GROUP BY kindA, kindB, shareDenominator
ORDER BY coOccurrenceEpisodeCount DESC, kindA, kindB;
