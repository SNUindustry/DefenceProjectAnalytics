-- First observed/user actions and consecutive high-level user-action transitions.
WITH
-- @include post_run_population_ctes_v1
, mature AS (
  SELECT anchorKey, gameplayOutcome AS anchorOutcome, resolvedByNextRunWithinWindow
  FROM classified_windows WHERE matureWindow
), first_actions AS (
  SELECT
    m.anchorKey, m.anchorOutcome,
    COALESCE(
      ARRAY_AGG(a.observedAction IGNORE NULLS ORDER BY a.occurredAtUtc, a.domainPriority,
        a.rowIndex, a.eventId LIMIT 1)[SAFE_OFFSET(0)],
      IF(m.resolvedByNextRunWithinWindow, 'NextRunStarted', 'NoObservedAction')
    ) AS firstObservedAction,
    COALESCE(
      ARRAY_AGG(a.userAction IGNORE NULLS ORDER BY a.occurredAtUtc, a.domainPriority,
        a.rowIndex, a.eventId LIMIT 1)[SAFE_OFFSET(0)],
      'NoObservedUserAction'
    ) AS firstUserAction
  FROM mature AS m
  LEFT JOIN normalized_actions AS a USING (anchorKey)
  GROUP BY m.anchorKey, m.anchorOutcome, m.resolvedByNextRunWithinWindow
), ordered_user AS (
  SELECT
    a.anchorKey, m.anchorOutcome, a.userAction, a.occurredAtUtc,
    a.domainPriority, a.rowIndex, a.eventId,
    LAG(a.userAction) OVER (
      PARTITION BY a.anchorKey ORDER BY a.occurredAtUtc, a.domainPriority, a.rowIndex, a.eventId
    ) AS previousUserAction
  FROM normalized_actions AS a
  JOIN mature AS m USING (anchorKey)
  WHERE a.userAction IS NOT NULL
), collapsed_user AS (
  SELECT anchorKey, anchorOutcome, userAction, occurredAtUtc, domainPriority, rowIndex, eventId
  FROM ordered_user
  WHERE previousUserAction IS NULL OR previousUserAction != userAction
), transitions AS (
  SELECT
    anchorKey, anchorOutcome, userAction AS fromAction,
    LEAD(userAction) OVER (
      PARTITION BY anchorKey ORDER BY occurredAtUtc, domainPriority, rowIndex, eventId
    ) AS toAction
  FROM collapsed_user
), first_observed_rows AS (
  SELECT
    'firstObserved' AS rowType, anchorOutcome, firstObservedAction AS fromAction,
    CAST(NULL AS STRING) AS toAction, COUNT(*) AS actionCount,
    COUNT(*) OVER (PARTITION BY anchorOutcome) AS denominator
  FROM first_actions GROUP BY anchorOutcome, firstObservedAction
), first_user_rows AS (
  SELECT
    'firstUser' AS rowType, anchorOutcome, firstUserAction AS fromAction,
    CAST(NULL AS STRING) AS toAction, COUNT(*) AS actionCount,
    COUNT(*) OVER (PARTITION BY anchorOutcome) AS denominator
  FROM first_actions GROUP BY anchorOutcome, firstUserAction
), transition_rows AS (
  SELECT
    'transition' AS rowType, anchorOutcome, fromAction, toAction,
    COUNT(*) AS actionCount,
    SUM(COUNT(*)) OVER (PARTITION BY anchorOutcome, fromAction) AS denominator
  FROM transitions WHERE toAction IS NOT NULL AND fromAction != toAction
  GROUP BY anchorOutcome, fromAction, toAction
)
SELECT rowType, anchorOutcome, fromAction, toAction, actionCount, denominator,
  SAFE_DIVIDE(actionCount, denominator) AS ratio
FROM first_observed_rows
UNION ALL
SELECT rowType, anchorOutcome, fromAction, toAction, actionCount, denominator,
  SAFE_DIVIDE(actionCount, denominator) AS ratio
FROM first_user_rows
UNION ALL
SELECT rowType, anchorOutcome, fromAction, toAction, actionCount, denominator,
  SAFE_DIVIDE(actionCount, denominator) AS ratio
FROM transition_rows
ORDER BY rowType, anchorOutcome, actionCount DESC, fromAction, toAction
