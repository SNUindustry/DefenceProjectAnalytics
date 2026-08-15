"""Read-only Weapon Performance analysis and aggregate report generation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from defence_project_analytics.bigquery_client import dry_run_query, query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec
from defence_project_analytics.reporting.models import (
    REPORT_CONTRACT_VERSION,
    MetricRatio,
    ReportBundle,
    ReportMetadata,
    ReportWarning,
    WeaponAdoptionMetrics,
    WeaponAnalysisScope,
    WeaponBossMetrics,
    WeaponCombatMetrics,
    WeaponDataQuality,
    WeaponDpsMetrics,
    WeaponOutcomeAssociationMetrics,
    WeaponPerformanceMetrics,
    WeaponProgressionMetrics,
    WeaponReportDefinitions,
    WeaponSampleSummary,
    utc_now_seconds,
)
from defence_project_analytics.reporting.renderers import (
    attached_tables_markdown,
    camel_case,
    to_external,
)
from defence_project_analytics.reporting.warnings import (
    WeaponThresholds,
    sort_weapon_warnings,
)
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
POPULATION_FRAGMENT = "sql/analysis/_weapon_population_ctes_v1.sql"
INCLUDE_MARKER = "-- @include weapon_population_ctes_v1"
SQL_FILES = {
    "population": "sql/analysis/weapon_population_v1.sql",
    "adoption": "sql/analysis/weapon_adoption_v1.sql",
    "combat": "sql/analysis/weapon_combat_performance_v1.sql",
    "dps": "sql/analysis/weapon_dps_v1.sql",
    "boss": "sql/analysis/weapon_boss_performance_v1.sql",
    "outcome": "sql/analysis/weapon_outcome_association_v1.sql",
    "state": "sql/analysis/weapon_state_breakdown_v1.sql",
}

TABLE_SPECS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "weapon_adoption.csv": ((
        "weaponFamilyId", "eligibleAttempts", "eligibleSegments", "combatObservedSegments",
        "combatObservedAttempts", "segmentInclusionRatio", "attemptInclusionRatio",
        "startLoadoutAssessedAttempts", "startingLoadoutAttempts", "startingLoadoutRatio",
        "newWeaponAcquisitionAttempts", "acquisitionP25", "acquisitionMedian", "acquisitionP75",
        "upgradeSelectionCount", "evolutionSelectionCount",
    ), ("weaponFamilyId",)),
    "weapon_final_ownership.csv": ((
        "weaponFamilyId", "finalStateAssessedAttempts", "finalOwnedAttempts", "finalOwnershipRatio",
        "finalOwnedClearAttempts", "finalOwnedDeadAttempts", "finalOwnedAbandonAttempts",
        "finalActiveAttempts", "finalActiveRatio", "finalEvolvedAttempts", "finalEvolvedRatio",
        "finalLevelP25", "finalLevelMedian", "finalLevelP75",
    ), ("weaponFamilyId",)),
    "weapon_combat_performance.csv": ((
        "aggregationLevel", "weaponFamilyId", "weaponId", "eligibleAttempts",
        "combatObservedAttempts", "combatObservedSegments", "combatObservedInstanceSegments",
        "totalDamage", "totalBossDamage", "totalHits", "damageMean", "damageP25", "damageMedian",
        "damageP75", "damageP90", "bossDamageMean", "bossDamageP25", "bossDamageMedian",
        "bossDamageP75", "bossDamageP90", "hitsMean", "hitsP25", "hitsMedian", "hitsP75",
        "hitsP90", "damageShareMean", "damageShareP25", "damageShareMedian", "damageShareP75",
        "damageShareP90",
    ), ("aggregationLevel", "weaponFamilyId", "weaponId")),
    "weapon_dps.csv": ((
        "aggregationLevel", "weaponFamilyId", "weaponId", "combatObservedInstanceSegments",
        "instanceSegmentsWithValidDpsSample", "dpsCoverageAmongCombatObservedInstanceSegments",
        "validSampleCount", "invalidSampleCount", "discardedDpsSamples", "totalValidDuration",
        "effectiveDpsWeighted", "effectiveDpsP25", "effectiveDpsMedian", "effectiveDpsP75",
        "mobDpsWeighted", "mobDpsP25", "mobDpsMedian", "mobDpsP75", "bossDpsWeighted",
        "bossDpsP25", "bossDpsMedian", "bossDpsP75", "uptimeObservedCount", "uptimeMissingCount",
        "uptimeP25", "uptimeMedian", "uptimeP75", "firstObservedElapsedMedian",
    ), ("aggregationLevel", "weaponFamilyId", "weaponId")),
    "weapon_dps_invalid_reasons.csv": ((
        "aggregationLevel", "weaponFamilyId", "weaponId", "invalidReason", "invalidSampleCount",
    ), ("aggregationLevel", "weaponFamilyId", "weaponId", "invalidReason")),
    "weapon_boss_performance.csv": ((
        "weaponFamilyId", "bossEligibleSegments", "familyBossEligibleSegments", "totalDamage",
        "totalBossDamage", "bossDamageShare", "validBossSampleCount", "bossDpsWeighted",
        "mobBucketSamples", "mixedBucketSamples", "bossBucketSamples",
    ), ("-totalBossDamage", "weaponFamilyId")),
    "weapon_outcome_association.csv": ((
        "associationDefinition", "weaponFamilyId", "presentFinalAttempts", "presentClears",
        "presentDeaths", "presentAbandons", "presentUnrecognizedOutcomes", "presentClearRate",
        "presentElapsedP25", "presentElapsedMedian", "presentElapsedP75", "absentFinalAttempts",
        "absentClears", "absentDeaths", "absentAbandons", "absentUnrecognizedOutcomes",
        "absentClearRate", "absentElapsedP25", "absentElapsedMedian", "absentElapsedP75",
        "clearRateDifferencePp",
    ), ("associationDefinition", "weaponFamilyId")),
    "weapon_state_breakdown.csv": ((
        "weaponFamilyId", "weaponId", "weaponType", "isEvolutionResult", "isActive", "stateRows",
        "finalOwnedAttempts", "clearAttempts", "deadAttempts", "abandonAttempts", "minBaseLevel",
        "maxBaseLevel", "finalStateAssessedAttempts", "finalOwnershipRatio",
    ), ("weaponFamilyId", "weaponId", "weaponType", "isEvolutionResult", "isActive")),
    "weapon_level_breakdown.csv": ((
        "weaponFamilyId", "weaponId", "weaponType", "effectiveLevel", "isEvolutionResult", "isActive",
        "stateRows", "finalOwnedAttempts", "clearAttempts", "deadAttempts", "abandonAttempts",
        "minBaseLevel", "maxBaseLevel", "finalStateAssessedAttempts", "finalOwnershipRatio",
    ), ("weaponFamilyId", "weaponId", "weaponType", "effectiveLevel")),
    "weapon_data_quality.csv": (("metric", "count", "denominator", "ratio"), ("metric",)),
}


@dataclass(frozen=True, slots=True)
class WeaponPerformanceRequest:
    environment: str
    stage_key: str
    content_version: int
    app_version: str | None = None
    release_id: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    segment_ended_at_utc_start: datetime | None = None
    segment_ended_at_utc_end: datetime | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.environment not in {"Production", "Test"}:
            raise ValueError("environment must be Production or Test")
        if not self.stage_key.strip():
            raise ValueError("stage_key must not be empty")
        if isinstance(self.content_version, bool) or not isinstance(self.content_version, int):
            raise ValueError("content_version must be an integer")
        for name in ("app_version", "release_id", "release_channel", "release_type"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be omitted or non-empty")
        for name in (
            "segment_ended_at_utc_start", "segment_ended_at_utc_end",
            "uploaded_at_utc_start", "uploaded_at_utc_end", "analysis_as_of_utc",
        ):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.segment_ended_at_utc_start and self.segment_ended_at_utc_end:
            if self.segment_ended_at_utc_start >= self.segment_ended_at_utc_end:
                raise ValueError("segment_ended_at_utc_start must be earlier than segment_ended_at_utc_end")
        if self.uploaded_at_utc_start and self.uploaded_at_utc_end:
            if self.uploaded_at_utc_start >= self.uploaded_at_utc_end:
                raise ValueError("uploaded_at_utc_start must be earlier than uploaded_at_utc_end")

    def resolved_as_of(self, clock: Callable[[], datetime] = utc_now_seconds) -> datetime:
        value = self.analysis_as_of_utc or clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_as_of_utc must be timezone-aware")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def to_scope(self, analysis_as_of_utc: datetime) -> WeaponAnalysisScope:
        return WeaponAnalysisScope(
            environment=self.environment, stage_key=self.stage_key, content_version=self.content_version,
            app_version=self.app_version, release_id=self.release_id, release_channel=self.release_channel,
            release_type=self.release_type, is_development_build=self.is_development_build,
            uploaded_at_utc_start=self.uploaded_at_utc_start, uploaded_at_utc_end=self.uploaded_at_utc_end,
            segment_ended_at_utc_start=self.segment_ended_at_utc_start,
            segment_ended_at_utc_end=self.segment_ended_at_utc_end,
            analysis_as_of_utc=analysis_as_of_utc,
        )


@dataclass(frozen=True, slots=True)
class WeaponPerformanceAnalysis:
    bundle: ReportBundle
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


def _typed(value: Any, bigquery_type: str) -> QueryParameterValue:
    return QueryParameterValue(value, bigquery_type)


def weapon_performance_parameters(
    request: WeaponPerformanceRequest, analysis_as_of_utc: datetime
) -> dict[str, Any]:
    return {
        "environment": request.environment,
        "stage_key": request.stage_key,
        "content_version": _typed(request.content_version, "INT64"),
        "app_version": _typed(request.app_version, "STRING"),
        "release_id": _typed(request.release_id, "STRING"),
        "release_channel": _typed(request.release_channel, "STRING"),
        "release_type": _typed(request.release_type, "STRING"),
        "is_development_build": _typed(request.is_development_build, "BOOL"),
        "segment_ended_start_utc": _typed(request.segment_ended_at_utc_start, "TIMESTAMP"),
        "segment_ended_end_utc": _typed(request.segment_ended_at_utc_end, "TIMESTAMP"),
        "uploaded_start_utc": _typed(request.uploaded_at_utc_start, "TIMESTAMP"),
        "uploaded_end_utc": _typed(request.uploaded_at_utc_end, "TIMESTAMP"),
        "analysis_as_of_utc": _typed(analysis_as_of_utc, "TIMESTAMP"),
    }


def build_weapon_performance_queries(
    request: WeaponPerformanceRequest,
    *,
    config: AnalyticsConfig | None = None,
    analysis_as_of_utc: datetime | None = None,
    clock: Callable[[], datetime] = utc_now_seconds,
) -> dict[str, QuerySpec]:
    as_of = analysis_as_of_utc or request.resolved_as_of(clock)
    fragment = load_sql(POPULATION_FRAGMENT, config=config)
    parameters = weapon_performance_parameters(request, as_of)
    queries: dict[str, QuerySpec] = {}
    for name, path in SQL_FILES.items():
        sql = load_sql(path, config=config)
        if sql.count(INCLUDE_MARKER) != 1:
            raise ValueError(f"Weapon SQL must contain exactly one population marker: {path}")
        sql = sql.replace(INCLUDE_MARKER, fragment)
        expected = named_parameter_names(sql)
        supplied = {key: value for key, value in parameters.items() if key in expected}
        missing = expected.difference(supplied)
        if missing:
            raise ValueError(f"Missing named BigQuery parameters: {', '.join(sorted(missing))}")
        queries[name] = QuerySpec(sql, supplied)
    return queries


def _value(row: pd.Series, name: str, default: Any = None) -> Any:
    value = row.get(name, default)
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return default
    if hasattr(value, "item"):
        value = value.item()
    return value


def _int(row: pd.Series, name: str) -> int:
    return int(_value(row, name, 0))


def _float(row: pd.Series, name: str) -> float | None:
    value = _value(row, name)
    return None if value is None else float(value)


def _external_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns={name: camel_case(str(name)) for name in frame.columns})


def _rows(frame: pd.DataFrame, *, limit: int = 10) -> tuple[Mapping[str, Any], ...]:
    return tuple(_external_frame(frame.head(limit)).to_dict(orient="records"))


def _ratio_from_columns(row: pd.Series, count: str, denominator: str) -> MetricRatio:
    return MetricRatio.from_counts(_int(row, count), _int(row, denominator))


def _weapon_warnings(
    sample: WeaponSampleSummary,
    quality: WeaponDataQuality,
    adoption: pd.DataFrame,
    dps: pd.DataFrame,
    boss: pd.DataFrame,
    outcome: pd.DataFrame,
    state: pd.DataFrame,
    thresholds: WeaponThresholds,
) -> tuple[ReportWarning, ...]:
    warnings: list[ReportWarning] = []

    def add(code: str, message: str) -> None:
        warnings.append(ReportWarning(code, message))

    if sample.final_attempts == 0:
        add("NO_ATTEMPTS", "No final attempts matched the requested scope.")
    elif sample.final_attempts < thresholds.min_combat_observed_attempts:
        add("LOW_SAMPLE_ATTEMPTS", f"Final attempts ({sample.final_attempts}) are below {thresholds.min_combat_observed_attempts}.")
    if quality.incomplete_segments_excluded:
        add("INCOMPLETE_DETAIL_EXCLUDED", f"Excluded {quality.incomplete_segments_excluded} explicitly incomplete segments.")
    if quality.legacy_unassessed_segments:
        add("UNASSESSED_LEGACY_DETAIL", f"Excluded {quality.legacy_unassessed_segments} segments without upload status.")
    if quality.mixed_content_segments_excluded:
        add("MIXED_CONTENT_DETAIL_EXCLUDED", f"Excluded {quality.mixed_content_segments_excluded} mixed-scope segments.")
    if quality.unresolved_release_rows:
        add("UNRESOLVED_RELEASE_ROWS", f"{quality.unresolved_release_rows} final attempts have unresolved release identity.")
    detail_ratio = quality.detail_coverage_rate.ratio
    if detail_ratio is None or detail_ratio < thresholds.min_detail_coverage:
        add("HIGH_WEAPON_DETAIL_MISSINGNESS", "Fully covered weapon-detail attempts are below the configured coverage threshold.")
    if quality.missing_weapon_identity_rows:
        add("MISSING_WEAPON_IDENTITY", f"Found {quality.missing_weapon_identity_rows} weapon rows without a family identity.")
    state_rows = state[state.get("rowType", pd.Series(dtype=str)).eq("state")] if not state.empty else state
    if not state_rows.empty and state_rows.groupby("weaponId", dropna=True)["weaponFamilyId"].nunique().gt(1).any():
        add("MIXED_WEAPON_STATE", "At least one weaponId maps to multiple scoped weapon families.")
    if quality.discarded_dps_samples:
        add("WEAPON_SAMPLE_TRUNCATED", f"Telemetry reports {quality.discarded_dps_samples} discarded DPS samples.")
    low_families = 0
    if not adoption.empty:
        low_families = int((pd.to_numeric(adoption["combatObservedAttempts"], errors="coerce").fillna(0) < thresholds.min_combat_observed_attempts).sum())
    if low_families:
        add("LOW_WEAPON_SAMPLE", f"{low_families} weapon families are below the combat-observed attempt threshold.")
    coverage = quality.dps_coverage_among_combat_observed_instance_segments.ratio
    if coverage is None or coverage < thresholds.min_dps_coverage:
        add("LOW_WEAPON_DPS_COVERAGE", "Valid DPS coverage among combat-observed instance-segments is below the configured threshold.")
    boss_count = int(pd.to_numeric(boss.get("bossEligibleSegments", pd.Series(dtype=float)), errors="coerce").fillna(0).max()) if not boss.empty else 0
    if boss_count < thresholds.min_boss_eligible_segments:
        add("LOW_BOSS_SAMPLE", f"Boss-eligible segments ({boss_count}) are below {thresholds.min_boss_eligible_segments}.")
    if not outcome.empty:
        add("OUTCOME_ASSOCIATION_BIASED_SAMPLE", "Weapon outcome associations are observational and conditioned by acquisition and survival.")
    return sort_weapon_warnings(warnings)


def _quality_table(quality: WeaponDataQuality) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    external = to_external(quality)
    for metric, value in external.items():
        if isinstance(value, dict) and set(value) == {"count", "denominator", "ratio"}:
            rows.append({"metric": metric, **value})
        else:
            rows.append({"metric": metric, "count": value, "denominator": None, "ratio": None})
    return pd.DataFrame(rows)


def _render_markdown(
    request: WeaponPerformanceRequest,
    sample: WeaponSampleSummary,
    quality: WeaponDataQuality,
    warnings: tuple[ReportWarning, ...],
    signals: list[str],
    tables: Mapping[str, pd.DataFrame],
) -> str:
    warning_lines = "\n".join(f"- `{item.code}`: {item.message}" for item in warnings) or "- None."
    signal_lines = "\n".join(f"- {item}" for item in signals) or "- No deterministic signal crossed the configured thresholds."
    coverage = quality.dps_coverage_among_combat_observed_instance_segments.ratio
    coverage_display = "n/a" if coverage is None else f"{coverage:.1%}"
    return f"""# Weapon Performance Report

