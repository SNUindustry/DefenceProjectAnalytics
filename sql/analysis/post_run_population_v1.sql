-- Post-run anchor, window, sample, and data-quality summary.
WITH
-- @include post_run_population_ctes_v1
, window_summary AS (
  SELECT
    COUNT(*) AS anchorFinalRuns,
    COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) AS uniquePlayers,
    COUNTIF(gameplayOutcome = 'Clear') AS clears,
    COUNTIF(gameplayOutcome = 'Dead') AS deaths,
    COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
    COUNTIF(gameplayOutcome NOT IN ('Clear', 'Dead', 'Abandon') OR gameplayOutcome IS NULL)
      AS unrecognizedOutcomes,
    COUNTIF(linkageEligible) AS linkageEligibleWindows,
    COUNTIF(matureWindow) AS matureWindows,
    COUNTIF(rightCensored) AS rightCensoredWindows,
    COUNTIF(resolvedByNextRunWithinWindow) AS resolvedWindows,
    COUNTIF(matureNoNextRunWithinWindow) AS matureNoNextWindows,
    COUNTIF(laterNextRunOutsideWindow) AS laterNextRunOutsideWindowWindows,
    COUNTIF(NULLIF(TRIM(telemetryPlayerId), '') IS NULL) AS missingPlayerIdentityAnchors,
    COUNTIF(segmentEndedAtUtc IS NULL) AS missingAnchorEndRows,
    COUNTIF(nextOutcomePending) AS nextOutcomePendingWindows,
    COUNTIF(resolvedByNextRunWithinWindow) AS nextRunWithinWindowWindows
  FROM classified_windows
), action_summary AS (
  SELECT
    COUNT(DISTINCT anchorKey) AS windowsWithObservedAction,
    COUNT(DISTINCT IF(userAction IS NOT NULL, anchorKey, NULL)) AS windowsWithUserAction,
    COUNTIF(NOT sameContent) AS crossContentActions,
    COUNTIF(NOT sameRelease) AS crossReleaseActions,
    COUNTIF(STARTS_WITH(observedAction, 'Unrecognized:')) AS unrecognizedActionRows
  FROM normalized_actions
), feedback_summary AS (
  SELECT
    COUNTIF(feedbackExposed) AS feedbackExposedWindows,
    COUNTIF(feedbackResponded) AS feedbackRespondedWindows,
    COUNTIF(conflictingResponse) AS conflictingFeedbackResponseWindows
  FROM feedback_cohorts
), navigation_summary AS (
  SELECT
    COUNT(DISTINCT IF(
      (eventKind = 'TabViewed' AND tab = 'Shop') OR eventKind = 'ShopSectionViewed',
      anchorKey, NULL)) AS shopPresentedWindows,
    COUNT(DISTINCT IF(
      eventKind = 'TabViewed' AND tab = 'Shop' AND navigationSource = 'User',
      anchorKey, NULL)) AS shopUserNavigatedWindows,
    COUNT(DISTINCT anchorKey) AS windowsWithLobbyActivity
  FROM lobby_windowed
), commerce_summary AS (
  SELECT
    COUNT(DISTINCT IF(
      eventKind = 'Attempt'
      AND analysisCategory IN ('CommerceShop', 'CommerceShopLegacy', 'CommerceIap')
      AND sameContent, anchorKey, NULL)) AS commerceAttemptWindows,
    COUNT(DISTINCT IF(
      eventKind = 'Result' AND resultCategory = 'Succeeded'
      AND analysisCategory IN ('CommerceShop', 'CommerceShopLegacy', 'CommerceIap')
      AND sameContent, anchorKey, NULL)) AS committedSuccessWindows,
    COUNTIF(analysisCategory = 'FunFeedbackReward') AS funFeedbackRewardsExcluded,
    COUNTIF(analysisCategory = 'ProgressionSpend') AS progressionTransactionsExcluded
  FROM transaction_windowed
), progression_summary AS (
  SELECT COUNT(DISTINCT IF(sameContent, anchorKey, NULL)) AS progressionWindows
  FROM progression_windowed
), feedback_quality AS (
  SELECT
    COUNTIF(eventKind = 'Response' AND NOT EXISTS (
      SELECT 1 FROM feedback_windowed AS e
      WHERE e.anchorKey = f.anchorKey AND e.eventKind = 'Exposure' AND e.inWindow
    )) AS feedbackLinkMismatchRows,
    COUNTIF(NOT inWindow OR NOT sameContent) AS feedbackOutsideWindowRows
  FROM feedback_windowed AS f
), shop_quality AS (
  SELECT COUNTIF(eventKind = 'OfferSelected' AND NOT EXISTS (
    SELECT 1 FROM shop_windowed AS e
    WHERE e.anchorKey = s.anchorKey AND e.eventKind = 'OfferExposure'
      AND e.presentationId IS NOT DISTINCT FROM s.presentationId
  )) AS offerSelectionWithoutExposure
  FROM shop_windowed AS s
), transaction_quality AS (
  SELECT
    COUNTIF(resultEventId IS NULL) AS transactionAttemptWithoutResult,
    (SELECT COUNTIF(NOT hasObservedAttempt) FROM result_observation)
      AS transactionResultWithoutObservedAttempt,
    (SELECT COUNTIF(
      analysisCategory IN ('CommerceShop', 'CommerceShopLegacy', 'CommerceIap')
      AND resultCategory = 'Succeeded' AND NOT hasObservedAttempt
    ) FROM result_observation) AS committedSuccessWithoutObservedAttemptResults,
    (SELECT COUNT(DISTINCT IF(
      analysisCategory IN ('CommerceShop', 'CommerceShopLegacy', 'CommerceIap')
      AND resultCategory = 'Succeeded' AND NOT hasObservedAttempt, anchorKey, NULL
    )) FROM result_observation) AS committedSuccessWithoutObservedAttemptWindows
  FROM attempt_resolution
), sequence_quality AS (
  SELECT COUNT(*) AS sameTimestampActionGroups
  FROM (
    SELECT anchorKey, occurredAtUtc
    FROM normalized_actions
    GROUP BY anchorKey, occurredAtUtc
    HAVING COUNT(*) > 1
  )
), offer_attempt_quality AS (
  SELECT COUNTIF(NOT EXISTS (
    SELECT 1 FROM shop_windowed AS s
    WHERE s.anchorKey = a.anchorKey AND s.eventKind = 'OfferSelected'
      AND NULLIF(TRIM(s.offerId), '') = NULLIF(TRIM(a.transactionId), '')
      AND s.occurredAtUtc <= a.occurredAtUtc
  )) AS commerceAttemptWithoutShopSelection
  FROM transaction_attempts AS a
  WHERE a.analysisCategory IN ('CommerceShop', 'CommerceShopLegacy', 'CommerceIap')
    AND a.sameContent
)
SELECT
  w.anchorFinalRuns, w.uniquePlayers, w.clears, w.deaths, w.abandons,
  w.unrecognizedOutcomes, w.linkageEligibleWindows, w.matureWindows,
  w.rightCensoredWindows, w.resolvedWindows, w.matureNoNextWindows,
  w.laterNextRunOutsideWindowWindows, w.missingPlayerIdentityAnchors,
  w.missingAnchorEndRows, w.nextOutcomePendingWindows,
  w.nextRunWithinWindowWindows,
  f.feedbackExposedWindows, f.feedbackRespondedWindows,
  n.shopPresentedWindows, n.shopUserNavigatedWindows,
  c.commerceAttemptWindows, c.committedSuccessWindows, p.progressionWindows,
  a.windowsWithObservedAction, a.windowsWithUserAction,
  GREATEST(w.matureWindows - a.windowsWithObservedAction, 0) AS windowsWithoutObservedAction,
  GREATEST(w.matureWindows - n.windowsWithLobbyActivity, 0)
    AS windowsWithoutLobbyActivityObserved,
  fq.feedbackLinkMismatchRows, fq.feedbackOutsideWindowRows,
  f.conflictingFeedbackResponseWindows,
  (SELECT COUNT(*) FROM lobby_physical) AS physicalLobbyRows,
  (SELECT COUNT(*) FROM lobby_deduped) AS dedupedLobbyRows,
  (SELECT COUNT(*) FROM shop_physical) AS physicalShopRows,
  (SELECT COUNT(*) FROM shop_deduped) AS dedupedShopRows,
  (SELECT COUNT(*) FROM transaction_physical) AS physicalTransactionRows,
  (SELECT COUNT(*) FROM transaction_deduped) AS dedupedTransactionRows,
  (SELECT COUNT(*) FROM progression_physical) AS physicalProgressionRows,
  (SELECT COUNT(*) FROM progression_deduped) AS dedupedProgressionRows,
  (SELECT COUNT(*) FROM iap_physical) AS physicalIapRows,
  (SELECT COUNT(*) FROM iap_deduped) AS dedupedIapRows,
  a.crossContentActions, a.crossReleaseActions,
  sq.offerSelectionWithoutExposure, oq.commerceAttemptWithoutShopSelection,
  tq.transactionAttemptWithoutResult, tq.transactionResultWithoutObservedAttempt,
  tq.committedSuccessWithoutObservedAttemptResults,
  tq.committedSuccessWithoutObservedAttemptWindows,
  c.funFeedbackRewardsExcluded, c.progressionTransactionsExcluded,
  seq.sameTimestampActionGroups, a.unrecognizedActionRows
FROM window_summary AS w
CROSS JOIN action_summary AS a
CROSS JOIN feedback_summary AS f
CROSS JOIN navigation_summary AS n
CROSS JOIN commerce_summary AS c
CROSS JOIN progression_summary AS p
CROSS JOIN feedback_quality AS fq
CROSS JOIN shop_quality AS sq
CROSS JOIN transaction_quality AS tq
CROSS JOIN sequence_quality AS seq
CROSS JOIN offer_attempt_quality AS oq
