"""Explicit v1 source and evidence registry.

Nothing in this module discovers metrics from arbitrary numeric columns.  Adding a
new source metric therefore requires an intentional contract change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from defence_project_analytics.metric_registry import (
    COMPARISON_TABLE_SPECS,
    RETENTION_EVIDENCE_METRIC_SPECS,
    evidence_metric_keys,
)


ANALYSIS_TO_DOMAIN = {
    "stageDifficulty": "stageDifficulty",
    "weaponPerformance": "weaponPerformance",
    "upgradeChoice": "upgradeChoice",
    "progressionNextRun": "progressionNextRun",
    "postRunBehavior": "postRunBehavior",
    "runRetention": "runRetention",
    "observedAppReturn": "observedAppReturn",
    "gaIdentityBridge": "gaIdentityBridge",
    "observedUninstall": "observedUninstall",
    "retentionEvidence": "retentionEvidence",
    "contentVersionCompare": "contentVersionCompare",
}
SINGLE_ANALYSIS_TYPES = frozenset(ANALYSIS_TO_DOMAIN) - {"contentVersionCompare"}
STAGE_ANALYSIS_TYPES = frozenset(
    ("stageDifficulty", "weaponPerformance", "upgradeChoice")
)
HISTORICAL_ONLY_WARNING_CODES = frozenset({
    "FEEDBACK_LINK_MISMATCH",
    "LOW_FEEDBACK_SAMPLE",
    "FUN_FEEDBACK_REWARD_EXCLUDED_FROM_COMMERCE",
})


def active_source_warning_codes(warnings: object) -> tuple[str, ...]:
    if not isinstance(warnings, (list, tuple)):
        return ()
    return tuple(
        str(item["code"])
        for item in warnings
        if isinstance(item, Mapping)
        and item.get("code")
        and item["code"] not in HISTORICAL_ONLY_WARNING_CODES
    )


@dataclass(frozen=True, slots=True)
class SummaryMetricSpec:
    family: str
    metric: str
    path: str
    value_type: str
    unit: str
    observation_unit: str
    core: bool = False


@dataclass(frozen=True, slots=True)
class TableMetricSpec:
    metric: str
    value_type: str
    value_column: str
    count_column: str | None = None
    denominator_column: str | None = None
    unit: str = "count"
    core: bool = False


@dataclass(frozen=True, slots=True)
class TableEvidenceSpec:
    family: str
    keys: tuple[str, ...]
    entity_type: str
    observation_unit: str
    metrics: tuple[TableMetricSpec, ...]


SUMMARY_METRICS: Mapping[str, tuple[SummaryMetricSpec, ...]] = {
    "stageDifficulty": (
        SummaryMetricSpec("outcome", "finalAttempts", "outcome.finalAttempts", "scalar", "attempts", "finalAttempts", True),
        SummaryMetricSpec("outcome", "uniquePlayers", "outcome.uniquePlayers", "scalar", "players", "telemetryPlayers"),
        SummaryMetricSpec("outcome", "clears", "outcome.clears", "scalar", "attempts", "finalAttempts"),
        SummaryMetricSpec("outcome", "deaths", "outcome.deaths", "scalar", "attempts", "finalAttempts"),
        SummaryMetricSpec("outcome", "abandons", "outcome.abandons", "scalar", "attempts", "finalAttempts"),
        SummaryMetricSpec("outcome", "clearRate", "outcome.clearRate", "ratio", "ratio", "clearOrDeadFinalAttempts", True),
        SummaryMetricSpec("survival", "medianAttemptElapsedSeconds", "survival", "distribution", "seconds", "finalAttempts", True),
        SummaryMetricSpec("dataQuality", "telemetryCompleteRate", "dataQuality.telemetryCompleteRate", "ratio", "ratio", "finalAttempts"),
        SummaryMetricSpec("dataQuality", "detailCoverageRate", "dataQuality.detailCoverageRate", "ratio", "ratio", "finalAttempts"),
    ),
    "weaponPerformance": (
        SummaryMetricSpec("sample", "finalAttempts", "sample.finalAttempts", "scalar", "attempts", "finalAttempts", True),
        SummaryMetricSpec("sample", "detailEligibleAttempts", "sample.detailEligibleAttempts", "scalar", "attempts", "fullyCoveredAttempts", True),
        SummaryMetricSpec("combat", "totalDamage", "combatPerformance.totalDamage", "scalar", "damage", "eligibleSegments", True),
        SummaryMetricSpec("combat", "totalBossDamage", "combatPerformance.totalBossDamage", "scalar", "damage", "eligibleSegments"),
        SummaryMetricSpec("dps", "validSampleCount", "dps.validSampleCount", "scalar", "samples", "validDpsSamples"),
        SummaryMetricSpec("dps", "dpsCoverageAmongCombatObservedInstanceSegments", "dps.coverage", "ratio", "ratio", "combatObservedInstanceSegments", True),
        SummaryMetricSpec("dataQuality", "detailCoverageRate", "dataQuality.detailCoverageRate", "ratio", "ratio", "finalAttempts"),
    ),
    "upgradeChoice": (
        SummaryMetricSpec("sample", "finalAttempts", "sample.finalAttempts", "scalar", "attempts", "finalAttempts", True),
        SummaryMetricSpec("exposure", "completeExposures", "exposure.completeExposures", "scalar", "exposures", "completeExposures", True),
        SummaryMetricSpec("selection", "linkedSelections", "selection.linkedSelections", "scalar", "selections", "linkedSelections", True),
        SummaryMetricSpec("selection", "noSelectionExposures", "selection.noSelectionExposures", "scalar", "exposures", "completeExposures"),
        SummaryMetricSpec("dataQuality", "choiceCoverageRate", "dataQuality.choiceCoverageRate", "ratio", "ratio", "candidateSegments"),
        SummaryMetricSpec("dataQuality", "attemptChoiceCoverageRate", "dataQuality.attemptChoiceCoverageRate", "ratio", "ratio", "finalAttempts"),
    ),
    "progressionNextRun": (
        SummaryMetricSpec("activity", "progressionEvents", "progressionActivity.progressionEvents", "scalar", "events", "progressionEvents", True),
        SummaryMetricSpec("episodes", "boundedEpisodes", "episodes.boundedEpisodes", "scalar", "episodes", "boundedEpisodes", True),
        SummaryMetricSpec("episodes", "singleProgressionEpisodes", "episodes.singleProgressionEpisodes", "scalar", "episodes", "boundedEpisodes"),
        SummaryMetricSpec("episodes", "multiProgressionEpisodes", "episodes.multiProgressionEpisodes", "scalar", "episodes", "boundedEpisodes"),
        SummaryMetricSpec("nextRun", "nextRunWithinWindow", "nextRunEngagement.nextRunWithinWindow", "ratio", "ratio", "matureEpisodes", True),
        SummaryMetricSpec("nextRun", "sameStageRetry", "nextRunEngagement.sameStageRetry", "ratio", "ratio", "linkedEpisodes"),
        SummaryMetricSpec("pairedOutcome", "previousClearRate", "pairedOutcome.previousClearRate", "ratio", "ratio", "pairedAttempts"),
        SummaryMetricSpec("pairedOutcome", "nextClearRate", "pairedOutcome.nextClearRate", "ratio", "ratio", "pairedAttempts"),
        SummaryMetricSpec("pairedElapsed", "deltaMedianSeconds", "pairedElapsed.deltaMedianSeconds", "scalar", "seconds", "pairedAttempts"),
    ),
    "postRunBehavior": (
        SummaryMetricSpec("sample", "anchorFinalRuns", "sample.anchorFinalRuns", "scalar", "windows", "finalAttemptWindows", True),
        SummaryMetricSpec("window", "matureWindows", "window.matureWindows", "scalar", "windows", "matureWindows", True),
        SummaryMetricSpec("feedback", "positiveResponseRate", "feedback.positiveResponseRate", "ratio", "ratio", "feedbackResponses"),
        SummaryMetricSpec("navigation", "shopPresentedRate", "navigation.shopPresentedRate", "ratio", "ratio", "matureWindows", True),
        SummaryMetricSpec("navigation", "shopUserNavigatedRate", "navigation.shopUserNavigatedRate", "ratio", "ratio", "matureWindows", True),
        SummaryMetricSpec("commerce", "observedAttemptSuccessRate", "commerce.observedAttemptSuccessRate", "ratio", "ratio", "observedCommerceAttempts"),
        SummaryMetricSpec("commerce", "committedSuccessWindowRate", "commerce.committedSuccessWindowRate", "ratio", "ratio", "matureWindows"),
        SummaryMetricSpec("progression", "progressionWindows", "progression.progressionWindows", "scalar", "windows", "matureWindows"),
        SummaryMetricSpec("nextRun", "nextRunWithinWindow", "nextRun.nextRunWithinWindow", "ratio", "ratio", "matureWindows", True),
        SummaryMetricSpec("nextRun", "sameStageRetry", "nextRun.sameStageRetry", "ratio", "ratio", "linkedWindows"),
    ),
    "runRetention": (
        SummaryMetricSpec("sample", "anchorFinalAttempts", "sample.anchorFinalAttempts", "scalar", "attempts", "canonicalFinalAttempts", True),
        SummaryMetricSpec("sample", "eligibleAnchors", "sample.eligibleAnchors", "scalar", "attempts", "linkageEligibleCanonicalFinalAttempts", True),
        SummaryMetricSpec("nextRun", "nextRunObservedRate", "nextRun.nextRunObserved", "ratio", "ratio", "eligibleAnchors", True),
        SummaryMetricSpec("observation", "latencyRightCensoredRate", "observation.latencyRightCensored", "ratio", "ratio", "eligibleAnchors", True),
        SummaryMetricSpec("latency", "nextRunDelayP50", "latency.observedDelaySeconds.p50", "scalar", "seconds", "anchorsWithObservedNextNewAttempt", True),
        SummaryMetricSpec("latency", "nextRunDelayP75", "latency.observedDelaySeconds.p75", "scalar", "seconds", "anchorsWithObservedNextNewAttempt"),
        SummaryMetricSpec("threshold", "thresholdExceededCount", "thresholdClassification.thresholdExceededCount", "scalar", "anchors", "thresholdResolvedAnchors"),
        SummaryMetricSpec("threshold", "noNextRunBeyondThresholdCount", "thresholdClassification.noNextRunBeyondThresholdCount", "scalar", "anchors", "thresholdResolvedAnchors"),
    ),
    "observedAppReturn": (
        SummaryMetricSpec("sample", "anchorFinalAttempts", "sample.anchorFinalAttempts", "scalar", "attempts", "canonicalFinalAttempts", True),
        SummaryMetricSpec("sample", "eligibleAnchors", "sample.eligibleAnchors", "scalar", "attempts", "lifecycleBaselineEligibleAnchors", True),
        SummaryMetricSpec("return", "observedReturnCount", "return.observedReturnCount", "scalar", "attempts", "eligibleAnchors", True),
        SummaryMetricSpec("return", "observedReturnRate", "return.observedReturnRate", "ratio", "ratio", "eligibleAnchors", True),
        SummaryMetricSpec("return", "coldStartReturnCount", "return.coldStartReturnCount", "scalar", "attempts", "observedReturns"),
        SummaryMetricSpec("return", "foregroundResumeReturnCount", "return.foregroundResumeReturnCount", "scalar", "attempts", "observedReturns"),
        SummaryMetricSpec("observation", "rightCensoredCount", "observation.rightCensoredCount", "scalar", "attempts", "eligibleAnchors", True),
        SummaryMetricSpec("observation", "rightCensoredRate", "observation.rightCensoredRate", "ratio", "ratio", "eligibleAnchors"),
        SummaryMetricSpec("latency", "timeToObservedAppReturnP50", "latency.timeToObservedAppReturnP50", "scalar", "seconds", "observedReturns"),
        SummaryMetricSpec("latency", "timeToObservedAppReturnP75", "latency.timeToObservedAppReturnP75", "scalar", "seconds", "observedReturns"),
        SummaryMetricSpec("latency", "timeToObservedAppReturnP90", "latency.timeToObservedAppReturnP90", "scalar", "seconds", "observedReturns"),
        SummaryMetricSpec("threshold", "returnedWithinThresholdCount", "threshold.returnedWithinThresholdCount", "scalar", "attempts", "thresholdClassifiedAnchors"),
        SummaryMetricSpec("threshold", "returnedAfterThresholdCount", "threshold.returnedAfterThresholdCount", "scalar", "attempts", "thresholdClassifiedAnchors"),
        SummaryMetricSpec("threshold", "noObservedReturnBeyondThresholdCount", "threshold.noObservedReturnBeyondThresholdCount", "scalar", "attempts", "thresholdClassifiedAnchors"),
    ),
    "gaIdentityBridge": (
        SummaryMetricSpec("source", "gaForegroundEvents", "source.gaForegroundEvents", "scalar", "events", "gaForegroundObservations", True),
        SummaryMetricSpec("mapping", "mappedCount", "mapping.mappedCount", "scalar", "observations", "gaForegroundObservations", True),
        SummaryMetricSpec("mapping", "mappedRate", "mapping.mappedRate", "ratio", "ratio", "gaForegroundObservations", True),
        SummaryMetricSpec("mapping", "unmappedCount", "mapping.unmappedCount", "scalar", "observations", "gaForegroundObservations"),
        SummaryMetricSpec("mapping", "temporalConflictCount", "mapping.temporalConflictCount", "scalar", "observations", "gaForegroundObservations", True),
        SummaryMetricSpec("mapping", "multiProfilePseudoCount", "mapping.multiProfilePseudoCount", "scalar", "appInstances", "mappedAppInstances"),
        SummaryMetricSpec("mapping", "profileSwitchObservationCount", "mapping.profileSwitchObservationCount", "scalar", "transitions", "mappedTimelineTransitions"),
    ),
    "observedUninstall": (
        SummaryMetricSpec("source", "appRemoveEvents", "source.appRemoveEvents", "scalar", "events", "observedAppRemoveEvents", True),
        SummaryMetricSpec("uninstall", "observedCount", "uninstall.observedCount", "scalar", "events", "observedAppRemoveEvents", True),
        SummaryMetricSpec("attribution", "mappedCount", "attribution.mappedCount", "scalar", "events", "observedAppRemoveEvents", True),
        SummaryMetricSpec("attribution", "mappedRate", "attribution.mappedRate", "ratio", "ratio", "observedAppRemoveEvents", True),
        SummaryMetricSpec("attribution", "unmappedCount", "attribution.unmappedCount", "scalar", "events", "observedAppRemoveEvents", True),
        SummaryMetricSpec("attribution", "ambiguousCount", "attribution.ambiguousCount", "scalar", "events", "observedAppRemoveEvents", True),
        SummaryMetricSpec("attribution", "missingPseudoCount", "attribution.missingPseudoCount", "scalar", "events", "observedAppRemoveEvents"),
        SummaryMetricSpec("attribution", "noPriorMappingCount", "attribution.noPriorMappingCount", "scalar", "events", "observedAppRemoveEvents"),
        SummaryMetricSpec("attribution", "temporalConflictCount", "attribution.temporalConflictCount", "scalar", "events", "observedAppRemoveEvents"),
        SummaryMetricSpec("mappingAge", "p50Seconds", "mappingAge.p50Seconds", "scalar", "seconds", "mappedObservedAppRemoveEvents"),
        SummaryMetricSpec("mappingAge", "p95Seconds", "mappingAge.p95Seconds", "scalar", "seconds", "mappedObservedAppRemoveEvents"),
    ),
    "retentionEvidence": tuple(
        SummaryMetricSpec(
            family,
            metric,
            path,
            value_type,
            unit,
            (
                "matureAnchors"
                if family in {"gameplayReturn", "appReturn"}
                else "retentionEpisodes"
                if family == "retentionEvidence"
                else "observedAppRemoveEvents"
            ),
            metric in {"returnedWithinHorizonRate", "rightCensoredCount", "mappedObservedCount"},
        )
        for family, metric, path, value_type, unit
        in RETENTION_EVIDENCE_METRIC_SPECS
    ),
}


TABLE_EVIDENCE: Mapping[str, Mapping[str, TableEvidenceSpec]] = {
    "stageDifficulty": {
        "deaths_by_time_bucket.csv": TableEvidenceSpec("deathTiming", ("bucket",), "timeBucket", "timedDeaths", (TableMetricSpec("deathBucketRate", "ratio", "ratio", "count", "denominator", "ratio"),)),
        "deaths_by_phase.csv": TableEvidenceSpec("deathConcentration", ("value",), "phase", "attributedDeaths", (TableMetricSpec("deathShare", "ratio", "ratio", "count", "denominator", "ratio", True),)),
        "deaths_by_floor.csv": TableEvidenceSpec("deathConcentration", ("value",), "floor", "attributedDeaths", (TableMetricSpec("deathShare", "ratio", "ratio", "count", "denominator", "ratio"),)),
        "deaths_by_wave.csv": TableEvidenceSpec("deathConcentration", ("value",), "wave", "attributedDeaths", (TableMetricSpec("deathShare", "ratio", "ratio", "count", "denominator", "ratio"),)),
        "deaths_by_zone.csv": TableEvidenceSpec("deathConcentration", ("value",), "zone", "attributedDeaths", (TableMetricSpec("deathShare", "ratio", "ratio", "count", "denominator", "ratio"),)),
        "final_deaths_by_source.csv": TableEvidenceSpec("deathCauses", ("label",), "sourceCategory", "attributedDeaths", (TableMetricSpec("finalDeathShare", "ratio", "ratio", "count", "denominator", "ratio", True),)),
        "deaths_by_enemy.csv": TableEvidenceSpec("deathCauses", ("label",), "enemyDefinition", "attributedDeaths", (TableMetricSpec("finalDeathShare", "ratio", "ratio", "count", "denominator", "ratio"),)),
        "final_deaths_by_environment_effect.csv": TableEvidenceSpec("deathCauses", ("label",), "environmentEffect", "attributedDeaths", (TableMetricSpec("finalDeathShare", "ratio", "ratio", "count", "denominator", "ratio"),)),
        "incoming_damage_by_enemy.csv": TableEvidenceSpec("incomingDamage", ("label",), "enemyDefinition", "eligibleRuns", (TableMetricSpec("totalAppliedDamage", "scalar", "totalAppliedDamage", unit="damage"), TableMetricSpec("damageShare", "scalar", "damageRatio", unit="ratio"))),
        "damage_by_source_category.csv": TableEvidenceSpec("incomingDamage", ("label",), "sourceCategory", "eligibleRuns", (TableMetricSpec("totalAppliedDamage", "scalar", "totalAppliedDamage", unit="damage"),)),
        "threat_by_outcome.csv": TableEvidenceSpec("threat", ("outcome", "metric"), "threatMetric", "eligibleRunSegments", (TableMetricSpec("median", "scalar", "median", unit="metricValue"),)),
        "player_state_at_death.csv": TableEvidenceSpec("playerStateAtDeath", ("metric",), "deathStateMetric", "selectedDeathSnapshots", (TableMetricSpec("median", "scalar", "median", unit="metricValue"),)),
    },
    "weaponPerformance": {
        "weapon_adoption.csv": TableEvidenceSpec("adoption", ("weaponFamilyId",), "weaponFamily", "eligibleAttempts", (TableMetricSpec("attemptInclusionRate", "ratio", "attemptInclusionRatio", "combatObservedAttempts", "eligibleAttempts", "ratio", True),)),
        "weapon_final_ownership.csv": TableEvidenceSpec("finalOwnership", ("weaponFamilyId",), "weaponFamily", "finalStateAssessedAttempts", (TableMetricSpec("finalOwnershipRate", "ratio", "finalOwnershipRatio", "finalOwnedAttempts", "finalStateAssessedAttempts", "ratio", True),)),
        "weapon_combat_performance.csv": TableEvidenceSpec("combat", ("aggregationLevel", "weaponFamilyId", "weaponId"), "weapon", "combatObservedAttempts", (TableMetricSpec("totalDamage", "scalar", "totalDamage", unit="damage"), TableMetricSpec("damageShareMean", "scalar", "damageShareMean", unit="ratio", core=True))),
        "weapon_dps.csv": TableEvidenceSpec("dps", ("aggregationLevel", "weaponFamilyId", "weaponId"), "weapon", "combatObservedInstanceSegments", (TableMetricSpec("dpsCoverageAmongCombatObservedInstanceSegments", "ratio", "dpsCoverageAmongCombatObservedInstanceSegments", "instanceSegmentsWithValidDpsSample", "combatObservedInstanceSegments", "ratio", True), TableMetricSpec("effectiveDpsWeighted", "scalar", "effectiveDpsWeighted", unit="damagePerSecond"))),
        "weapon_boss_performance.csv": TableEvidenceSpec("boss", ("weaponFamilyId",), "weaponFamily", "bossEligibleSegments", (TableMetricSpec("totalBossDamage", "scalar", "totalBossDamage", unit="damage"),)),
        "weapon_outcome_association.csv": TableEvidenceSpec("outcomeAssociation", ("associationDefinition", "weaponFamilyId"), "weaponFamilyAssociation", "finalAttempts", (TableMetricSpec("presentClearRate", "derivedClearRatio", "presentClearRate", "presentClears", "presentDeaths", "ratio"), TableMetricSpec("absentClearRate", "derivedClearRatio", "absentClearRate", "absentClears", "absentDeaths", "ratio"))),
        "weapon_state_breakdown.csv": TableEvidenceSpec("state", ("weaponFamilyId", "weaponId", "weaponType"), "weaponState", "finalOwnedAttempts", (TableMetricSpec("finalOwnershipRate", "scalar", "finalOwnershipRatio", unit="ratio"),)),
        "weapon_level_breakdown.csv": TableEvidenceSpec("level", ("weaponFamilyId", "weaponId", "effectiveLevel"), "weaponLevel", "finalOwnedAttempts", (TableMetricSpec("finalOwnedAttempts", "scalar", "finalOwnedAttempts", unit="attempts"),)),
        "weapon_dps_invalid_reasons.csv": TableEvidenceSpec("dpsQuality", ("aggregationLevel", "weaponFamilyId", "weaponId", "invalidReason"), "dpsInvalidReason", "invalidSamples", (TableMetricSpec("invalidSampleCount", "scalar", "invalidSampleCount", unit="samples"),)),
        "weapon_data_quality.csv": TableEvidenceSpec("dataQuality", ("metric",), "qualityMetric", "sourcePopulation", (TableMetricSpec("ratio", "ratio", "ratio", "count", "denominator", "ratio"),)),
    },
    "upgradeChoice": {
        "upgrade_candidate_exposure.csv": TableEvidenceSpec("candidate", ("candidateKey",), "upgradeCandidate", "completeExposures", (TableMetricSpec("pickRate", "ratio", "pickRate", "pickRateCount", "pickRateDenominator", "ratio", True),)),
        "upgrade_candidate_selection.csv": TableEvidenceSpec("selection", ("candidateKey",), "upgradeCandidate", "completeExposures", (TableMetricSpec("pickRate", "ratio", "pickRate", "pickRateCount", "pickRateDenominator", "ratio"),)),
        "upgrade_candidate_position.csv": TableEvidenceSpec("position", ("candidateKey", "dimension", "dimensionValue"), "upgradeCandidatePosition", "completeExposures", (TableMetricSpec("pickRate", "ratio", "pickRate", "pickRateCount", "pickRateDenominator", "ratio"),)),
        "upgrade_head_to_head.csv": TableEvidenceSpec("headToHead", ("candidateA", "candidateB"), "upgradeCandidatePair", "coExposures", (TableMetricSpec("aConditionalPreference", "ratio", "aConditionalPreference", "aConditionalPreferenceCount", "aConditionalPreferenceDenominator", "ratio"), TableMetricSpec("bConditionalPreference", "ratio", "bConditionalPreference", "bConditionalPreferenceCount", "bConditionalPreferenceDenominator", "ratio"))),
        "upgrade_outcome_association.csv": TableEvidenceSpec("outcomeAssociation", ("candidateKey",), "upgradeCandidate", "fullyChoiceCoveredAttempts", (TableMetricSpec("selectedClearRate", "derivedClearRatio", "selectedClearRate", "selectedClears", "selectedDeaths", "ratio"), TableMetricSpec("exposedNotSelectedClearRate", "derivedClearRatio", "exposedNotSelectedClearRate", "exposedNotSelectedClears", "exposedNotSelectedDeaths", "ratio"), TableMetricSpec("alternativeSelectedAttempts", "scalar", "alternativeSelectedAttempts", unit="attempts"), TableMetricSpec("noSelectionOnlyAttempts", "scalar", "noSelectionOnlyAttempts", unit="attempts"))),
        "upgrade_context.csv": TableEvidenceSpec("context", ("candidateKey", "dimension", "dimensionValue", "attributionSource"), "upgradeContext", "completeExposures", (TableMetricSpec("pickRate", "ratio", "pickRate", "pickRateCount", "pickRateDenominator", "ratio"),)),
        "upgrade_data_quality.csv": TableEvidenceSpec("dataQuality", ("metric",), "qualityMetric", "sourcePopulation", (TableMetricSpec("ratio", "ratio", "ratio", "count", "denominator", "ratio"),)),
    },
    "progressionNextRun": {
        "progression_activity.csv": TableEvidenceSpec("activity", ("progressionKind", "targetId", "secondaryId"), "progressionTarget", "progressionEvents", (TableMetricSpec("eventCount", "scalar", "eventCount", unit="events", core=True),)),
        "progression_state_transitions.csv": TableEvidenceSpec("stateTransition", ("progressionKind", "targetId", "secondaryId", "beforeState", "afterState"), "progressionState", "progressionEvents", (TableMetricSpec("eventCount", "scalar", "eventCount", unit="events"),)),
        "progression_episode_composition.csv": TableEvidenceSpec("episodes", ("progressionKindSet", "episodeType", "boundaryType"), "episodeComposition", "boundedEpisodes", (TableMetricSpec("episodeCount", "scalar", "episodeCount", unit="episodes"),)),
        "progression_cooccurrence.csv": TableEvidenceSpec("coOccurrence", ("kindA", "kindB"), "progressionKindPair", "multiProgressionEpisodes", (TableMetricSpec("coOccurrenceShare", "ratio", "shareOfMultiProgressionEpisodes", "coOccurrenceEpisodeCount", "shareDenominator", "ratio"),)),
        "progression_next_run.csv": TableEvidenceSpec("nextRun", ("dimension", "progressionKind"), "progressionCohort", "matureEpisodes", (TableMetricSpec("nextRunRate", "ratio", "nextRunRate", "nextRunCount", "nextRunRateDenominator", "ratio", True),)),
        "progression_outcome_transitions.csv": TableEvidenceSpec("outcomeTransition", ("pairScope", "previousOutcome", "nextOutcome"), "outcomeTransition", "pairedEpisodes", (TableMetricSpec("transitionRate", "ratio", "transitionRatio", "transitionCount", "transitionDenominator", "ratio"),)),
        "progression_paired_elapsed.csv": TableEvidenceSpec("pairedElapsed", ("pairScope",), "pairScope", "pairedEpisodes", (TableMetricSpec("deltaMedian", "scalar", "deltaMedian", unit="seconds"),)),
        "progression_data_quality.csv": TableEvidenceSpec("dataQuality", ("metric",), "qualityMetric", "sourcePopulation", (TableMetricSpec("ratio", "ratio", "ratio", "count", "denominator", "ratio"),)),
    },
    "postRunBehavior": {
        "post_run_first_action.csv": TableEvidenceSpec("firstAction", ("rowType", "anchorOutcome", "fromAction", "toAction"), "action", "matureWindows", (TableMetricSpec("rate", "ratio", "ratio", "actionCount", "denominator", "ratio"),)),
        "post_run_navigation.csv": TableEvidenceSpec("navigation", ("anchorOutcome", "dimension"), "navigationDimension", "matureWindows", (TableMetricSpec("viewedRate", "ratio", "viewedRate", "viewedWindows", "viewedDenominator", "ratio"), TableMetricSpec("userNavigatedRate", "ratio", "userNavigatedRate", "userNavigatedWindows", "userNavigatedDenominator", "ratio"))),
        "post_run_feedback_behavior.csv": TableEvidenceSpec("feedback", ("anchorOutcome", "feedbackCohort"), "feedbackCohort", "matureWindows", (TableMetricSpec("shopPresentedRate", "ratio", "shopPresentedRate", "shopPresentedCount", "windowCount", "ratio"), TableMetricSpec("shopUserNavigatedRate", "ratio", "shopUserNavigatedRate", "shopUserNavigatedCount", "windowCount", "ratio"))),
        "post_run_shop_funnel.csv": TableEvidenceSpec("shop", ("rowType", "anchorOutcome", "dimension"), "shopFunnel", "matureWindows", (TableMetricSpec("shopPresentedRate", "ratio", "shopPresentedRate", "shopPresentedWindows", "matureWindows", "ratio", True), TableMetricSpec("shopUserNavigatedRate", "ratio", "shopUserNavigatedRate", "shopUserNavigatedWindows", "matureWindows", "ratio", True))),
        "post_run_offer_funnel.csv": TableEvidenceSpec("offer", ("rowType", "anchorOutcome", "dimension"), "offerFunnel", "matureWindows", (TableMetricSpec("selectedWindows", "scalar", "selectedWindows", unit="windows"),)),
        "post_run_commerce.csv": TableEvidenceSpec("commerce", ("rowType", "anchorOutcome", "dimension"), "commerceCategory", "commerceOperationsOrWindows", (TableMetricSpec("observedAttemptSuccessRate", "ratio", "observedAttemptSuccessRate", "linkedSucceededAttemptCount", "observedAttemptCount", "ratio"), TableMetricSpec("committedSuccessWindowRate", "ratio", "committedSuccessWindowRate", "committedSuccessWindows", "matureWindows", "ratio"))),
        "post_run_progression.csv": TableEvidenceSpec("progression", ("anchorOutcome", "progressionKind"), "progressionKind", "matureWindows", (TableMetricSpec("progressionRate", "ratio", "progressionRate", "progressionWindows", "windowCount", "ratio"),)),
        "post_run_next_run.csv": TableEvidenceSpec("nextRun", ("anchorOutcome",), "anchorOutcome", "matureWindows", (TableMetricSpec("nextRunRate", "ratio", "nextRunRate", "nextRunWithinWindow", "nextRunRateDenominator", "ratio", True),)),
        "post_run_action_transitions.csv": TableEvidenceSpec("actionSequence", ("rowType", "anchorOutcome", "fromAction", "toAction"), "actionPair", "sourceActions", (TableMetricSpec("transitionRate", "ratio", "ratio", "actionCount", "denominator", "ratio"),)),
        "post_run_data_quality.csv": TableEvidenceSpec("dataQuality", ("metric",), "qualityMetric", "sourcePopulation", (TableMetricSpec("ratio", "ratio", "ratio", "count", "denominator", "ratio"),)),
    },
    "runRetention": {
        "run_retention_summary.csv": TableEvidenceSpec("nextRun", ("cohortType", "cohortValue"), "retentionCohort", "eligibleAnchors", (
            TableMetricSpec("nextRunObservedRate", "ratio", "nextRunObservedRate", "nextRunObservedCount", "eligibleAnchors", "ratio", True),
            TableMetricSpec("thresholdExceededRate", "ratio", "thresholdExceededRate", "thresholdExceededCount", "thresholdResolvedDenominator", "ratio", True),
        )),
        "run_retention_by_outcome.csv": TableEvidenceSpec("nextRun", ("cohortType", "cohortValue"), "anchorOutcome", "eligibleAnchors", (
            TableMetricSpec("nextRunObservedRate", "ratio", "nextRunObservedRate", "nextRunObservedCount", "eligibleAnchors", "ratio"),
            TableMetricSpec("thresholdExceededRate", "ratio", "thresholdExceededRate", "thresholdExceededCount", "thresholdResolvedDenominator", "ratio"),
        )),
        "run_retention_by_stage.csv": TableEvidenceSpec("nextRun", ("cohortType", "cohortValue"), "anchorStage", "eligibleAnchors", (
            TableMetricSpec("nextRunObservedRate", "ratio", "nextRunObservedRate", "nextRunObservedCount", "eligibleAnchors", "ratio"),
            TableMetricSpec("thresholdExceededRate", "ratio", "thresholdExceededRate", "thresholdExceededCount", "thresholdResolvedDenominator", "ratio"),
        )),
        "run_retention_latency.csv": TableEvidenceSpec("latency", ("distribution",), "retentionLatencyDistribution", "sourceDefined", (
            TableMetricSpec("nextRunDelayP50", "scalar", "p50Seconds", unit="seconds", core=True),
            TableMetricSpec("nextRunDelayP75", "scalar", "p75Seconds", unit="seconds"),
        )),
    },
    "observedAppReturn": {},
    "gaIdentityBridge": {},
    "observedUninstall": {},
    "retentionEvidence": {},
}


COMPARISON_TABLES = {
    domain: {
        "stageDifficulty": "comparison_stage.csv",
        "weaponPerformance": "comparison_weapon.csv",
        "upgradeChoice": "comparison_upgrade.csv",
        "progressionNextRun": "comparison_progression.csv",
        "postRunBehavior": "comparison_post_run.csv",
    }[domain]
    for domain in COMPARISON_TABLE_SPECS
}
COMPARISON_REQUIRED_COLUMNS = (
    "domain", "metricFamily", "metric", "entityType", "entityKey", "dimension",
    "dimensionValue", "valueType", "unit", "observationUnit", "baselineObserved",
    "candidateObserved", "baselineCount", "baselineDenominator", "baselineValue",
    "candidateCount", "candidateDenominator", "candidateValue", "absoluteDelta",
    "percentagePointDelta", "relativeDelta", "direction", "status", "warningCodes",
)


REQUIRED_METRIC_KEYS = {
    "stageDifficulty": (
        "outcome", "survival", "deathTiming", "deathConcentration", "deathCauses",
        "incomingDamage", "threat", "playerStateAtDeath", "dataQuality",
    ),
    "weaponPerformance": (
        "sample", "adoption", "combatPerformance", "dps", "bossPerformance",
        "outcomeAssociation", "progressionState", "dataQuality",
    ),
    "upgradeChoice": (
        "sample", "exposure", "selection", "choiceContext", "headToHead",
        "outcomeAssociation", "dataQuality",
    ),
    "progressionNextRun": (
        "sample", "progressionActivity", "episodes", "nextRunEngagement",
        "pairedOutcome", "pairedElapsed", "coOccurrence", "dataQuality",
    ),
    "postRunBehavior": (
        "sample", "window", "feedback", "navigation", "shop", "commerce",
        "progression", "nextRun", "actionSequence", "dataQuality",
    ),
    "runRetention": (
        "sample", "observation", "nextRun", "latency", "thresholdClassification",
        "dataQuality",
    ),
    "observedAppReturn": (
        "sample", "return", "observation", "latency", "threshold", "dataQuality",
    ),
    "gaIdentityBridge": ("source", "mapping", "dataQuality"),
    "observedUninstall": (
        "source", "uninstall", "attribution", "mappingAge", "dataQuality",
    ),
    "retentionEvidence": (
        "gameplayReturn", "appReturn", "retentionEvidence", "observedUninstall",
    ),
}
REQUIRED_METRIC_KEYS["contentVersionCompare"] = (
    "sample", "stageDifficulty", "weaponPerformance", "upgradeChoice",
    "progressionNextRun", "postRunBehavior", "comparisonDataQuality",
)


def required_metric_keys(
    analysis_type: str, analysis_version: str
) -> tuple[str, ...]:
    keys = REQUIRED_METRIC_KEYS[analysis_type]
    if analysis_type == "postRunBehavior" and analysis_version == "1.1.0":
        return tuple(key for key in keys if key != "feedback")
    return keys


def required_tables(
    analysis_type: str, analysis_version: str = "1.0.0"
) -> Mapping[str, tuple[str, ...]]:
    if analysis_type == "contentVersionCompare":
        result = {
            filename: COMPARISON_REQUIRED_COLUMNS
            for filename in COMPARISON_TABLES.values()
        }
        result["comparison_data_quality.csv"] = (
            "domain", "metric", "baselineValue", "candidateValue",
            "absoluteDelta", "status", "warningCodes",
        )
        return result
    if analysis_type == "runRetention":
        cohort_columns = (
            "cohortType", "cohortValue", "anchorFinalAttempts", "eligibleAnchors",
            "nextRunObservedCount", "noNextRunObservedAsOfCount", "nextRunObservedRate",
            "latencyRightCensoredCount", "latencyRightCensoredRate",
            "returnedWithinThresholdCount", "returnedAfterThresholdCount",
            "noNextRunBeyondThresholdCount", "thresholdRightCensoredCount",
            "thresholdExceededCount", "thresholdResolvedDenominator",
            "thresholdExceededRate",
        )
        return {
            "run_retention_summary.csv": cohort_columns,
            "run_retention_by_outcome.csv": cohort_columns,
            "run_retention_by_stage.csv": cohort_columns,
            "run_retention_latency.csv": (
                "distribution", "observedCount", "missingCount", "p50Seconds",
                "p75Seconds", "p90Seconds", "p95Seconds",
            ),
            "run_retention_data_quality.csv": ("metric", "count", "denominator", "ratio"),
        }
    if analysis_type == "observedAppReturn":
        cohort = (
            "cohortType", "cohortValue", "anchorFinalAttempts", "eligibleAnchors",
            "ineligibleAnchors", "observedReturnCount", "observedReturnRate",
            "coldStartReturnCount", "foregroundResumeReturnCount",
            "rightCensoredCount", "rightCensoredRate",
            "returnedWithinThresholdCount", "returnedAfterThresholdCount",
            "noObservedReturnBeyondThresholdCount", "thresholdRightCensoredCount",
            "thresholdIneligibleCount",
        )
        return {
            "overall-summary.csv": cohort,
            "outcome-summary.csv": cohort,
            "stage-summary.csv": cohort,
            "latency-summary.csv": (
                "distribution", "observedCount", "p50Seconds", "p75Seconds", "p90Seconds",
            ),
            "data-quality-summary.csv": ("metric", "count"),
        }
    if analysis_type == "gaIdentityBridge":
        return {
            "mapping-quality-summary.csv": ("metric", "count", "denominator", "ratio"),
            "source-summary.csv": (
                "sourceTableKind", "finalizationState", "selectedTableCount",
                "foregroundPhysicalRows", "logicalObservations",
            ),
            "conflict-summary.csv": ("mappingStatus", "count"),
        }
    if analysis_type == "observedUninstall":
        return {
            "source-summary.csv": (
                "sourceTableKind", "finalizationState", "selectedTableCount",
                "appRemovePhysicalRows", "logicalEvents",
            ),
            "attribution-summary.csv": (
                "attributionStatus", "attributionReason", "count", "denominator", "ratio",
            ),
            "mapping-age-summary.csv": (
                "distribution", "mappedCount", "p50Seconds", "p95Seconds",
                "minSeconds", "maxSeconds",
            ),
        }
    if analysis_type == "retentionEvidence":
        return {
            "evidence-summary.csv": ("evidenceDomain", "metric", "value", "unit"),
            "sequence-summary.csv": ("sequenceFacet", "episodeCount"),
            "maturity-summary.csv": (
                "evidenceDomain", "eligibleAnchorCount", "matureAnchorCount",
                "boundedAbsenceCount",
            ),
            "censoring-summary.csv": (
                "evidenceDomain", "rightCensoredCount", "censoringRate",
            ),
            "uninstall-attribution-quality-summary.csv": ("metric", "count"),
            "source-compatibility-summary.csv": (
                "artifactType", "sourceFinalizationState", "compatible",
            ),
        }
    result = {
        filename: tuple(dict.fromkeys((*spec.keys, *(
            column
            for metric in spec.metrics
            for column in (
                metric.value_column,
                metric.count_column,
                metric.denominator_column,
            )
            if column is not None
        ))))
        for filename, spec in TABLE_EVIDENCE.get(analysis_type, {}).items()
    }
    if analysis_type == "postRunBehavior" and analysis_version == "1.1.0":
        result.pop("post_run_feedback_behavior.csv", None)
    return result


def is_evidence_metric_eligible(domain: str, family: str, metric: str) -> bool:
    return (domain, family, metric) in evidence_metric_keys()
