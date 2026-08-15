-- Aggregate navigation presentation and exact user-navigation metrics.
WITH
-- @include post_run_population_ctes_v1
, mature AS (
  SELECT anchorKey, gameplayOutcome FROM classified_windows WHERE matureWindow
), dimensions AS (
  SELECT
    m.anchorKey, m.gameplayOutcome AS anchorOutcome,
    CASE
      WHEN l.eventKind = 'TabViewed' THEN COALESCE(l.tab, 'Unknown')
      WHEN l.eventKind = 'ShopSectionViewed' THEN CONCAT('ShopSection:', COALESCE(l.shopSection, 'Unknown'))
      ELSE CONCAT('Unrecognized:', l.eventKind)
    END AS dimension,
    l.eventKind, l.tab, l.navigationSource
  FROM mature AS m
  JOIN lobby_windowed AS l USING (anchorKey)
)
SELECT
  anchorOutcome, dimension,
  COUNT(DISTINCT anchorKey) AS viewedWindows,
  (SELECT COUNT(*) FROM mature AS x WHERE x.gameplayOutcome IS NOT DISTINCT FROM d.anchorOutcome)
    AS viewedDenominator,
  SAFE_DIVIDE(COUNT(DISTINCT anchorKey),
    (SELECT COUNT(*) FROM mature AS x WHERE x.gameplayOutcome IS NOT DISTINCT FROM d.anchorOutcome))
    AS viewedRate,
  COUNT(DISTINCT IF(eventKind = 'TabViewed' AND navigationSource = 'User', anchorKey, NULL))
    AS userNavigatedWindows,
  (SELECT COUNT(*) FROM mature AS x WHERE x.gameplayOutcome IS NOT DISTINCT FROM d.anchorOutcome)
    AS userNavigatedDenominator,
  SAFE_DIVIDE(COUNT(DISTINCT IF(
    eventKind = 'TabViewed' AND navigationSource = 'User', anchorKey, NULL)),
    (SELECT COUNT(*) FROM mature AS x WHERE x.gameplayOutcome IS NOT DISTINCT FROM d.anchorOutcome))
    AS userNavigatedRate,
  COUNT(DISTINCT IF(navigationSource = 'Initial', anchorKey, NULL)) AS initialViewedWindows,
  COUNT(DISTINCT IF(navigationSource = 'Programmatic', anchorKey, NULL))
    AS programmaticViewedWindows,
  COUNT(*) AS eventCount
FROM dimensions AS d
GROUP BY anchorOutcome, dimension
ORDER BY anchorOutcome, dimension