## Scope

- Environment: `{request.environment}`
- Stage: `{request.stage_key}`
- Content version: `{request.content_version}`

## Sample & Data Quality

- Final attempts: {sample.final_attempts}
- Fully covered detail attempts: {sample.detail_eligible_attempts}/{sample.detail_candidate_attempts}
- Eligible gameplay segments: {sample.eligible_segments}

## Adoption

- `combatObservedSegments` counts eligible gameplay segments with positive applied weapon damage.
- `combatObservedAttempts` deduplicates those observations at final-attempt level.

## Combat Contribution

- Damage share is recomputed from summed family damage and summed attributed weapon damage across all eligible resume segments.

## DPS / Uptime

- Valid DPS coverage among combat-observed instance-segments: {coverage_display}
- This is not coverage of every owned, equipped, or used weapon.

## Boss Performance

- Boss denominators include only gameplay segments with a `BossStarted` transition.

## Outcome Associations

- Combat-observed and final-owned present/absent cohorts use separate stated denominators.

## Weapon State / Progression

- Final levels are exact integer values; evolution results are not merged into a presumed root family.

## Notable Statistical Signals

{signal_lines}

## Caveats

- Weapon outcome associations are observational, not causal.
- Combat-observed cohorts are conditioned on the weapon having produced positive applied damage. Elapsed-time differences are therefore especially sensitive to acquisition timing and survivorship.
- Late-acquired weapons can only be observed in attempts that survive long enough to acquire and damage with them.
- Combat/DPS metrics use fully covered telemetry-complete gameplay segments; final-attempt outcome metrics can have a larger population.
{warning_lines}

