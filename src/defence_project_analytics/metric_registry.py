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
METRIC_REGISTRY_VERSION = "1.1.0"


class MetricLifecycle(str, Enum):
    """Decision lifecycle for stable metric identities.

    Historical-only metrics remain parseable so finalized artifacts can be read,
    but they cannot participate in newly generated comparisons, evidence, or
    decisions.
    """

    ACTIVE = "Active"
    HISTORICAL_ONLY = "HistoricalOnly"


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
    return frozenset(result)


def metric_lifecycle(key: tuple[str, str, str]) -> MetricLifecycle:
    if key[0] == POST_RUN and key[1] == "feedback":
        return MetricLifecycle.HISTORICAL_ONLY
    return MetricLifecycle.ACTIVE


def _eligible_metric_keys() -> frozenset[tuple[str, str, str]]:
    return frozenset(
        key for key in known_metric_keys()
        if metric_lifecycle(key) is MetricLifecycle.ACTIVE
    )


def comparison_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys()


def evidence_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys()


def decision_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys()


def target_metric_keys() -> frozenset[tuple[str, str, str]]:
    return _eligible_metric_keys()


def is_decision_evidence_item(item: Mapping[str, Any]) -> bool:
    key = (
        str(item.get("domain") or ""),
        str(item.get("metricFamily") or ""),
        str(item.get("metric") or ""),
    )
    return key in decision_metric_keys()


def comparison_summary_specs(
    domain: str,
) -> tuple[tuple[str, str, str, str, str], ...]:
    return tuple(
        spec for spec in COMPARISON_SUMMARY_SPECS.get(domain, ())
        if metric_lifecycle((domain, spec[0], spec[1])) is MetricLifecycle.ACTIVE
    )


def comparison_table_specs(domain: str) -> tuple[ComparisonTableSpec, ...]:
    return tuple(
        spec for spec in COMPARISON_TABLE_SPECS.get(domain, ())
        if all(
            metric_lifecycle((domain, spec.metric_family, metric[0]))
            is MetricLifecycle.ACTIVE
            for metric in spec.metrics
        )
    )
