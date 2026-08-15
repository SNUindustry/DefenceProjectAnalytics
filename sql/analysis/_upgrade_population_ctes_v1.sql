final_attempts AS (
  SELECT
    environment, telemetryPlayerId, attemptId, runId AS finalRunId,
    gameplayOutcome, attemptElapsedTimeSeconds, segmentEndedAtUtc,
    appVersion, contentVersion, releaseId, releaseChannel, releaseType,
    isDevelopmentBuild, releaseIdentityResolved, uploadedAtUtc
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_attempt_outcomes_v1`
  WHERE environment = @environment
    AND stageKey = @stage_key
    AND contentVersion = @content_version
    AND (@app_version IS NULL OR appVersion = @app_version)
    AND (@release_id IS NULL OR releaseId = @release_id)
    AND (@release_channel IS NULL OR releaseChannel = @release_channel)
    AND (@release_type IS NULL OR releaseType = @release_type)
    AND (@is_development_build IS NULL OR isDevelopmentBuild = @is_development_build)
    AND (@segment_ended_start_utc IS NULL OR segmentEndedAtUtc >= @segment_ended_start_utc)
    AND (@segment_ended_end_utc IS NULL OR segmentEndedAtUtc < @segment_ended_end_utc)
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
    AND uploadedAtUtc < @analysis_as_of_utc
),
run_summary_rows AS (
  SELECT environment, runId, upgradeExposureOverflowCount, upgradeCandidateOverflowCount
  FROM `<firebase-project-id>.<bigquery-dataset-id>.telemetry_run_summary`
  WHERE environment = @environment
    AND (@uploaded_start_utc IS NULL OR uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR uploadedAtUtc < @uploaded_end_utc)
    AND uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId ORDER BY uploadedAtUtc DESC
  ) = 1
),
candidate_segments AS (
  SELECT
    attempts.environment, attempts.telemetryPlayerId, attempts.attemptId,
    attempts.finalRunId, attempts.gameplayOutcome, attempts.attemptElapsedTimeSeconds,
    segments.runId, segments.uploadId, segments.segmentIndex,
    segments.contentVersion, segments.appVersion, segments.releaseId,
    segments.releaseChannel, segments.releaseType, segments.isDevelopmentBuild,
    segments.uploadedAtUtc,
    summary.upgradeExposureOverflowCount,
    summary.upgradeCandidateOverflowCount
  FROM final_attempts AS attempts
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_gameplay_segments_v1` AS segments
    ON segments.environment = attempts.environment
   AND segments.attemptId = attempts.attemptId
   AND segments.telemetryPlayerId IS NOT DISTINCT FROM attempts.telemetryPlayerId
  LEFT JOIN run_summary_rows AS summary
    ON summary.environment = segments.environment AND summary.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR segments.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR segments.uploadedAtUtc < @uploaded_end_utc)
    AND segments.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY segments.environment, segments.runId
    ORDER BY segments.uploadedAtUtc DESC, segments.segmentIndex DESC
  ) = 1
),
classified_segments AS (
  SELECT
    candidate.environment, candidate.telemetryPlayerId, candidate.attemptId,
    candidate.finalRunId, candidate.gameplayOutcome, candidate.attemptElapsedTimeSeconds,
    candidate.runId, candidate.uploadId, candidate.segmentIndex, candidate.contentVersion,
    candidate.appVersion, candidate.releaseId, candidate.releaseChannel,
    candidate.releaseType, candidate.isDevelopmentBuild, candidate.uploadedAtUtc,
    candidate.upgradeExposureOverflowCount, candidate.upgradeCandidateOverflowCount,
    candidate.contentVersion = @content_version
      AND (@app_version IS NULL OR candidate.appVersion = @app_version)
      AND (@release_id IS NULL OR candidate.releaseId = @release_id)
      AND (@release_channel IS NULL OR candidate.releaseChannel = @release_channel)
      AND (@release_type IS NULL OR candidate.releaseType = @release_type)
      AND (@is_development_build IS NULL OR candidate.isDevelopmentBuild = @is_development_build)
      AS scopeMatches,
    status.telemetryComplete,
    status.completedAtUtc,
    status.telemetryComplete IS TRUE AND status.completedAtUtc < @analysis_as_of_utc AS completeAsOf
  FROM candidate_segments AS candidate
  LEFT JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upload_status_v1` AS status
    ON status.environment = candidate.environment
   AND status.runId = candidate.runId
   AND status.uploadId = candidate.uploadId
),
transport_eligible_segments AS (
  SELECT environment, telemetryPlayerId, attemptId, finalRunId, gameplayOutcome,
    attemptElapsedTimeSeconds, runId, uploadId, segmentIndex, contentVersion,
    appVersion, releaseId, releaseChannel, releaseType, isDevelopmentBuild,
    uploadedAtUtc, upgradeExposureOverflowCount, upgradeCandidateOverflowCount,
    scopeMatches, telemetryComplete, completedAtUtc, completeAsOf
  FROM classified_segments WHERE scopeMatches AND completeAsOf
),
exposure_candidate_rows AS (
  SELECT
    segments.telemetryPlayerId, segments.attemptId, segments.gameplayOutcome,
    segments.attemptElapsedTimeSeconds,
    candidates.environment, candidates.runId, candidates.rowIndex,
    candidates.exposureId, candidates.exposureIndex, candidates.upgradeRequestId,
    candidates.presentationRevision, candidates.requestSource,
    candidates.elapsedTime, candidates.segmentElapsedTime,
    candidates.presentedCandidateCount, candidates.recordedCandidateCount,
    candidates.candidateIndex, candidates.upgradeId, candidates.category,
    candidates.categoryName, candidates.weaponFamilyId, candidates.grantWeaponId,
    candidates.cardType, candidates.weaponSourceType, candidates.upgradeCount,
    candidates.evolutionDepth, candidates.isFallback,
    CASE candidates.category
      WHEN 0 THEN 'NewWeapon' WHEN 1 THEN 'UpgradeWeapon'
      WHEN 2 THEN 'Global' WHEN 3 THEN 'Reward' WHEN 4 THEN 'Evolution'
      ELSE CONCAT('Unrecognized:', CAST(candidates.category AS STRING))
    END AS categoryLabel,
    CASE
      WHEN candidates.category IS NULL OR NULLIF(TRIM(candidates.upgradeId), '') IS NULL THEN NULL
      ELSE CONCAT(
        CASE candidates.category
          WHEN 0 THEN 'NewWeapon' WHEN 1 THEN 'UpgradeWeapon'
          WHEN 2 THEN 'Global' WHEN 3 THEN 'Reward' WHEN 4 THEN 'Evolution'
          ELSE CONCAT('Unrecognized:', CAST(candidates.category AS STRING))
        END,
        ':', TRIM(candidates.upgradeId)
      )
    END AS candidateKey,
    candidates.uploadedAtUtc
  FROM transport_eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upgrade_exposure_candidates` AS candidates
    ON candidates.environment = segments.environment AND candidates.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR candidates.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR candidates.uploadedAtUtc < @uploaded_end_utc)
    AND candidates.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY candidates.environment, candidates.runId, candidates.rowIndex
    ORDER BY candidates.uploadedAtUtc DESC
  ) = 1
),
selection_rows AS (
  SELECT
    segments.telemetryPlayerId, segments.attemptId,
    selections.environment, selections.runId, selections.rowIndex,
    selections.exposureId, selections.exposureIndex, selections.candidateIndex,
    selections.elapsedTime, selections.segmentElapsedTime,
    selections.upgradeId, selections.category, selections.weaponFamilyId,
    selections.grantWeaponId,
    CASE selections.category
      WHEN 0 THEN 'NewWeapon' WHEN 1 THEN 'UpgradeWeapon'
      WHEN 2 THEN 'Global' WHEN 3 THEN 'Reward' WHEN 4 THEN 'Evolution'
      ELSE CONCAT('Unrecognized:', CAST(selections.category AS STRING))
    END AS categoryLabel,
    CASE
      WHEN selections.category IS NULL OR NULLIF(TRIM(selections.upgradeId), '') IS NULL THEN NULL
      ELSE CONCAT(
        CASE selections.category
          WHEN 0 THEN 'NewWeapon' WHEN 1 THEN 'UpgradeWeapon'
          WHEN 2 THEN 'Global' WHEN 3 THEN 'Reward' WHEN 4 THEN 'Evolution'
          ELSE CONCAT('Unrecognized:', CAST(selections.category AS STRING))
        END,
        ':', TRIM(selections.upgradeId)
      )
    END AS candidateKey,
    selections.uploadedAtUtc
  FROM transport_eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_upgrade_selections` AS selections
    ON selections.environment = segments.environment AND selections.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR selections.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR selections.uploadedAtUtc < @uploaded_end_utc)
    AND selections.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY selections.environment, selections.runId, selections.rowIndex
    ORDER BY selections.uploadedAtUtc DESC
  ) = 1
),
snapshot_rows AS (
  SELECT snapshots.environment, snapshots.runId, snapshots.rowIndex,
    snapshots.snapshotIndex, snapshots.reason, snapshots.elapsedTime,
    snapshots.phaseIndex, snapshots.waveNumber, snapshots.floorNumber,
    snapshots.zoneIndex, snapshots.zoneType, snapshots.bossActive,
    snapshots.playerLevel, snapshots.hpRatio, snapshots.ownedWeaponCount,
    snapshots.upgradeSelectionCount, snapshots.uploadedAtUtc
  FROM transport_eligible_segments AS segments
  JOIN `<firebase-project-id>.<bigquery-dataset-id>.telemetry_player_snapshots` AS snapshots
    ON snapshots.environment = segments.environment AND snapshots.runId = segments.runId
  WHERE (@uploaded_start_utc IS NULL OR snapshots.uploadedAtUtc >= @uploaded_start_utc)
    AND (@uploaded_end_utc IS NULL OR snapshots.uploadedAtUtc < @uploaded_end_utc)
    AND snapshots.uploadedAtUtc < @analysis_as_of_utc
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY snapshots.environment, snapshots.runId, snapshots.rowIndex
    ORDER BY snapshots.uploadedAtUtc DESC
  ) = 1
),
run_end_snapshots AS (
  SELECT environment, runId, upgradeSelectionCount
  FROM snapshot_rows
  WHERE reason = 'run_end'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY environment, runId
    ORDER BY elapsedTime DESC, snapshotIndex DESC, rowIndex DESC
  ) = 1
),
exposure_rollup AS (
  SELECT
    environment, runId, exposureId,
    ANY_VALUE(telemetryPlayerId) AS telemetryPlayerId,
    ANY_VALUE(attemptId) AS attemptId,
    MIN(elapsedTime) AS exposureElapsed,
    ANY_VALUE(requestSource) AS requestSource,
    MAX(presentedCandidateCount) AS presentedCandidateCount,
    MAX(recordedCandidateCount) AS recordedCandidateCount,
    COUNT(*) AS candidateRows,
    COUNT(DISTINCT candidateIndex) AS distinctCandidateIndices,
    MIN(candidateIndex) AS minCandidateIndex,
    MAX(candidateIndex) AS maxCandidateIndex,
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(
      exposureIndex, upgradeRequestId, presentationRevision, requestSource,
      elapsedTime, presentedCandidateCount, recordedCandidateCount
    ))) AS headerVariants,
    COUNT(DISTINCT COALESCE(candidateKey, CONCAT('__missing__:', CAST(candidateIndex AS STRING))))
      AS distinctCandidateIdentities,
    MAX(presentedCandidateCount) != MAX(recordedCandidateCount) AS isTruncated,
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(
      exposureIndex, upgradeRequestId, presentationRevision, requestSource,
      elapsedTime, presentedCandidateCount, recordedCandidateCount
    ))) != 1
      OR COUNT(DISTINCT candidateIndex) != COUNT(*)
      OR MIN(candidateIndex) != 0
      OR MAX(candidateIndex) != MAX(recordedCandidateCount) - 1
      OR COUNT(DISTINCT COALESCE(candidateKey, CONCAT('__missing__:', CAST(candidateIndex AS STRING)))) != COUNT(*)
      AS isMalformed
  FROM exposure_candidate_rows
  WHERE NULLIF(TRIM(exposureId), '') IS NOT NULL
  GROUP BY environment, runId, exposureId
),
selection_validation AS (
  SELECT
    selections.telemetryPlayerId, selections.attemptId, selections.environment,
    selections.runId, selections.rowIndex, selections.exposureId,
    selections.exposureIndex, selections.candidateIndex, selections.elapsedTime,
    selections.segmentElapsedTime, selections.upgradeId, selections.category,
    selections.weaponFamilyId, selections.grantWeaponId, selections.categoryLabel,
    selections.candidateKey, selections.uploadedAtUtc,
    COUNTIF(
      candidates.rowIndex IS NOT NULL
      AND candidates.candidateKey IS NOT DISTINCT FROM selections.candidateKey
    ) > 0 AS candidateMatch
  FROM selection_rows AS selections
  LEFT JOIN exposure_candidate_rows AS candidates
    ON candidates.environment = selections.environment
   AND candidates.runId = selections.runId
   AND candidates.exposureId = selections.exposureId
   AND candidates.candidateIndex = selections.candidateIndex
  GROUP BY ALL
),
exposure_selection_rollup AS (
  SELECT
    exposures.environment, exposures.runId, exposures.exposureId,
    exposures.telemetryPlayerId, exposures.attemptId, exposures.exposureElapsed,
    exposures.requestSource, exposures.presentedCandidateCount,
    exposures.recordedCandidateCount, exposures.candidateRows,
    exposures.distinctCandidateIndices, exposures.minCandidateIndex,
    exposures.maxCandidateIndex, exposures.headerVariants,
    exposures.distinctCandidateIdentities, exposures.isTruncated, exposures.isMalformed,
    COUNT(DISTINCT selections.rowIndex) AS selectionCount,
    COUNT(DISTINCT IF(selections.candidateMatch, selections.rowIndex, NULL)) AS validSelectionCount,
    ANY_VALUE(IF(selections.candidateMatch, selections.candidateKey, NULL)) AS selectedCandidateKey,
    ANY_VALUE(IF(selections.candidateMatch, selections.elapsedTime, NULL)) AS selectedSelectionElapsed
  FROM exposure_rollup AS exposures
  LEFT JOIN selection_validation AS selections
    ON selections.environment = exposures.environment
   AND selections.runId = exposures.runId
   AND selections.exposureId = exposures.exposureId
  GROUP BY ALL
),
selection_segment_summary AS (
  SELECT
    segments.environment, segments.runId,
    COUNT(DISTINCT selections.rowIndex) AS observedSelectionRows,
    COUNT(DISTINCT IF(NULLIF(TRIM(selections.exposureId), '') IS NULL, selections.rowIndex, NULL))
      AS legacyUnlinkedSelections,
    COUNT(DISTINCT IF(
      NULLIF(TRIM(selections.exposureId), '') IS NOT NULL AND NOT selections.candidateMatch,
      selections.rowIndex, NULL
    )) AS selectionsWithoutExposureMatch
  FROM transport_eligible_segments AS segments
  LEFT JOIN selection_validation AS selections
    ON selections.environment = segments.environment AND selections.runId = segments.runId
  GROUP BY segments.environment, segments.runId
),
exposure_segment_quality_rows AS (
  SELECT environment, runId,
    COUNTIF(isTruncated) AS truncatedExposures,
    COUNTIF(isMalformed) AS malformedExposures,
    COUNTIF(selectionCount = 0) AS noSelectionExposures,
    COUNTIF(selectionCount > 1) AS multipleSelectionExposures,
    COUNTIF(selectionCount = 1 AND validSelectionCount != 1) AS invalidResolvedExposures
  FROM exposure_selection_rollup
  GROUP BY environment, runId
),
blank_exposure_segment_rows AS (
  SELECT environment, runId, COUNT(*) AS blankExposureCandidateRows
  FROM exposure_candidate_rows
  WHERE NULLIF(TRIM(exposureId), '') IS NULL
  GROUP BY environment, runId
),
exposure_segment_summary AS (
  SELECT segments.environment, segments.runId,
    COALESCE(quality.truncatedExposures, 0) AS truncatedExposures,
    COALESCE(quality.malformedExposures, 0) AS malformedExposures,
    COALESCE(quality.noSelectionExposures, 0) AS noSelectionExposures,
    COALESCE(quality.multipleSelectionExposures, 0) AS multipleSelectionExposures,
    COALESCE(quality.invalidResolvedExposures, 0) AS invalidResolvedExposures,
    COALESCE(blank.blankExposureCandidateRows, 0) AS blankExposureCandidateRows
  FROM transport_eligible_segments AS segments
  LEFT JOIN exposure_segment_quality_rows AS quality
    ON quality.environment = segments.environment AND quality.runId = segments.runId
  LEFT JOIN blank_exposure_segment_rows AS blank
    ON blank.environment = segments.environment AND blank.runId = segments.runId
),
choice_segment_assessment AS (
  SELECT
    segments.environment, segments.telemetryPlayerId, segments.attemptId,
    segments.finalRunId, segments.gameplayOutcome, segments.attemptElapsedTimeSeconds,
    segments.runId, segments.uploadId, segments.segmentIndex, segments.contentVersion,
    segments.appVersion, segments.releaseId, segments.releaseChannel,
    segments.releaseType, segments.isDevelopmentBuild, segments.uploadedAtUtc,
    segments.upgradeExposureOverflowCount, segments.upgradeCandidateOverflowCount,
    run_end.upgradeSelectionCount AS expectedSegmentSelectionCount,
    COALESCE(selection_quality.observedSelectionRows, 0) AS observedSegmentSelectionCount,
    run_end.upgradeSelectionCount IS NOT NULL AS selectionCountAssessed,
    run_end.upgradeSelectionCount = COALESCE(selection_quality.observedSelectionRows, 0)
      AS selectionCountMatches,
    COALESCE(exposure_quality.truncatedExposures, 0) AS truncatedExposures,
    COALESCE(exposure_quality.malformedExposures, 0) AS malformedExposures,
    COALESCE(exposure_quality.noSelectionExposures, 0) AS noSelectionExposures,
    COALESCE(exposure_quality.multipleSelectionExposures, 0) AS multipleSelectionExposures,
    COALESCE(selection_quality.legacyUnlinkedSelections, 0) AS legacyUnlinkedSelections,
    COALESCE(selection_quality.selectionsWithoutExposureMatch, 0)
      + COALESCE(exposure_quality.invalidResolvedExposures, 0) AS selectionsWithoutExposureMatch,
    segments.upgradeExposureOverflowCount = 0
      AND segments.upgradeCandidateOverflowCount = 0
      AND run_end.upgradeSelectionCount IS NOT NULL
      AND run_end.upgradeSelectionCount = COALESCE(selection_quality.observedSelectionRows, 0)
      AND COALESCE(exposure_quality.truncatedExposures, 0) = 0
      AND COALESCE(exposure_quality.malformedExposures, 0) = 0
      AND COALESCE(exposure_quality.multipleSelectionExposures, 0) = 0
      AND COALESCE(exposure_quality.blankExposureCandidateRows, 0) = 0
      AND COALESCE(selection_quality.legacyUnlinkedSelections, 0) = 0
      AND COALESCE(selection_quality.selectionsWithoutExposureMatch, 0) = 0
      AND COALESCE(exposure_quality.invalidResolvedExposures, 0) = 0
      AS choiceEligible
  FROM transport_eligible_segments AS segments
  LEFT JOIN run_end_snapshots AS run_end
    ON run_end.environment = segments.environment AND run_end.runId = segments.runId
  LEFT JOIN selection_segment_summary AS selection_quality
    ON selection_quality.environment = segments.environment
   AND selection_quality.runId = segments.runId
  LEFT JOIN exposure_segment_summary AS exposure_quality
    ON exposure_quality.environment = segments.environment
   AND exposure_quality.runId = segments.runId
),
choice_eligible_segments AS (
  SELECT environment, telemetryPlayerId, attemptId, runId, segmentIndex,
    gameplayOutcome, attemptElapsedTimeSeconds
  FROM choice_segment_assessment WHERE choiceEligible
),
complete_exposures AS (
  SELECT exposures.environment, exposures.runId, exposures.exposureId,
    exposures.telemetryPlayerId, exposures.attemptId, exposures.exposureElapsed,
    exposures.requestSource, exposures.presentedCandidateCount,
    exposures.recordedCandidateCount, exposures.distinctCandidateIndices,
    exposures.isTruncated, exposures.isMalformed, exposures.selectionCount,
    exposures.validSelectionCount, exposures.selectedCandidateKey,
    exposures.selectedSelectionElapsed
  FROM exposure_selection_rollup AS exposures
  JOIN choice_eligible_segments AS segments
    ON segments.environment = exposures.environment AND segments.runId = exposures.runId
  WHERE NOT exposures.isTruncated
    AND NOT exposures.isMalformed
    AND exposures.presentedCandidateCount = exposures.recordedCandidateCount
    AND exposures.recordedCandidateCount = exposures.distinctCandidateIndices
    AND exposures.recordedCandidateCount > 0
),
complete_exposure_candidates AS (
  SELECT candidates.telemetryPlayerId, candidates.attemptId, candidates.gameplayOutcome,
    candidates.attemptElapsedTimeSeconds, candidates.environment, candidates.runId,
    candidates.exposureId, candidates.exposureIndex, candidates.upgradeRequestId,
    candidates.presentationRevision, candidates.requestSource, candidates.elapsedTime,
    candidates.segmentElapsedTime, candidates.presentedCandidateCount,
    candidates.recordedCandidateCount, candidates.candidateIndex, candidates.upgradeId,
    candidates.category, candidates.categoryName, candidates.weaponFamilyId,
    candidates.grantWeaponId, candidates.cardType, candidates.weaponSourceType,
    candidates.upgradeCount, candidates.evolutionDepth, candidates.isFallback,
    candidates.categoryLabel, candidates.candidateKey,
    exposures.exposureElapsed, exposures.selectionCount,
    exposures.validSelectionCount, exposures.selectedCandidateKey,
    exposures.selectedSelectionElapsed
  FROM exposure_candidate_rows AS candidates
  JOIN complete_exposures AS exposures
    ON exposures.environment = candidates.environment
   AND exposures.runId = candidates.runId
   AND exposures.exposureId = candidates.exposureId
),
attempt_choice_coverage AS (
  SELECT
    classified.environment, classified.telemetryPlayerId, classified.attemptId,
    COUNT(*) AS candidateSegments,
    COUNTIF(assessment.choiceEligible) AS choiceEligibleSegments
  FROM classified_segments AS classified
  LEFT JOIN choice_segment_assessment AS assessment
    ON assessment.environment = classified.environment AND assessment.runId = classified.runId
  GROUP BY classified.environment, classified.telemetryPlayerId, classified.attemptId
),
fully_choice_covered_attempts AS (
  SELECT environment, telemetryPlayerId, attemptId
  FROM attempt_choice_coverage
  WHERE candidateSegments > 0 AND candidateSegments = choiceEligibleSegments
)