## Attached Tables

{attached_tables_markdown(f'tables/{name}' for name in tables)}
"""


def _assemble_bundle(
    request: WeaponPerformanceRequest,
    analysis_as_of_utc: datetime,
    frames: Mapping[str, pd.DataFrame],
    *,
    estimated_bytes: int,
    thresholds: WeaponThresholds,
    generated_at_utc: datetime,
) -> ReportBundle:
    if frames["population"].empty:
        raise RuntimeError("Weapon population query returned no aggregate row")
    population = frames["population"].iloc[0]
    adoption = _external_frame(frames["adoption"])
    combat = _external_frame(frames["combat"])
    dps_all = _external_frame(frames["dps"])
    boss = _external_frame(frames["boss"])
    outcome = _external_frame(frames["outcome"])
    state = _external_frame(frames["state"])
    dps = dps_all[dps_all.get("rowType", pd.Series(dtype=str)).eq("metric")].drop(columns=["rowType", "invalidReason"], errors="ignore")
    dps_invalid = dps_all[dps_all.get("rowType", pd.Series(dtype=str)).eq("invalidReason")][
        [name for name in ("aggregationLevel", "weaponFamilyId", "weaponId", "invalidReason", "invalidSampleCount") if name in dps_all]
    ]
    family_dps = dps[dps.get("aggregationLevel", pd.Series(dtype=str)).eq("family")]
    family_combat = combat[combat.get("aggregationLevel", pd.Series(dtype=str)).eq("family")]

    final_state_assessed = _int(adoption.iloc[0], "finalStateAssessedAttempts") if not adoption.empty else 0
    dps_denominator = int(pd.to_numeric(family_dps.get("combatObservedInstanceSegments", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    dps_count = int(pd.to_numeric(family_dps.get("instanceSegmentsWithValidDpsSample", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    discarded = int(pd.to_numeric(family_dps.get("discardedDpsSamples", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    candidate_attempts = _int(population, "detailCandidateAttempts")
    eligible_attempts = _int(population, "detailEligibleAttempts")
    sample = WeaponSampleSummary(
        final_attempts=_int(population, "finalAttempts"), unique_players=_int(population, "uniquePlayers"),
        clears=_int(population, "clears"), deaths=_int(population, "deaths"), abandons=_int(population, "abandons"),
        detail_candidate_attempts=candidate_attempts,
        detail_observed_attempts=_int(population, "detailObservedAttempts"),
        detail_eligible_attempts=eligible_attempts,
        partially_covered_attempts=_int(population, "partiallyCoveredAttempts"),
        eligible_segments=_int(population, "eligibleSegments"),
        weapon_family_count=_int(population, "weaponFamilyCount"),
    )
    quality = WeaponDataQuality(
        telemetry_complete_rate=_ratio_from_columns(population, "completeSegments", "assessedSegments"),
        detail_coverage_rate=MetricRatio.from_counts(eligible_attempts, candidate_attempts),
        weapon_summary_coverage=MetricRatio.from_counts(_int(population, "weaponSummaryObservedAttempts"), eligible_attempts),
        final_weapon_state_coverage=MetricRatio.from_counts(final_state_assessed, eligible_attempts),
        dps_coverage_among_combat_observed_instance_segments=MetricRatio.from_counts(dps_count, dps_denominator),
        unresolved_release_rows=_int(population, "unresolvedReleaseRows"),
        incomplete_segments_excluded=_int(population, "incompleteSegmentsExcluded"),
        legacy_unassessed_segments=_int(population, "legacyUnassessedSegments"),
        mixed_content_segments_excluded=_int(population, "mixedContentSegmentsExcluded"),
        partially_covered_attempts=_int(population, "partiallyCoveredAttempts"),
        missing_weapon_identity_rows=_int(population, "missingWeaponIdentityRows"),
        inconsistent_damage_share_rows=_int(population, "inconsistentDamageShareRows"),
        unmapped_acquisition_rows=_int(adoption.iloc[0], "unmappedAcquisitionRows") if not adoption.empty else 0,
        discarded_dps_samples=discarded,
    )
    warnings = _weapon_warnings(sample, quality, adoption, dps, boss, outcome, state, thresholds)

    adoption_ranked = adoption.sort_values(["attemptInclusionRatio", "combatObservedAttempts", "weaponFamilyId"], ascending=[False, False, True], kind="mergesort") if not adoption.empty else adoption
    ownership_ranked = adoption.sort_values(["finalOwnershipRatio", "finalOwnedAttempts", "weaponFamilyId"], ascending=[False, False, True], kind="mergesort") if not adoption.empty else adoption
    combat_ranked = family_combat.sort_values(["damageShareMedian", "combatObservedAttempts", "weaponFamilyId"], ascending=[False, False, True], kind="mergesort") if not family_combat.empty else family_combat
    qualified_dps = family_dps[
        (pd.to_numeric(family_dps.get("validSampleCount", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_valid_dps_samples)
        & (pd.to_numeric(family_dps.get("dpsCoverageAmongCombatObservedInstanceSegments", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_dps_coverage)
    ] if not family_dps.empty else family_dps
    dps_ranked = qualified_dps.sort_values(["effectiveDpsWeighted", "validSampleCount", "weaponFamilyId"], ascending=[False, False, True], kind="mergesort") if not qualified_dps.empty else qualified_dps
    boss_ranked = boss.sort_values(["totalBossDamage", "familyBossEligibleSegments", "weaponFamilyId"], ascending=[False, False, True], kind="mergesort") if not boss.empty else boss
    outcome_signal = outcome[
        (pd.to_numeric(outcome.get("presentFinalAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_outcome_cohort_attempts)
        & (pd.to_numeric(outcome.get("absentFinalAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_outcome_cohort_attempts)
        & (pd.to_numeric(outcome.get("clearRateDifferencePp", pd.Series(dtype=float)), errors="coerce").abs().fillna(0) >= thresholds.min_notable_clear_rate_difference * 100)
    ] if not outcome.empty else outcome
    outcome_ranked = outcome_signal.assign(_absolute=outcome_signal["clearRateDifferencePp"].abs()).sort_values(
        ["_absolute", "presentFinalAttempts", "weaponFamilyId"], ascending=[False, False, True], kind="mergesort"
    ).drop(columns="_absolute") if not outcome_signal.empty else outcome_signal

    valid_sample_count = int(pd.to_numeric(family_dps.get("validSampleCount", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    invalid_sample_count = int(pd.to_numeric(family_dps.get("invalidSampleCount", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    boss_eligible = int(pd.to_numeric(boss.get("bossEligibleSegments", pd.Series(dtype=float)), errors="coerce").fillna(0).max()) if not boss.empty else 0
    valid_boss_samples = int(pd.to_numeric(boss.get("validBossSampleCount", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not boss.empty else 0
    total_damage = float(pd.to_numeric(family_combat.get("totalDamage", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total_boss_damage = float(pd.to_numeric(family_combat.get("totalBossDamage", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total_hits = int(pd.to_numeric(family_combat.get("totalHits", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())

    metrics_model = WeaponPerformanceMetrics(
        sample=sample,
        adoption=WeaponAdoptionMetrics(sample.weapon_family_count, _rows(adoption_ranked), _rows(ownership_ranked)),
        combat_performance=WeaponCombatMetrics(total_damage, total_boss_damage, total_hits, _rows(combat_ranked)),
        dps=WeaponDpsMetrics(valid_sample_count, invalid_sample_count,
            quality.dps_coverage_among_combat_observed_instance_segments, _rows(dps_ranked)),
        boss_performance=WeaponBossMetrics(boss_eligible, valid_boss_samples, _rows(boss_ranked)),
        outcome_association=WeaponOutcomeAssociationMetrics(len(outcome), _rows(outcome_ranked)),
        progression_state=WeaponProgressionMetrics(final_state_assessed, _rows(ownership_ranked)),
        data_quality=quality,
    )
    metrics = to_external(metrics_model)
    signals: list[str] = []
    for _, row in adoption_ranked.head(10).iterrows():
        if _int(row, "combatObservedAttempts") >= thresholds.min_combat_observed_attempts:
            signals.append(f"{row['weaponFamilyId']} produced positive applied damage in {_float(row, 'attemptInclusionRatio'):.1%} of fully covered attempts ({_int(row, 'combatObservedAttempts')}/{_int(row, 'eligibleAttempts')}).")
    for _, row in outcome_ranked.head(10).iterrows():
        signals.append(f"{row['weaponFamilyId']} {row['associationDefinition']} present-cohort clear rate differs from the absent cohort by {_float(row, 'clearRateDifferencePp'):.1f} percentage points ({_int(row, 'presentFinalAttempts')} present, {_int(row, 'absentFinalAttempts')} absent).")

    state_rows = state[state.get("rowType", pd.Series(dtype=str)).eq("state")].drop(columns=["rowType", "effectiveLevel"], errors="ignore")
    level_rows = state[state.get("rowType", pd.Series(dtype=str)).eq("level")].drop(columns=["rowType"], errors="ignore")
    tables = {
        "weapon_adoption.csv": adoption,
        "weapon_final_ownership.csv": adoption,
        "weapon_combat_performance.csv": combat,
        "weapon_dps.csv": dps,
        "weapon_dps_invalid_reasons.csv": dps_invalid,
        "weapon_boss_performance.csv": boss,
        "weapon_outcome_association.csv": outcome,
        "weapon_state_breakdown.csv": state_rows,
        "weapon_level_breakdown.csv": level_rows,
        "weapon_data_quality.csv": _quality_table(quality),
    }
    definitions = WeaponReportDefinitions(
        population="Final attempts from telemetry_attempt_outcomes_v1; weapon detail uses fully covered attempts only.",
        clear_rate="Clear / (Clear + Dead); Abandon is excluded.",
        detail_eligibility="Every candidate gameplay segment must match scope and be telemetryComplete as of the shared snapshot.",
        outcome_observation_unit="finalAttempt",
        adoption_observation_unit="eligibleGameplaySegment and fullyCoveredFinalAttempt",
        combat_observation_unit="combatObservedWeaponInstanceSegment aggregated across a fully covered attempt",
        dps_observation_unit="validWeaponAnalyticsSample",
        final_ownership_observation_unit="finalAttempt final gameplay run weapon state",
        boss_observation_unit="eligibleGameplaySegment with BossStarted",
        dps_coverage="Valid DPS sample coverage among positive-damage combat-observed instance-segments; not all owned or used weapons.",
        notes=(
            "combatObservedSegments counts eligible gameplay segments, not attempts.",
            "Outcome associations are observational and not causal.",
            "Elapsed-time differences are descriptive and excluded from automatic signals.",
        ),
    )
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION, "weaponPerformance", ANALYSIS_VERSION, generated_at_utc,
        request.to_scope(analysis_as_of_utc), sample, quality, definitions, warnings, estimated_bytes,
    )
    markdown = _render_markdown(request, sample, quality, warnings, signals, tables)
    return ReportBundle(metadata, metrics, markdown, tables)


def analyze_weapon_performance(
    request: WeaponPerformanceRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: WeaponThresholds = WeaponThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> WeaponPerformanceAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be non-negative")
    execution_started_at_utc = clock()
    if execution_started_at_utc.tzinfo is None or execution_started_at_utc.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    execution_started_at_utc = execution_started_at_utc.astimezone(timezone.utc).replace(microsecond=0)
    analysis_as_of_utc = (
        request.analysis_as_of_utc.astimezone(timezone.utc).replace(microsecond=0)
        if request.analysis_as_of_utc is not None else execution_started_at_utc
    )
    resolved_request = replace(request, analysis_as_of_utc=analysis_as_of_utc)
    queries = build_weapon_performance_queries(
        resolved_request, config=config, analysis_as_of_utc=analysis_as_of_utc
    )
    estimates = {
        name: dry_run_query(query, client=client, config=config).total_bytes_processed
        for name, query in queries.items()
    }
    total = sum(estimates.values())
    if total > maximum_total_bytes:
        raise RuntimeError(
            f"Weapon Performance dry-run estimate {total:,} bytes exceeds maximum "
            f"{maximum_total_bytes:,} bytes; no analysis query was executed"
        )
    frames = {
        name: query_dataframe(
            query, client=client, config=config, maximum_bytes_billed=maximum_total_bytes
        )
        for name, query in queries.items()
    }
    bundle = _assemble_bundle(
        resolved_request, analysis_as_of_utc, frames, estimated_bytes=total,
        thresholds=thresholds, generated_at_utc=execution_started_at_utc,
    )
    return WeaponPerformanceAnalysis(bundle, total, estimates)


def generate_weapon_performance_report(
    request: WeaponPerformanceRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: WeaponThresholds = WeaponThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    analysis = analyze_weapon_performance(
        request, client=client, config=config, maximum_total_bytes=maximum_total_bytes,
        thresholds=thresholds, clock=clock,
    )
    return write_report_bundle(
        analysis.bundle, output_root=output_root, overwrite=overwrite, table_specs=TABLE_SPECS
    )
