"""Shared stable metric identities used by B-6 and the C-1 evidence compiler."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


STAGE = "stageDifficulty"
WEAPON = "weaponPerformance"
UPGRADE = "upgradeChoice"
PROGRESSION = "progressionNextRun"
POST_RUN = "postRunBehavior"
RUN_RETENTION = "runRetention"
OBSERVED_APP_RETURN = "observedAppReturn"
GA_IDENTITY_BRIDGE = "gaIdentityBridge"
OBSERVED_UNINSTALL = "observedUninstall"
RETENTION_EVIDENCE = "retentionEvidence"
METRIC_REGISTRY_VERSION = "1.5.0"


class MetricLifecycle(str, Enum):
    """Decision lifecycle for stable metric identities.

    Historical-only metrics remain parseable so finalized artifacts can be read,
    but they cannot participate in newly generated comparisons, evidence, or
    decisions.
    """

    ACTIVE = "Active"
    HISTORICAL_ONLY = "HistoricalOnly"


class EvidenceUse(str, Enum):
    """Authority required by a particular C-2 reference, not by its source domain."""

    FACTUAL_REFERENCE = "FactualReference"
    DECISION_SUPPORT = "DecisionSupport"
    TARGET_GUARDRAIL = "TargetGuardrail"


@dataclass(frozen=True, slots=True)
class MetricAuthority:
    lifecycle: MetricLifecycle
    known_readable: bool
    comparison_eligible: bool
    evidence_eligible: bool
    decision_eligible: bool
    target_eligible: bool


COMPARISON_SUMMARY_SPECS: Mapping[
    str, tuple[tuple[str, str, str, str, str], ...]
] = {
    STAGE: (
        ("outcome", "finalAttempts", "outcome.finalAttempts", "scalar", "attempts"),
        ("outcome", "uniquePlayers", "outcome.uniquePlayers", "scalar", "players"),
        ("outcome", "clears", "outcome.clears", "scalar", "attempts"),
        ("outcome", "deaths", "outcome.deaths", "scalar", "attempts"),
        ("outcome", "abandons", "outcome.abandons", "scalar", "attempts"),
        ("outcome", "clearRate", "outcome.clearRate", "ratio", "finalAttempts"),
        ("survival", "p25AttemptElapsedSeconds", "survival.p25", "scalar", "seconds"),
        ("survival", "medianAttemptElapsedSeconds", "survival.median", "scalar", "seconds"),
        ("survival", "p75AttemptElapsedSeconds", "survival.p75", "scalar", "seconds"),
        ("survival", "p90AttemptElapsedSeconds", "survival.p90", "scalar", "seconds"),
        ("dataQuality", "telemetryCompleteRate", "dataQuality.telemetryCompleteRate", "ratio", "finalAttempts"),
        ("dataQuality", "detailCoverageRate", "dataQuality.detailCoverageRate", "ratio", "finalAttempts"),
    ),
    WEAPON: (
        ("sample", "finalAttempts", "sample.finalAttempts", "scalar", "attempts"),
        ("sample", "detailEligibleAttempts", "sample.detailEligibleAttempts", "scalar", "attempts"),
        ("combat", "totalDamage", "combatPerformance.totalDamage", "scalar", "damage"),
        ("combat", "totalBossDamage", "combatPerformance.totalBossDamage", "scalar", "damage"),
        ("combat", "totalHits", "combatPerformance.totalHits", "scalar", "hits"),
        ("dps", "validSampleCount", "dps.validSampleCount", "scalar", "samples"),
        ("dps", "dpsCoverageAmongCombatObservedInstanceSegments", "dps.coverage", "ratio", "combatObservedInstanceSegments"),
        ("dataQuality", "detailCoverageRate", "dataQuality.detailCoverageRate", "ratio", "finalAttempts"),
    ),
    UPGRADE: (
        ("sample", "finalAttempts", "sample.finalAttempts", "scalar", "attempts"),
        ("exposure", "completeExposures", "exposure.completeExposures", "scalar", "exposures"),
        ("selection", "linkedSelections", "selection.linkedSelections", "scalar", "selections"),
        ("selection", "noSelectionExposures", "selection.noSelectionExposures", "scalar", "exposures"),
        ("dataQuality", "choiceCoverageRate", "dataQuality.choiceCoverageRate", "ratio", "candidateSegments"),
        ("dataQuality", "attemptChoiceCoverageRate", "dataQuality.attemptChoiceCoverageRate", "ratio", "attempts"),
    ),
    PROGRESSION: (
        ("activity", "progressionEvents", "progressionActivity.progressionEvents", "scalar", "events"),
        ("episodes", "boundedEpisodes", "episodes.boundedEpisodes", "scalar", "episodes"),
        ("episodes", "singleProgressionEpisodes", "episodes.singleProgressionEpisodes", "scalar", "episodes"),
        ("episodes", "multiProgressionEpisodes", "episodes.multiProgressionEpisodes", "scalar", "episodes"),
        ("nextRun", "nextRunWithinWindow", "nextRunEngagement.nextRunWithinWindow", "ratio", "matureEpisodes"),
        ("nextRun", "sameStageRetry", "nextRunEngagement.sameStageRetry", "ratio", "linkedEpisodes"),
        ("pairedOutcome", "previousClearRate", "pairedOutcome.previousClearRate", "ratio", "pairedAttempts"),
        ("pairedOutcome", "nextClearRate", "pairedOutcome.nextClearRate", "ratio", "pairedAttempts"),
        ("pairedElapsed", "deltaMedianSeconds", "pairedElapsed.deltaMedianSeconds", "scalar", "seconds"),
    ),
    POST_RUN: (
        ("sample", "anchorFinalRuns", "sample.anchorFinalRuns", "scalar", "windows"),
        ("window", "matureWindows", "window.matureWindows", "scalar", "windows"),
        ("feedback", "positiveResponseRate", "feedback.positiveResponseRate", "ratio", "responses"),
        ("navigation", "shopPresentedRate", "navigation.shopPresentedRate", "ratio", "matureWindows"),
        ("navigation", "shopUserNavigatedRate", "navigation.shopUserNavigatedRate", "ratio", "matureWindows"),
        ("commerce", "observedAttemptSuccessRate", "commerce.observedAttemptSuccessRate", "ratio", "observedCommerceAttempts"),
        ("commerce", "committedSuccessWindowRate", "commerce.committedSuccessWindowRate", "ratio", "matureWindows"),
        ("progression", "progressionWindows", "progression.progressionWindows", "scalar", "windows"),
        ("nextRun", "nextRunWithinWindow", "nextRun.nextRunWithinWindow", "ratio", "matureWindows"),
        ("nextRun", "sameStageRetry", "nextRun.sameStageRetry", "ratio", "linkedWindows"),
    ),
}

RUN_RETENTION_METRIC_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("sample", "anchorFinalAttempts", "sample.anchorFinalAttempts", "scalar", "attempts"),
    ("sample", "eligibleAnchors", "sample.eligibleAnchors", "scalar", "attempts"),
    ("nextRun", "nextRunObservedRate", "nextRun.nextRunObserved", "ratio", "eligibleAnchors"),
    ("observation", "latencyRightCensoredRate", "observation.latencyRightCensored", "ratio", "eligibleAnchors"),
    ("nextRun", "sameStageNextRunRate", "nextRun.sameStageNextRun", "ratio", "observedNextAttempts"),
    ("nextRun", "sameContentNextRunRate", "nextRun.sameContentNextRun", "ratio", "observedNextAttempts"),
    ("latency", "nextRunDelayP50", "latency.observedDelaySeconds.p50", "scalar", "seconds"),
    ("latency", "nextRunDelayP75", "latency.observedDelaySeconds.p75", "scalar", "seconds"),
    ("latency", "nextRunDelayP90", "latency.observedDelaySeconds.p90", "scalar", "seconds"),
    ("latency", "nextRunDelayP95", "latency.observedDelaySeconds.p95", "scalar", "seconds"),
    ("threshold", "thresholdExceededCount", "thresholdClassification.thresholdExceededCount", "scalar", "anchors"),
    ("threshold", "thresholdExceededRate", "thresholdClassification.thresholdExceededRate", "scalar", "thresholdResolvedAnchors"),
    ("threshold", "noNextRunBeyondThresholdCount", "thresholdClassification.noNextRunBeyondThresholdCount", "scalar", "anchors"),
)

OBSERVED_APP_RETURN_METRIC_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("sample", "anchorFinalAttempts", "sample.anchorFinalAttempts", "scalar", "attempts"),
    ("sample", "eligibleAnchors", "sample.eligibleAnchors", "scalar", "attempts"),
    ("return", "observedReturnCount", "return.observedReturnCount", "scalar", "attempts"),
    ("return", "observedReturnRate", "return.observedReturnRate", "ratio", "eligibleAnchors"),
    ("return", "coldStartReturnCount", "return.coldStartReturnCount", "scalar", "attempts"),
    ("return", "foregroundResumeReturnCount", "return.foregroundResumeReturnCount", "scalar", "attempts"),
    ("observation", "rightCensoredCount", "observation.rightCensoredCount", "scalar", "attempts"),
    ("observation", "rightCensoredRate", "observation.rightCensoredRate", "ratio", "eligibleAnchors"),
    ("latency", "timeToObservedAppReturnP50", "latency.timeToObservedAppReturnP50", "scalar", "seconds"),
    ("latency", "timeToObservedAppReturnP75", "latency.timeToObservedAppReturnP75", "scalar", "seconds"),
    ("latency", "timeToObservedAppReturnP90", "latency.timeToObservedAppReturnP90", "scalar", "seconds"),
    ("threshold", "returnedWithinThresholdCount", "threshold.returnedWithinThresholdCount", "scalar", "attempts"),
    ("threshold", "returnedAfterThresholdCount", "threshold.returnedAfterThresholdCount", "scalar", "attempts"),
    ("threshold", "noObservedReturnBeyondThresholdCount", "threshold.noObservedReturnBeyondThresholdCount", "scalar", "attempts"),
)

GA_IDENTITY_BRIDGE_METRIC_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("source", "gaForegroundEvents", "source.gaForegroundEvents", "scalar", "events"),
    ("source", "dailyRows", "source.dailyRows", "scalar", "events"),
    ("source", "intradayRows", "source.intradayRows", "scalar", "events"),
    ("mapping", "mappedCount", "mapping.mappedCount", "scalar", "observations"),
    ("mapping", "mappedRate", "mapping.mappedRate", "ratio", "observations"),
    ("mapping", "unmappedCount", "mapping.unmappedCount", "scalar", "observations"),
    ("mapping", "missingUserIdCount", "mapping.missingUserIdCount", "scalar", "observations"),
    ("mapping", "missingPseudoIdCount", "mapping.missingPseudoIdCount", "scalar", "observations"),
    ("mapping", "missingOccurrenceCount", "mapping.missingOccurrenceCount", "scalar", "observations"),
    ("mapping", "unmatchedGaCount", "mapping.unmatchedGaCount", "scalar", "observations"),
    ("mapping", "customConflictCount", "mapping.customConflictCount", "scalar", "observations"),
    ("mapping", "gaConflictCount", "mapping.gaConflictCount", "scalar", "observations"),
    ("mapping", "temporalConflictCount", "mapping.temporalConflictCount", "scalar", "observations"),
    ("mapping", "durableIdentityConflictCount", "mapping.durableIdentityConflictCount", "scalar", "observations"),
    ("mapping", "distinctPseudoCount", "mapping.distinctPseudoCount", "scalar", "appInstances"),
    ("mapping", "distinctTelemetryPlayerCount", "mapping.distinctTelemetryPlayerCount", "scalar", "profiles"),
    ("mapping", "distinctRetentionBridgeCount", "mapping.distinctRetentionBridgeCount", "scalar", "profiles"),
    ("mapping", "multiProfilePseudoCount", "mapping.multiProfilePseudoCount", "scalar", "appInstances"),
    ("mapping", "profileSwitchObservationCount", "mapping.profileSwitchObservationCount", "scalar", "transitions"),
)

OBSERVED_UNINSTALL_METRIC_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("source", "appRemoveEvents", "source.appRemoveEvents", "scalar", "events"),
    ("uninstall", "observedCount", "uninstall.observedCount", "scalar", "events"),
    ("uninstall", "distinctMappedProfiles", "uninstall.distinctMappedProfiles", "scalar", "profiles"),
    ("uninstall", "distinctPseudoIds", "uninstall.distinctPseudoIds", "scalar", "appInstances"),
    ("attribution", "mappedCount", "attribution.mappedCount", "scalar", "events"),
    ("attribution", "mappedRate", "attribution.mappedRate", "ratio", "events"),
    ("attribution", "unmappedCount", "attribution.unmappedCount", "scalar", "events"),
    ("attribution", "ambiguousCount", "attribution.ambiguousCount", "scalar", "events"),
    ("attribution", "missingPseudoCount", "attribution.missingPseudoCount", "scalar", "events"),
    ("attribution", "noPriorMappingCount", "attribution.noPriorMappingCount", "scalar", "events"),
    ("attribution", "temporalConflictCount", "attribution.temporalConflictCount", "scalar", "events"),
    ("attribution", "gaDuplicateConflictCount", "attribution.gaDuplicateConflictCount", "scalar", "events"),
    ("mappingAge", "p50Seconds", "mappingAge.p50Seconds", "scalar", "seconds"),
    ("mappingAge", "p95Seconds", "mappingAge.p95Seconds", "scalar", "seconds"),
)

RETENTION_EVIDENCE_METRIC_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("gameplayReturn", "returnedWithinHorizonCount", "gameplayReturn.returnedWithinHorizonCount", "scalar", "anchors"),
    ("gameplayReturn", "matureAnchorCount", "gameplayReturn.matureAnchorCount", "scalar", "anchors"),
    ("gameplayReturn", "returnedWithinHorizonRate", "gameplayReturn.returnedWithinHorizonRate", "scalar", "ratio"),
    ("appReturn", "returnedWithinHorizonCount", "appReturn.returnedWithinHorizonCount", "scalar", "anchors"),
    ("appReturn", "matureAnchorCount", "appReturn.matureAnchorCount", "scalar", "anchors"),
    ("appReturn", "returnedWithinHorizonRate", "appReturn.returnedWithinHorizonRate", "scalar", "ratio"),
    ("retentionEvidence", "rightCensoredCount", "retentionEvidence.rightCensoredCount", "scalar", "anchors"),
    ("retentionEvidence", "matureAnchorCount", "retentionEvidence.matureAnchorCount", "scalar", "anchors"),
    ("observedUninstall", "mappedObservedCount", "observedUninstall.mappedObservedCount", "scalar", "events"),
    ("observedUninstall", "unmappedObservedCount", "observedUninstall.unmappedObservedCount", "scalar", "events"),
    ("observedUninstall", "ambiguousObservedCount", "observedUninstall.ambiguousObservedCount", "scalar", "events"),
)


@dataclass(frozen=True, slots=True)
class ComparisonTableSpec:
    filename: str
    metric_family: str
    keys: tuple[str, ...]
    metrics: tuple[tuple[str, str, str | None, str | None, str], ...]
    entity_type: str
    observation_unit: str


COMPARISON_TABLE_SPECS: Mapping[str, tuple[ComparisonTableSpec, ...]] = {
    STAGE: (
        ComparisonTableSpec("deaths_by_time_bucket.csv", "deathTiming", ("bucket",), (("deathBucketRate", "ratio", "count", "denominator", "ratio"),), "timeBucket", "timedDeaths"),
        ComparisonTableSpec("deaths_by_phase.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "phase", "attributedDeaths"),
        ComparisonTableSpec("deaths_by_floor.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "floor", "attributedDeaths"),
        ComparisonTableSpec("deaths_by_wave.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "wave", "attributedDeaths"),
        ComparisonTableSpec("deaths_by_zone.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "zone", "attributedDeaths"),
        ComparisonTableSpec("final_deaths_by_source.csv", "deathCauses", ("label",), (("finalDeathShare", "ratio", "count", "denominator", "ratio"),), "sourceCategory", "attributedDeaths"),
        ComparisonTableSpec("deaths_by_enemy.csv", "deathCauses", ("label",), (("finalDeathShare", "ratio", "count", "denominator", "ratio"),), "enemyDefinition", "attributedDeaths"),
        ComparisonTableSpec("incoming_damage_by_enemy.csv", "incomingDamage", ("label",), (("totalAppliedDamage", "scalar", None, None, "totalAppliedDamage"), ("hitCount", "scalar", None, None, "hitCount")), "enemyDefinition", "eligibleRuns"),
        ComparisonTableSpec("threat_by_outcome.csv", "threat", ("outcome", "metric"), (("median", "scalar", None, None, "median"), ("mean", "scalar", None, None, "mean")), "threatMetric", "eligibleRunSegments"),
    ),
    WEAPON: (
        ComparisonTableSpec("weapon_adoption.csv", "adoption", ("weaponFamilyId",), (("attemptInclusionRate", "ratio", "combatObservedAttempts", "eligibleAttempts", "attemptInclusionRatio"), ("startingLoadoutRate", "ratio", "startingLoadoutAttempts", "startLoadoutAssessedAttempts", "startingLoadoutRatio")), "weaponFamily", "eligibleAttempts"),
        ComparisonTableSpec("weapon_final_ownership.csv", "finalOwnership", ("weaponFamilyId",), (("finalOwnershipRate", "ratio", "finalOwnedAttempts", "finalStateAssessedAttempts", "finalOwnershipRatio"), ("finalLevelMedian", "scalar", None, None, "finalLevelMedian")), "weaponFamily", "finalStateAssessedAttempts"),
        ComparisonTableSpec("weapon_combat_performance.csv", "combat", ("aggregationLevel", "weaponFamilyId", "weaponId"), (("totalDamage", "scalar", None, None, "totalDamage"), ("damageMedian", "scalar", None, None, "damageMedian"), ("damageShareMean", "scalar", None, None, "damageShareMean")), "weapon", "combatObservedAttempts"),
        ComparisonTableSpec("weapon_dps.csv", "dps", ("aggregationLevel", "weaponFamilyId", "weaponId"), (("dpsCoverageAmongCombatObservedInstanceSegments", "ratio", "instanceSegmentsWithValidDpsSample", "combatObservedInstanceSegments", "dpsCoverageAmongCombatObservedInstanceSegments"), ("effectiveDpsWeighted", "scalar", None, None, "effectiveDpsWeighted"), ("uptimeMedian", "scalar", None, None, "uptimeMedian")), "weapon", "combatObservedInstanceSegments"),
        ComparisonTableSpec("weapon_boss_performance.csv", "boss", ("weaponFamilyId",), (("totalBossDamage", "scalar", None, None, "totalBossDamage"), ("bossDpsWeighted", "scalar", None, None, "bossDpsWeighted")), "weaponFamily", "bossEligibleSegments"),
        ComparisonTableSpec("weapon_outcome_association.csv", "outcomeAssociation", ("associationDefinition", "weaponFamilyId"), (("presentClearRate", "ratio", "presentClears", "presentDeaths", "presentClearRate"), ("absentClearRate", "ratio", "absentClears", "absentDeaths", "absentClearRate")), "weaponFamilyAssociation", "finalAttempts"),
    ),
    UPGRADE: (
        ComparisonTableSpec("upgrade_candidate_exposure.csv", "candidate", ("candidateKey",), (("pickRate", "ratio", "pickRateCount", "pickRateDenominator", "pickRate"), ("completeExposureCount", "scalar", None, None, "completeExposureCount")), "upgradeCandidate", "completeExposures"),
        ComparisonTableSpec("upgrade_candidate_position.csv", "position", ("candidateKey", "dimension", "dimensionValue"), (("pickRate", "ratio", "pickRateCount", "pickRateDenominator", "pickRate"),), "upgradeCandidatePosition", "completeExposures"),
        ComparisonTableSpec("upgrade_head_to_head.csv", "headToHead", ("candidateA", "candidateB"), (("aConditionalPreference", "ratio", "aConditionalPreferenceCount", "aConditionalPreferenceDenominator", "aConditionalPreference"), ("bConditionalPreference", "ratio", "bConditionalPreferenceCount", "bConditionalPreferenceDenominator", "bConditionalPreference")), "upgradeCandidatePair", "coExposures"),
        ComparisonTableSpec("upgrade_outcome_association.csv", "outcomeAssociation", ("candidateKey",), (("selectedClearRate", "ratio", "selectedClears", "selectedDeaths", "selectedClearRate"), ("exposedNotSelectedClearRate", "ratio", "exposedNotSelectedClears", "exposedNotSelectedDeaths", "exposedNotSelectedClearRate"), ("alternativeSelectedAttempts", "scalar", None, None, "alternativeSelectedAttempts"), ("noSelectionOnlyAttempts", "scalar", None, None, "noSelectionOnlyAttempts")), "upgradeCandidate", "fullyChoiceCoveredAttempts"),
    ),
    PROGRESSION: (
        ComparisonTableSpec("progression_activity.csv", "activity", ("progressionKind", "targetId", "secondaryId"), (("eventCount", "scalar", None, None, "eventCount"), ("nextRunRate", "scalar", None, None, "nextRunRate")), "progressionTarget", "progressionEvents"),
        ComparisonTableSpec("progression_next_run.csv", "nextRun", ("dimension", "progressionKind"), (("nextRunRate", "ratio", "nextRunCount", "nextRunRateDenominator", "nextRunRate"), ("sameStageRate", "ratio", "sameStageCount", "sameStageDenominator", "sameStageRate"), ("timeToNextRunMedian", "scalar", None, None, "timeToNextRunMedian")), "progressionCohort", "matureEpisodes"),
        ComparisonTableSpec("progression_outcome_transitions.csv", "outcomeTransition", ("pairScope", "previousOutcome", "nextOutcome"), (("transitionRate", "ratio", "transitionCount", "transitionDenominator", "transitionRatio"),), "outcomeTransition", "pairedEpisodes"),
        ComparisonTableSpec("progression_paired_elapsed.csv", "pairedElapsed", ("pairScope",), (("deltaMedian", "scalar", None, None, "deltaMedian"), ("pairedCount", "scalar", None, None, "pairedCount")), "pairScope", "pairedEpisodes"),
        ComparisonTableSpec("progression_cooccurrence.csv", "coOccurrence", ("kindA", "kindB"), (("coOccurrenceShare", "ratio", "coOccurrenceEpisodeCount", "shareDenominator", "shareOfMultiProgressionEpisodes"),), "progressionKindPair", "multiProgressionEpisodes"),
    ),
    POST_RUN: (
        ComparisonTableSpec("post_run_navigation.csv", "navigation", ("anchorOutcome", "dimension"), (("viewedRate", "ratio", "viewedWindows", "viewedDenominator", "viewedRate"), ("userNavigatedRate", "ratio", "userNavigatedWindows", "userNavigatedDenominator", "userNavigatedRate")), "navigationDimension", "matureWindows"),
        ComparisonTableSpec("post_run_feedback_behavior.csv", "feedback", ("anchorOutcome", "feedbackCohort"), (("shopPresentedRate", "ratio", "shopPresentedCount", "windowCount", "shopPresentedRate"), ("shopUserNavigatedRate", "ratio", "shopUserNavigatedCount", "windowCount", "shopUserNavigatedRate"), ("committedSuccessWindowRate", "ratio", "committedSuccessCount", "windowCount", "committedSuccessWindowRate"), ("nextRunRate", "ratio", "nextRunCount", "windowCount", "nextRunRate")), "feedbackCohort", "matureWindows"),
        ComparisonTableSpec("post_run_commerce.csv", "commerce", ("rowType", "anchorOutcome", "dimension"), (("observedAttemptSuccessRate", "ratio", "linkedSucceededAttemptCount", "observedAttemptCount", "observedAttemptSuccessRate"), ("committedSuccessWindowRate", "ratio", "committedSuccessWindows", "matureWindows", "committedSuccessWindowRate")), "commerceCategory", "commerceOperationsOrWindows"),
        ComparisonTableSpec("post_run_progression.csv", "progression", ("anchorOutcome", "progressionKind"), (("progressionRate", "ratio", "progressionWindows", "windowCount", "progressionRate"),), "progressionKind", "matureWindows"),
        ComparisonTableSpec("post_run_next_run.csv", "nextRun", ("anchorOutcome",), (("nextRunRate", "ratio", "nextRunWithinWindow", "nextRunRateDenominator", "nextRunRate"), ("sameStageRetryRate", "ratio", "sameStageRetryCount", "sameStageRetryDenominator", "sameStageRetryRate"), ("timeToNextRunMedian", "scalar", None, None, "timeToNextRunMedian")), "anchorOutcome", "matureWindows"),
        ComparisonTableSpec("post_run_action_transitions.csv", "actionSequence", ("anchorOutcome", "fromAction", "toAction"), (("transitionRate", "ratio", "actionCount", "denominator", "ratio"),), "actionPair", "sourceActions"),
    ),
}


def known_metric_keys() -> frozenset[tuple[str, str, str]]:
    """Return the stable domain/family/metric identities exposed by B-6."""

    result: set[tuple[str, str, str]] = set()
    for domain, specs in COMPARISON_SUMMARY_SPECS.items():
        result.update((domain, family, metric) for family, metric, *_ in specs)
    for domain, tables in COMPARISON_TABLE_SPECS.items():
        for table in tables:
            result.update(
                (domain, table.metric_family, metric[0]) for metric in table.metrics
            )
    result.update((RUN_RETENTION, family, metric) for family, metric, *_ in RUN_RETENTION_METRIC_SPECS)
    result.update((OBSERVED_APP_RETURN, family, metric) for family, metric, *_ in OBSERVED_APP_RETURN_METRIC_SPECS)
    result.update((GA_IDENTITY_BRIDGE, family, metric) for family, metric, *_ in GA_IDENTITY_BRIDGE_METRIC_SPECS)
    result.update((OBSERVED_UNINSTALL, family, metric) for family, metric, *_ in OBSERVED_UNINSTALL_METRIC_SPECS)
    result.update((RETENTION_EVIDENCE, family, metric) for family, metric, *_ in RETENTION_EVIDENCE_METRIC_SPECS)
    return frozenset(result)


def metric_lifecycle(key: tuple[str, str, str]) -> MetricLifecycle:
    if key[0] == POST_RUN and key[1] == "feedback":
        return MetricLifecycle.HISTORICAL_ONLY
    return MetricLifecycle.ACTIVE


def metric_authority(key: tuple[str, str, str]) -> MetricAuthority:
    lifecycle = metric_lifecycle(key)
    if lifecycle is MetricLifecycle.HISTORICAL_ONLY:
        return MetricAuthority(lifecycle, True, False, False, False, False)
    if key[0] in {
        RUN_RETENTION, OBSERVED_APP_RETURN, GA_IDENTITY_BRIDGE, OBSERVED_UNINSTALL,
    }:
        return MetricAuthority(lifecycle, True, False, True, False, False)
    if key[0] == RETENTION_EVIDENCE:
        comparison = key[1] in {"gameplayReturn", "appReturn"}
        return MetricAuthority(lifecycle, True, comparison, True, False, False)
    return MetricAuthority(lifecycle, True, True, True, True, True)


def _eligible_metric_keys(attribute: str) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        key for key in known_metric_keys()
        if bool(getattr(metric_authority(key), attribute))
    )


def comparison_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys("comparison_eligible")


def evidence_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys("evidence_eligible")


def decision_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys("decision_eligible")


def target_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys("target_eligible")


def monitor_metric_keys() -> frozenset[tuple[str, str, str]]:
    """Return metrics with static capability for MonitorOnly validation use.

    Runtime comparison authority remains a separate, required gate for R4 metrics.
    """

    return frozenset(
        key for key in comparison_metric_keys() if key[0] == RETENTION_EVIDENCE
    )


def is_decision_evidence_item(item: Mapping[str, Any]) -> bool:
    return is_evidence_item_eligible(item, EvidenceUse.DECISION_SUPPORT)


def is_evidence_item_eligible(item: Mapping[str, Any], use: EvidenceUse) -> bool:
    key = (
        str(item.get("domain") or ""),
        str(item.get("metricFamily") or ""),
        str(item.get("metric") or ""),
    )
    authority = metric_authority(key)
    if key not in known_metric_keys() or not authority.known_readable:
        return False
    if use is EvidenceUse.FACTUAL_REFERENCE:
        return authority.evidence_eligible
    if use is EvidenceUse.DECISION_SUPPORT:
        return authority.decision_eligible
    if use is EvidenceUse.TARGET_GUARDRAIL:
        return authority.target_eligible and authority.decision_eligible
    raise ValueError(f"Unsupported Evidence use: {use!r}")


def comparison_summary_specs(
    domain: str,
) -> tuple[tuple[str, str, str, str, str], ...]:
    return tuple(
        spec for spec in COMPARISON_SUMMARY_SPECS.get(domain, ())
        if (domain, spec[0], spec[1]) in comparison_metric_keys()
    )


def comparison_table_specs(domain: str) -> tuple[ComparisonTableSpec, ...]:
    return tuple(
        spec for spec in COMPARISON_TABLE_SPECS.get(domain, ())
        if all(
            (domain, spec.metric_family, metric[0]) in comparison_metric_keys()
            for metric in spec.metrics
        )
    )
