-- Shop presentation, offer, observed-Attempt funnel, and durable success presence.
WITH
-- @include post_run_population_ctes_v1
, mature AS (
  SELECT anchorKey, gameplayOutcome AS anchorOutcome
  FROM classified_windows WHERE matureWindow
), window_flags AS (
  SELECT
    m.anchorKey, m.anchorOutcome,
    EXISTS (SELECT 1 FROM lobby_windowed l WHERE l.anchorKey = m.anchorKey
      AND ((l.eventKind = 'TabViewed' AND l.tab = 'Shop') OR l.eventKind = 'ShopSectionViewed'))
      AS shopPresented,
    EXISTS (SELECT 1 FROM lobby_windowed l WHERE l.anchorKey = m.anchorKey
      AND l.eventKind = 'TabViewed' AND l.tab = 'Shop' AND l.navigationSource = 'User')
      AS shopUserNavigated,
    EXISTS (SELECT 1 FROM shop_windowed s WHERE s.anchorKey = m.anchorKey
      AND s.eventKind = 'OfferExposure' AND s.sameContent) AS offerExposed,
    EXISTS (SELECT 1 FROM shop_windowed s WHERE s.anchorKey = m.anchorKey
      AND s.eventKind = 'OfferSelected' AND s.sameContent) AS offerSelected,
    EXISTS (SELECT 1 FROM transaction_attempts t WHERE t.anchorKey = m.anchorKey
      AND t.sameContent AND t.analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap'))
      AS commerceAttemptObserved,
    EXISTS (SELECT 1 FROM attempt_resolution a WHERE a.anchorKey = m.anchorKey
      AND a.analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap')
      AND a.resultCategory = 'Succeeded') AS observedAttemptLinkedSucceeded,
    EXISTS (SELECT 1 FROM result_observation r WHERE r.anchorKey = m.anchorKey
      AND r.sameContent AND r.analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap')
      AND r.resultCategory = 'Succeeded') AS committedSuccess
  FROM mature AS m
), funnel AS (
  SELECT
    anchorOutcome, COUNT(*) AS matureWindows,
    COUNTIF(shopPresented) AS shopPresentedWindows,
    COUNTIF(shopUserNavigated) AS shopUserNavigatedWindows,
    COUNTIF(offerExposed) AS offerExposedWindows,
    COUNTIF(offerSelected) AS offerSelectedWindows,
    COUNTIF(commerceAttemptObserved) AS commerceAttemptObservedWindows,
    COUNTIF(observedAttemptLinkedSucceeded) AS observedAttemptLinkedSucceededWindows,
    COUNTIF(committedSuccess) AS committedSuccessWindows
  FROM window_flags GROUP BY anchorOutcome
), commerce AS (
  SELECT
    a.anchorOutcome, a.analysisCategory AS dimension,
    COUNT(*) AS observedAttemptCount,
    COUNTIF(a.resultCategory = 'Succeeded') AS linkedSucceededAttemptCount,
    COUNTIF(a.resultCategory = 'Blocked') AS blockedAttemptCount,
    COUNTIF(a.resultCategory = 'Cancelled') AS cancelledAttemptCount,
    COUNTIF(a.resultCategory = 'Failed') AS failedAttemptCount,
    COUNTIF(a.resultCategory = 'Duplicate') AS duplicateAttemptCount,
    COUNTIF(a.resultCategory IS NULL) AS unresolvedAttemptCount
  FROM attempt_resolution AS a
  WHERE a.analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap')
  GROUP BY a.anchorOutcome, a.analysisCategory
), committed AS (
  SELECT
    anchorOutcome, analysisCategory AS dimension,
    COUNTIF(resultCategory = 'Succeeded') AS committedSuccessResultCount,
    COUNT(DISTINCT IF(resultCategory = 'Succeeded', anchorKey, NULL)) AS committedSuccessWindows,
    COUNTIF(resultCategory = 'Succeeded' AND NOT hasObservedAttempt)
      AS committedSuccessWithoutObservedAttemptResults,
    COUNT(DISTINCT IF(resultCategory = 'Succeeded' AND NOT hasObservedAttempt, anchorKey, NULL))
      AS committedSuccessWithoutObservedAttemptWindows
  FROM result_observation
  WHERE sameContent AND analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap')
  GROUP BY anchorOutcome, analysisCategory
), exposed_offers AS (
  SELECT DISTINCT s.anchorKey, s.anchorOutcome, o.offerId
  FROM shop_windowed AS s
  JOIN shop_offer_deduped AS o
    ON o.environment = s.environment AND o.parentEventId = s.eventId
  WHERE s.eventKind = 'OfferExposure' AND s.sameContent
    AND NULLIF(TRIM(o.offerId), '') IS NOT NULL
), selected_offers AS (
  SELECT DISTINCT anchorKey, anchorOutcome, offerId, occurredAtUtc
  FROM shop_windowed
  WHERE eventKind = 'OfferSelected' AND sameContent AND NULLIF(TRIM(offerId), '') IS NOT NULL
), offer_dimensions AS (
  SELECT anchorOutcome, offerId FROM exposed_offers
  UNION DISTINCT
  SELECT anchorOutcome, offerId FROM selected_offers
), offer_rows AS (
  SELECT
    d.anchorOutcome, d.offerId AS dimension,
    COUNT(DISTINCT e.anchorKey) AS exposedWindows,
    COUNT(DISTINCT s.anchorKey) AS selectedWindows,
    COUNT(DISTINCT a.anchorKey) AS attemptedWindows,
    COUNT(DISTINCT IF(a.resultCategory = 'Succeeded', a.anchorKey, NULL)) AS succeededWindows
  FROM offer_dimensions AS d
  LEFT JOIN exposed_offers AS e
    ON e.anchorOutcome IS NOT DISTINCT FROM d.anchorOutcome AND e.offerId = d.offerId
  LEFT JOIN selected_offers AS s
    ON s.anchorOutcome IS NOT DISTINCT FROM d.anchorOutcome AND s.offerId = d.offerId
  LEFT JOIN attempt_resolution AS a
    ON a.anchorKey = s.anchorKey AND a.transactionId = d.offerId
      AND a.attemptOccurredAtUtc >= s.occurredAtUtc
      AND a.analysisCategory IN ('CommerceShop','CommerceShopLegacy','CommerceIap')
  GROUP BY d.anchorOutcome, d.offerId
), iap_rows AS (
  SELECT anchorOutcome, eventKind AS dimension,
    COUNT(DISTINCT anchorKey) AS eventWindows, COUNT(*) AS eventCount
  FROM iap_windowed WHERE sameContent GROUP BY anchorOutcome, eventKind
)
SELECT
  'funnel' AS rowType, anchorOutcome, 'AllCommerce' AS dimension,
  matureWindows, shopPresentedWindows, shopUserNavigatedWindows,
  offerExposedWindows, offerSelectedWindows, commerceAttemptObservedWindows,
  observedAttemptLinkedSucceededWindows, committedSuccessWindows,
  SAFE_DIVIDE(shopPresentedWindows, matureWindows) AS shopPresentedRate,
  SAFE_DIVIDE(shopUserNavigatedWindows, matureWindows) AS shopUserNavigatedRate,
  SAFE_DIVIDE(offerExposedWindows, shopPresentedWindows) AS shopPresentationToExposure,
  SAFE_DIVIDE(offerSelectedWindows, offerExposedWindows) AS exposureToSelection,
  SAFE_DIVIDE(commerceAttemptObservedWindows, offerSelectedWindows) AS selectionToObservedAttempt,
  CAST(NULL AS INT64) AS observedAttemptCount,
  CAST(NULL AS INT64) AS linkedSucceededAttemptCount,
  CAST(NULL AS FLOAT64) AS observedAttemptSuccessRate,
  CAST(NULL AS INT64) AS blockedAttemptCount,
  CAST(NULL AS INT64) AS cancelledAttemptCount,
  CAST(NULL AS INT64) AS failedAttemptCount,
  CAST(NULL AS INT64) AS duplicateAttemptCount,
  CAST(NULL AS INT64) AS unresolvedAttemptCount,
  CAST(NULL AS INT64) AS committedSuccessResultCount,
  SAFE_DIVIDE(committedSuccessWindows, matureWindows) AS committedSuccessWindowRate,
  CAST(NULL AS INT64) AS committedSuccessWithoutObservedAttemptResults,
  CAST(NULL AS INT64) AS committedSuccessWithoutObservedAttemptWindows,
  CAST(NULL AS INT64) AS exposedWindows, CAST(NULL AS INT64) AS selectedWindows,
  CAST(NULL AS INT64) AS attemptedWindows, CAST(NULL AS INT64) AS succeededWindows,
  CAST(NULL AS INT64) AS eventWindows, CAST(NULL AS INT64) AS eventCount
FROM funnel
UNION ALL
SELECT
  'commerce', c.anchorOutcome, c.dimension,
  m.matureWindows, NULL, NULL, NULL, NULL, NULL, NULL,
  COALESCE(k.committedSuccessWindows, 0), NULL, NULL, NULL, NULL, NULL,
  c.observedAttemptCount, c.linkedSucceededAttemptCount,
  SAFE_DIVIDE(c.linkedSucceededAttemptCount, c.observedAttemptCount),
  c.blockedAttemptCount, c.cancelledAttemptCount, c.failedAttemptCount,
  c.duplicateAttemptCount, c.unresolvedAttemptCount,
  COALESCE(k.committedSuccessResultCount, 0),
  SAFE_DIVIDE(COALESCE(k.committedSuccessWindows, 0), m.matureWindows),
  COALESCE(k.committedSuccessWithoutObservedAttemptResults, 0),
  COALESCE(k.committedSuccessWithoutObservedAttemptWindows, 0),
  NULL, NULL, NULL, NULL, NULL, NULL
FROM commerce AS c
LEFT JOIN committed AS k USING (anchorOutcome, dimension)
JOIN funnel AS m USING (anchorOutcome)
UNION ALL
SELECT
  'offer', anchorOutcome, dimension,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL,
  exposedWindows, selectedWindows, attemptedWindows, succeededWindows, NULL, NULL
FROM offer_rows
UNION ALL
SELECT
  'iapLifecycle', anchorOutcome, dimension,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, eventWindows, eventCount
FROM iap_rows
ORDER BY rowType, anchorOutcome, dimension
