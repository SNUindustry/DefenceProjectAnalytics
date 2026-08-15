-- Feedback cohorts and outcome-conditioned post-run behavior.
WITH
-- @include post_run_population_ctes_v1
, flags AS (
  SELECT
    w.anchorKey, w.gameplayOutcome AS anchorOutcome, c.feedbackCohort,
    EXISTS (SELECT 1 FROM lobby_windowed l WHERE l.anchorKey = w.anchorKey
      AND ((l.eventKind = 'TabViewed' AND l.tab = 'Shop') OR l.eventKind = 'ShopSectionViewed'))
      AS shopPresented,
    EXISTS (SELECT 1 FROM lobby_windowed l WHERE l.anchorKey = w.anchorKey
      AND l.eventKind = 'TabViewed' AND l.tab = 'Shop' AND l.navigationSource = 'User')
      AS shopUserNavigated,
    EXISTS (SELECT 1 FROM shop_windowed s WHERE s.anchorKey = w.anchorKey
      AND s.eventKind = 'OfferSelected' AND s.sameContent) AS offerSelected,
    EXISTS (SELECT 1 FROM transaction_attempts t WHERE t.anchorKey = w.anchorKey
      AND t.sameContent AND t.analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap'))
      AS commerceAttemptObserved,
    EXISTS (SELECT 1 FROM result_observation r WHERE r.anchorKey = w.anchorKey
      AND r.sameContent AND r.resultCategory = 'Succeeded'
      AND r.analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap'))
      AS committedSuccess,
    EXISTS (SELECT 1 FROM progression_windowed p WHERE p.anchorKey = w.anchorKey AND p.sameContent)
      AS progressionReached,
    w.resolvedByNextRunWithinWindow AS nextRunWithinWindow,
    w.timeToNextRunSeconds
  FROM classified_windows AS w
  JOIN feedback_cohorts AS c USING (anchorKey)
  WHERE w.matureWindow
), aggregated AS (
SELECT
  anchorOutcome, feedbackCohort, COUNT(*) AS windowCount,
  COUNTIF(shopPresented) AS shopPresentedCount,
  SAFE_DIVIDE(COUNTIF(shopPresented), COUNT(*)) AS shopPresentedRate,
  COUNTIF(shopUserNavigated) AS shopUserNavigatedCount,
  SAFE_DIVIDE(COUNTIF(shopUserNavigated), COUNT(*)) AS shopUserNavigatedRate,
  COUNTIF(offerSelected) AS offerSelectedCount,
  SAFE_DIVIDE(COUNTIF(offerSelected), COUNT(*)) AS offerSelectedRate,
  COUNTIF(commerceAttemptObserved) AS commerceAttemptCount,
  SAFE_DIVIDE(COUNTIF(commerceAttemptObserved), COUNT(*)) AS commerceAttemptRate,
  COUNTIF(committedSuccess) AS committedSuccessCount,
  SAFE_DIVIDE(COUNTIF(committedSuccess), COUNT(*)) AS committedSuccessWindowRate,
  COUNTIF(progressionReached) AS progressionCount,
  SAFE_DIVIDE(COUNTIF(progressionReached), COUNT(*)) AS progressionRate,
  COUNTIF(nextRunWithinWindow) AS nextRunCount,
  SAFE_DIVIDE(COUNTIF(nextRunWithinWindow), COUNT(*)) AS nextRunRate
FROM flags
GROUP BY anchorOutcome, feedbackCohort
), percentiles AS (
SELECT DISTINCT anchorOutcome, feedbackCohort,
  PERCENTILE_CONT(IF(nextRunWithinWindow, timeToNextRunSeconds, NULL), 0.25) OVER
    (PARTITION BY anchorOutcome, feedbackCohort) AS timeToNextRunP25,
  PERCENTILE_CONT(IF(nextRunWithinWindow, timeToNextRunSeconds, NULL), 0.50) OVER
    (PARTITION BY anchorOutcome, feedbackCohort) AS timeToNextRunMedian,
  PERCENTILE_CONT(IF(nextRunWithinWindow, timeToNextRunSeconds, NULL), 0.75) OVER
    (PARTITION BY anchorOutcome, feedbackCohort) AS timeToNextRunP75
FROM flags
)
SELECT a.anchorOutcome, a.feedbackCohort, a.windowCount, a.shopPresentedCount,
  a.shopPresentedRate, a.shopUserNavigatedCount, a.shopUserNavigatedRate,
  a.offerSelectedCount, a.offerSelectedRate, a.commerceAttemptCount,
  a.commerceAttemptRate, a.committedSuccessCount, a.committedSuccessWindowRate,
  a.progressionCount, a.progressionRate, a.nextRunCount, a.nextRunRate,
  p.timeToNextRunP25, p.timeToNextRunMedian, p.timeToNextRunP75
FROM aggregated AS a JOIN percentiles AS p USING (anchorOutcome, feedbackCohort)
ORDER BY anchorOutcome, feedbackCohort
