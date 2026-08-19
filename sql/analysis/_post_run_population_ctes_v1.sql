anchors AS (
  SELECT
    TO_JSON_STRING(STRUCT(environment, telemetryPlayerId, attemptId, runId)) AS anchorKey,
    environment, telemetryPlayerId, attemptId, runId, stageKey, gameplayOutcome,
    abandonReason, segmentKind, segmentEndedAtUtc, attemptElapsedTimeSeconds,
    appVersion, contentVersion, releaseId, releaseChannel, releaseType,
    isDevelopmentBuild, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment
    AND contentVersion = @content_version
    AND uploadedAtUtc < @analysis_as_of_utc
    AND (@stage_key IS NULL OR stageKey = @stage_key)
    AND (@final_outcome IS NULL OR gameplayOutcome = @final_outcome)
    AND (@app_version IS NULL OR appVersion = @app_version)
    AND (@release_id IS NULL OR releaseId = @release_id)
    AND (@release_channel IS NULL OR releaseChannel = @release_channel)
    AND (@release_type IS NULL OR releaseType = @release_type)
    AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
    AND (@run_ended_start_utc IS NULL OR segmentEndedAtUtc >= @run_ended_start_utc)
    AND (@run_ended_end_utc IS NULL OR segmentEndedAtUtc < @run_ended_end_utc)
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
),
run_start_deduped AS (
  SELECT environment, runId, isFromResume
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_start_snapshot`
  WHERE environment = @environment AND uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId
    ORDER BY uploadedAtUtc DESC, COALESCE(rowIndex, -1) DESC
  ) = 1
),
new_attempt_segments AS (
  SELECT
    g.environment, g.telemetryPlayerId, g.attemptId, g.runId, g.stageKey,
    g.segmentStartedAtUtc, g.contentVersion, g.releaseId
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_gameplay_segments_v1` AS g
  LEFT JOIN run_start_deduped AS s
    ON s.environment = g.environment AND s.runId = g.runId
  WHERE g.environment = @environment
    AND g.uploadedAtUtc < @analysis_as_of_utc
    AND g.segmentStartedAtUtc < @analysis_as_of_utc
    AND g.segmentIndex = 1
    AND NULLIF(TRIM(g.attemptId), '') IS NOT NULL
    AND s.isFromResume IS NOT TRUE
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY g.environment, g.telemetryPlayerId, g.attemptId
    ORDER BY g.uploadedAtUtc DESC, g.runId DESC
  ) = 1
),
next_candidates AS (
  SELECT
    a.anchorKey,
    n.attemptId AS nextAttemptId,
    n.runId AS nextRunId,
    n.stageKey AS nextStageKey,
    n.segmentStartedAtUtc AS nextStartedAtUtc,
    n.contentVersion AS nextContentVersion,
    n.releaseId AS nextReleaseId
  FROM anchors AS a
  JOIN new_attempt_segments AS n
    ON n.environment = a.environment
   AND n.telemetryPlayerId IS NOT DISTINCT FROM a.telemetryPlayerId
   AND n.segmentStartedAtUtc > a.segmentEndedAtUtc
  WHERE NULLIF(TRIM(a.telemetryPlayerId), '') IS NOT NULL
    AND a.segmentEndedAtUtc IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY a.anchorKey
    ORDER BY n.segmentStartedAtUtc, n.attemptId, n.runId
  ) = 1
),
next_outcomes AS (
  SELECT environment, attemptId, gameplayOutcome, attemptElapsedTimeSeconds
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment AND uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, telemetryPlayerId, attemptId
    ORDER BY uploadedAtUtc DESC, runId DESC
  ) = 1
),
windows AS (
  SELECT
    a.anchorKey, a.environment, a.telemetryPlayerId, a.attemptId, a.runId,
    a.stageKey, a.gameplayOutcome, a.abandonReason, a.segmentKind,
    a.segmentEndedAtUtc, a.attemptElapsedTimeSeconds, a.appVersion,
    a.contentVersion, a.releaseId, a.releaseChannel, a.releaseType,
    a.isDevelopmentBuild,
    n.nextAttemptId, n.nextRunId, n.nextStageKey, n.nextStartedAtUtc,
    n.nextContentVersion, n.nextReleaseId,
    o.gameplayOutcome AS nextOutcome,
    o.attemptElapsedTimeSeconds AS nextAttemptElapsedTimeSeconds,
    NULLIF(TRIM(a.telemetryPlayerId), '') IS NOT NULL AND a.segmentEndedAtUtc IS NOT NULL
      AS linkageEligible,
    n.nextStartedAtUtc IS NOT NULL
      AND n.nextStartedAtUtc <= TIMESTAMP_ADD(
        a.segmentEndedAtUtc, INTERVAL @post_run_max_gap_minutes MINUTE
      ) AS resolvedByNextRunWithinWindow,
    n.nextStartedAtUtc IS NOT NULL
      AND n.nextStartedAtUtc > TIMESTAMP_ADD(
        a.segmentEndedAtUtc, INTERVAL @post_run_max_gap_minutes MINUTE
      ) AS laterNextRunOutsideWindow,
    CASE
      WHEN n.nextStartedAtUtc IS NOT NULL
        AND n.nextStartedAtUtc <= TIMESTAMP_ADD(
          a.segmentEndedAtUtc, INTERVAL @post_run_max_gap_minutes MINUTE
        ) THEN n.nextStartedAtUtc
      ELSE TIMESTAMP_ADD(a.segmentEndedAtUtc, INTERVAL @post_run_max_gap_minutes MINUTE)
    END AS windowEndedAtUtc,
    TIMESTAMP_DIFF(n.nextStartedAtUtc, a.segmentEndedAtUtc, SECOND) AS timeToNextRunSeconds
  FROM anchors AS a
  LEFT JOIN next_candidates AS n USING (anchorKey)
  LEFT JOIN next_outcomes AS o
    ON o.environment = a.environment AND o.attemptId = n.nextAttemptId
),
classified_windows AS (
  SELECT
    w.anchorKey, w.environment, w.telemetryPlayerId, w.attemptId, w.runId,
    w.stageKey, w.gameplayOutcome, w.abandonReason, w.segmentKind,
    w.segmentEndedAtUtc, w.attemptElapsedTimeSeconds, w.appVersion,
    w.contentVersion, w.releaseId, w.releaseChannel, w.releaseType,
    w.isDevelopmentBuild, w.nextAttemptId, w.nextRunId, w.nextStageKey,
    w.nextStartedAtUtc, w.nextContentVersion, w.nextReleaseId, w.nextOutcome,
    w.nextAttemptElapsedTimeSeconds, w.linkageEligible,
    w.resolvedByNextRunWithinWindow, w.laterNextRunOutsideWindow,
    w.windowEndedAtUtc, w.timeToNextRunSeconds,
    linkageEligible AND (
      resolvedByNextRunWithinWindow
      OR segmentEndedAtUtc <= TIMESTAMP_SUB(
        @analysis_as_of_utc, INTERVAL @post_run_max_gap_minutes MINUTE
      )
    ) AS matureWindow,
    linkageEligible AND NOT resolvedByNextRunWithinWindow
      AND segmentEndedAtUtc > TIMESTAMP_SUB(
        @analysis_as_of_utc, INTERVAL @post_run_max_gap_minutes MINUTE
      ) AS rightCensored,
    linkageEligible AND NOT resolvedByNextRunWithinWindow
      AND segmentEndedAtUtc <= TIMESTAMP_SUB(
        @analysis_as_of_utc, INTERVAL @post_run_max_gap_minutes MINUTE
      ) AS matureNoNextRunWithinWindow,
    resolvedByNextRunWithinWindow AND nextOutcome IS NULL AS nextOutcomePending,
    resolvedByNextRunWithinWindow AND nextStageKey = stageKey AS sameStageRetry,
    resolvedByNextRunWithinWindow AND nextContentVersion = @content_version AS sameContentNextRun
  FROM windows AS w
),
lobby_physical AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind, tab, shopSection,
    navigationSource, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_lobby_activity_events`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND occurredAtUtc < @analysis_as_of_utc
),
lobby_deduped AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind, tab, shopSection, navigationSource
  FROM lobby_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, eventId
    ORDER BY uploadedAtUtc DESC, batchId DESC, rowIndex DESC
  ) = 1
),
shop_physical AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind, presentationId, tab,
    shopSection, offerId, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_shop_events`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND occurredAtUtc < @analysis_as_of_utc
),
shop_deduped AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind, presentationId, tab,
    shopSection, offerId
  FROM shop_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, eventId
    ORDER BY uploadedAtUtc DESC, batchId DESC, rowIndex DESC
  ) = 1
),
shop_offer_physical AS (
  SELECT environment, parentEventId, rowIndex, offerId, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_shop_exposure_offers`
  WHERE environment = @environment AND uploadedAtUtc < @analysis_as_of_utc
),
shop_offer_deduped AS (
  SELECT environment, parentEventId, rowIndex, offerId
  FROM shop_offer_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, parentEventId, rowIndex
    ORDER BY uploadedAtUtc DESC
  ) = 1
),
transaction_physical AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind, resultCategory, transactionId,
    transactionKind, pipelineKind, sourceCategory, operationId, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_transaction_events`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND occurredAtUtc < @analysis_as_of_utc
),
transaction_deduped AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind, resultCategory, transactionId,
    transactionKind, pipelineKind, sourceCategory, operationId,
    CASE
      WHEN sourceCategory = 'FunFeedback'
        AND transactionId = 'system.fun-feedback.uranium.2' THEN 'OtherSystemReward'
      WHEN sourceCategory IN ('WeaponRecipe', 'Evolution', 'StatReset') THEN 'ProgressionSpend'
      WHEN sourceCategory = 'RandomBox' THEN 'RandomBoxSpend'
      WHEN sourceCategory = 'IAP' OR transactionKind = 'Iap' THEN 'CommerceIap'
      WHEN sourceCategory = 'Shop' THEN 'CommerceShop'
      WHEN sourceCategory = 'Other' AND transactionKind IN ('Shop', 'Package')
        THEN 'CommerceShopLegacy'
      WHEN sourceCategory = 'System' OR transactionKind = 'SystemReward'
        THEN 'OtherSystemReward'
      ELSE 'Other'
    END AS analysisCategory
  FROM transaction_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, eventId
    ORDER BY uploadedAtUtc DESC, batchId DESC, rowIndex DESC
  ) = 1
),
progression_physical AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, progressionKind, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_progression_events`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND occurredAtUtc < @analysis_as_of_utc
),
progression_deduped AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, progressionKind
  FROM progression_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, eventId
    ORDER BY uploadedAtUtc DESC, batchId DESC, rowIndex DESC
  ) = 1
),
iap_physical AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_iap_events`
  WHERE environment = @environment
    AND uploadedAtUtc < @analysis_as_of_utc
    AND occurredAtUtc < @analysis_as_of_utc
),
iap_deduped AS (
  SELECT environment, telemetryPlayerId, contentVersion, releaseId, batchId,
    eventId, occurredAtUtc, rowIndex, eventKind
  FROM iap_physical
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, eventId
    ORDER BY uploadedAtUtc DESC, batchId DESC, rowIndex DESC
  ) = 1
),
lobby_windowed AS (
  SELECT w.anchorKey, w.gameplayOutcome AS anchorOutcome, l.environment,
    l.telemetryPlayerId, l.contentVersion, l.releaseId, l.batchId, l.eventId,
    l.occurredAtUtc, l.rowIndex, l.eventKind, l.tab, l.shopSection,
    l.navigationSource,
    l.contentVersion = @content_version AS sameContent,
    l.releaseId IS NOT DISTINCT FROM w.releaseId AS sameRelease
  FROM classified_windows AS w
  JOIN lobby_deduped AS l
    ON l.environment = w.environment
   AND l.telemetryPlayerId IS NOT DISTINCT FROM w.telemetryPlayerId
   AND l.occurredAtUtc >= w.segmentEndedAtUtc
   AND l.occurredAtUtc < w.windowEndedAtUtc
  WHERE w.linkageEligible
),
shop_windowed AS (
  SELECT w.anchorKey, w.gameplayOutcome AS anchorOutcome, s.environment,
    s.telemetryPlayerId, s.contentVersion, s.releaseId, s.batchId, s.eventId,
    s.occurredAtUtc, s.rowIndex, s.eventKind, s.presentationId, s.tab,
    s.shopSection, s.offerId,
    s.contentVersion = @content_version AS sameContent,
    s.releaseId IS NOT DISTINCT FROM w.releaseId AS sameRelease
  FROM classified_windows AS w
  JOIN shop_deduped AS s
    ON s.environment = w.environment
   AND s.telemetryPlayerId IS NOT DISTINCT FROM w.telemetryPlayerId
   AND s.occurredAtUtc >= w.segmentEndedAtUtc
   AND s.occurredAtUtc < w.windowEndedAtUtc
  WHERE w.linkageEligible
),
transaction_windowed AS (
  SELECT w.anchorKey, w.gameplayOutcome AS anchorOutcome, t.environment,
    t.telemetryPlayerId, t.contentVersion, t.releaseId, t.batchId, t.eventId,
    t.occurredAtUtc, t.rowIndex, t.eventKind, t.resultCategory, t.transactionId,
    t.transactionKind, t.pipelineKind, t.sourceCategory, t.operationId,
    t.analysisCategory,
    t.contentVersion = @content_version AS sameContent,
    t.releaseId IS NOT DISTINCT FROM w.releaseId AS sameRelease
  FROM classified_windows AS w
  JOIN transaction_deduped AS t
    ON t.environment = w.environment
   AND t.telemetryPlayerId IS NOT DISTINCT FROM w.telemetryPlayerId
   AND t.occurredAtUtc >= w.segmentEndedAtUtc
   AND t.occurredAtUtc < w.windowEndedAtUtc
  WHERE w.linkageEligible
),
progression_windowed AS (
  SELECT w.anchorKey, w.gameplayOutcome AS anchorOutcome, p.environment,
    p.telemetryPlayerId, p.contentVersion, p.releaseId, p.batchId, p.eventId,
    p.occurredAtUtc, p.rowIndex, p.progressionKind,
    p.contentVersion = @content_version AS sameContent,
    p.releaseId IS NOT DISTINCT FROM w.releaseId AS sameRelease,
    TIMESTAMP_DIFF(p.occurredAtUtc, w.segmentEndedAtUtc, SECOND) AS timeToProgressionSeconds
  FROM classified_windows AS w
  JOIN progression_deduped AS p
    ON p.environment = w.environment
   AND p.telemetryPlayerId IS NOT DISTINCT FROM w.telemetryPlayerId
   AND p.occurredAtUtc >= w.segmentEndedAtUtc
   AND p.occurredAtUtc < w.windowEndedAtUtc
  WHERE w.linkageEligible
),
iap_windowed AS (
  SELECT w.anchorKey, w.gameplayOutcome AS anchorOutcome, i.environment,
    i.telemetryPlayerId, i.contentVersion, i.releaseId, i.batchId, i.eventId,
    i.occurredAtUtc, i.rowIndex, i.eventKind,
    i.contentVersion = @content_version AS sameContent,
    i.releaseId IS NOT DISTINCT FROM w.releaseId AS sameRelease
  FROM classified_windows AS w
  JOIN iap_deduped AS i
    ON i.environment = w.environment
   AND i.telemetryPlayerId IS NOT DISTINCT FROM w.telemetryPlayerId
   AND i.occurredAtUtc >= w.segmentEndedAtUtc
   AND i.occurredAtUtc < w.windowEndedAtUtc
  WHERE w.linkageEligible
),
transaction_attempts AS (
  SELECT anchorKey, anchorOutcome, environment, telemetryPlayerId, contentVersion,
    releaseId, batchId, eventId, occurredAtUtc, rowIndex, eventKind, resultCategory,
    transactionId, transactionKind, pipelineKind, sourceCategory, operationId,
    analysisCategory, sameContent, sameRelease
  FROM transaction_windowed
  WHERE eventKind = 'Attempt'
),
transaction_results AS (
  SELECT anchorKey, anchorOutcome, environment, telemetryPlayerId, contentVersion,
    releaseId, batchId, eventId, occurredAtUtc, rowIndex, eventKind, resultCategory,
    transactionId, transactionKind, pipelineKind, sourceCategory, operationId,
    analysisCategory, sameContent, sameRelease
  FROM transaction_windowed
  WHERE eventKind = 'Result'
),
attempt_resolution AS (
  SELECT
    a.anchorKey, a.anchorOutcome, a.eventId AS attemptEventId, a.operationId,
    a.transactionId, a.analysisCategory, a.occurredAtUtc AS attemptOccurredAtUtc,
    r.eventId AS resultEventId, r.resultCategory,
    r.occurredAtUtc AS resultOccurredAtUtc
  FROM transaction_attempts AS a
  LEFT JOIN transaction_results AS r
    ON r.anchorKey = a.anchorKey
   AND NULLIF(TRIM(r.operationId), '') = NULLIF(TRIM(a.operationId), '')
   AND r.occurredAtUtc >= a.occurredAtUtc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY a.anchorKey, a.eventId
    ORDER BY r.occurredAtUtc DESC, r.eventId DESC
  ) = 1
),
result_observation AS (
  SELECT
    r.anchorKey, r.anchorOutcome, r.environment, r.telemetryPlayerId,
    r.contentVersion, r.releaseId, r.batchId, r.eventId, r.occurredAtUtc,
    r.rowIndex, r.eventKind, r.resultCategory, r.transactionId,
    r.transactionKind, r.pipelineKind, r.sourceCategory, r.operationId,
    r.analysisCategory, r.sameContent, r.sameRelease,
    EXISTS (
      SELECT 1 FROM transaction_attempts AS a
      WHERE a.anchorKey = r.anchorKey
        AND NULLIF(TRIM(a.operationId), '') = NULLIF(TRIM(r.operationId), '')
        AND a.occurredAtUtc <= r.occurredAtUtc
    ) AS hasObservedAttempt
  FROM transaction_results AS r
),
normalized_actions AS (
  SELECT anchorKey, occurredAtUtc, IF(eventKind = 'TabViewed', 20, 30), rowIndex, eventId,
    CASE WHEN eventKind = 'TabViewed' THEN CONCAT('TabViewed', COALESCE(tab, 'Unknown'))
      WHEN eventKind = 'ShopSectionViewed' THEN CONCAT('ShopSection', COALESCE(shopSection, 'Unknown'))
      ELSE CONCAT('Unrecognized:', eventKind) END,
    CASE WHEN eventKind = 'TabViewed' AND navigationSource = 'User' THEN
      CASE tab WHEN 'Shop' THEN 'Shop' WHEN 'Equipment' THEN 'Equipment'
        WHEN 'RandomBox' THEN 'RandomBox' WHEN 'Evolution' THEN 'Evolution'
        WHEN 'Battle' THEN 'Battle' ELSE 'Other' END ELSE NULL END,
    sameContent, sameRelease
  FROM lobby_windowed
  UNION ALL
  SELECT anchorKey, occurredAtUtc, IF(eventKind = 'OfferExposure', 40, 50), rowIndex, eventId,
    CASE WHEN eventKind = 'OfferExposure' THEN 'OfferExposure'
      WHEN eventKind = 'OfferSelected' THEN 'OfferSelected'
      ELSE CONCAT('Unrecognized:', eventKind) END,
    IF(eventKind = 'OfferSelected', 'OfferSelected', NULL), sameContent, sameRelease
  FROM shop_windowed
  UNION ALL
  SELECT anchorKey, occurredAtUtc, IF(eventKind = 'Attempt', 60, 70), rowIndex, eventId,
    CASE WHEN eventKind = 'Attempt' THEN 'TransactionAttempt'
      WHEN eventKind = 'Result' THEN CONCAT('Transaction', COALESCE(resultCategory, 'Unknown'))
      ELSE CONCAT('Unrecognized:', eventKind) END,
    CASE WHEN eventKind != 'Attempt' THEN NULL
      WHEN analysisCategory IN ('CommerceShop', 'CommerceShopLegacy', 'CommerceIap') THEN 'CommerceAttempt'
      WHEN analysisCategory = 'ProgressionSpend' THEN 'Progression'
      WHEN analysisCategory = 'RandomBoxSpend' THEN 'RandomBox'
      WHEN analysisCategory = 'OtherSystemReward' THEN NULL
      ELSE 'Other' END,
    sameContent, sameRelease
  FROM transaction_windowed
  UNION ALL
  SELECT anchorKey, occurredAtUtc, 90, rowIndex, eventId, 'Progression', 'Progression',
    sameContent, sameRelease
  FROM progression_windowed
)
