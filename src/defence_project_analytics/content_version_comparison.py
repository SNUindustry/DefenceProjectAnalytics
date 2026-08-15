"""B-6 ContentVersion comparison built from existing aggregate analyses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from defence_project_analytics.analysis_execution import (
    PreparedQueryBatch,
    execute_prepared_query_batch,
    prepare_query_batch,
)
from defence_project_analytics.comparison import (
    comparison_status,
    metric_ratio_from_mapping,
    ratio_comparison_row,
    scalar_comparison_row,
)
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec
from defence_project_analytics.metric_registry import (
    COMPARISON_SUMMARY_SPECS,
    COMPARISON_TABLE_SPECS,
)
from defence_project_analytics.post_run_behavior import (
    PostRunBehaviorRequest,
    _assemble_bundle as _assemble_post_run,
    build_post_run_behavior_queries,
)
from defence_project_analytics.progression_next_run import (
    ProgressionNextRunRequest,
    _assemble_bundle as _assemble_progression,
    build_progression_next_run_queries,
)
from defence_project_analytics.reporting.models import (
    REPORT_CONTRACT_VERSION,
    ComparisonDataQuality,
    ComparisonRow,
    ComparisonWarning,
    ContentVersionAnalysisSnapshot,
    ContentVersionComparisonAnalysis,
    ContentVersionComparisonDefinitions,
    ContentVersionComparisonReportMetadata,
    ContentVersionComparisonSample,
    ContentVersionComparisonScope,
    DomainComparison,
    MetricRatio,
    ReportBundle,
    SourceAnalysisManifest,
    SourceAnalysisSnapshot,
    utc_now_seconds,
)
from defence_project_analytics.reporting.renderers import attached_tables_markdown, to_external
from defence_project_analytics.reporting.warnings import (
    COMPARISON_WARNING_PRIORITY,
    ComparisonThresholds,
    PostRunThresholds,
    ProgressionThresholds,
    UpgradeThresholds,
    WarningThresholds,
    WeaponThresholds,
    sort_comparison_codes,
)
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_query
from defence_project_analytics.stage_difficulty import (
    StageDifficultyRequest,
    _assemble_bundle as _assemble_stage,
    build_stage_difficulty_queries,
)
from defence_project_analytics.upgrade_choice import (
    UpgradeChoiceRequest,
    _assemble_bundle as _assemble_upgrade,
    build_upgrade_choice_queries,
)
from defence_project_analytics.weapon_performance import (
    WeaponPerformanceRequest,
    _assemble_bundle as _assemble_weapon,
    build_weapon_performance_queries,
)


ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000

STAGE = "stageDifficulty"
WEAPON = "weaponPerformance"
UPGRADE = "upgradeChoice"
PROGRESSION = "progressionNextRun"
POST_RUN = "postRunBehavior"
DOMAIN_ORDER = (STAGE, WEAPON, UPGRADE, PROGRESSION, POST_RUN)
STAGE_DOMAINS = frozenset((STAGE, WEAPON, UPGRADE))
DOMAIN_ALIASES = {
    "stage": STAGE,
    "stageDifficulty": STAGE,
    "weapon": WEAPON,
    "weaponPerformance": WEAPON,
    "upgrade": UPGRADE,
    "upgradeChoice": UPGRADE,
    "progression": PROGRESSION,
    "progressionNextRun": PROGRESSION,
    "post-run": POST_RUN,
    "postRun": POST_RUN,
    "postRunBehavior": POST_RUN,
}

PROFILE_SQL = "sql/analysis/content_version_comparison_profile_v1.sql"
STAGE_SNAPSHOT_GUARANTEE = (
    "Stage Difficulty comparison snapshots use an uploadedAtUtc ingestion cutoff and "
    "do not provide a BigQuery historical system-time snapshot."
)
AS_OF_SNAPSHOT_GUARANTEE = (
    "analysisAsOfUtc is a telemetry-row cutoff parameter and is not a BigQuery "
    "historical system-time snapshot."
)

COMPARISON_COLUMNS = (
    "domain", "metricFamily", "metric", "entityType", "entityKey", "dimension",
    "dimensionValue", "valueType", "unit", "observationUnit", "baselineObserved",
    "candidateObserved", "baselineCount", "baselineDenominator", "baselineValue",
    "candidateCount", "candidateDenominator", "candidateValue", "absoluteDelta",
    "percentagePointDelta", "relativeDelta", "direction", "status", "warningCodes",
)
TABLE_SPECS = {
    "comparison_stage.csv": (COMPARISON_COLUMNS, ("metricFamily", "metric", "entityKey", "dimensionValue")),
    "comparison_weapon.csv": (COMPARISON_COLUMNS, ("metricFamily", "metric", "entityKey", "dimensionValue")),
    "comparison_upgrade.csv": (COMPARISON_COLUMNS, ("metricFamily", "metric", "entityKey", "dimensionValue")),
    "comparison_progression.csv": (COMPARISON_COLUMNS, ("metricFamily", "metric", "entityKey", "dimensionValue")),
    "comparison_post_run.csv": (COMPARISON_COLUMNS, ("metricFamily", "metric", "entityKey", "dimensionValue")),
    "comparison_data_quality.csv": ((
        "domain", "metric", "baselineValue", "candidateValue", "absoluteDelta",
        "status", "warningCodes",
    ), ("domain", "metric")),
}


@dataclass(frozen=True, slots=True)
class ContentVersionCompareRequest:
    environment: str
    baseline_content_version: int
    candidate_content_version: int
    stage_key: str | None = None
    domains: tuple[str, ...] | None = None
    app_version: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime | None = None
    previous_run_max_gap_minutes: int = 30
    next_run_max_gap_minutes: int = 30
    post_run_max_gap_minutes: int = 30

    def __post_init__(self) -> None:
        if self.environment not in {"Production", "Test"}:
            raise ValueError("environment must be Production or Test")
        for name in ("baseline_content_version", "candidate_content_version"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.baseline_content_version == self.candidate_content_version:
            raise ValueError("baseline and candidate content versions must differ")
        for name in ("stage_key", "app_version", "release_channel", "release_type"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be omitted or non-empty")
        for name in ("uploaded_at_utc_start", "uploaded_at_utc_end", "analysis_as_of_utc"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.uploaded_at_utc_start is not None
            and self.uploaded_at_utc_end is not None
            and self.uploaded_at_utc_start >= self.uploaded_at_utc_end
        ):
            raise ValueError("uploaded_at_utc_start must be earlier than uploaded_at_utc_end")
        if min(
            self.previous_run_max_gap_minutes,
            self.next_run_max_gap_minutes,
            self.post_run_max_gap_minutes,
        ) <= 0:
            raise ValueError("comparison max-gap minutes must be positive")
        if self.domains is not None:
            normalized = self.resolved_domains()
            if len(normalized) != len(set(normalized)):
                raise ValueError("domains must not contain duplicates")

    def resolved_domains(self) -> tuple[str, ...]:
        if self.domains is None:
            return DOMAIN_ORDER if self.stage_key is not None else (PROGRESSION, POST_RUN)
        result: list[str] = []
        for value in self.domains:
            try:
                domain = DOMAIN_ALIASES[value]
            except KeyError as exc:
                raise ValueError(f"Unsupported comparison domain: {value}") from exc
            if domain in result:
                raise ValueError("domains must not contain duplicates")
            result.append(domain)
        ordered = tuple(domain for domain in DOMAIN_ORDER if domain in result)
        if not ordered:
            raise ValueError("at least one comparison domain is required")
        if self.stage_key is None and STAGE_DOMAINS.intersection(ordered):
            raise ValueError("stage_key is required for stage, weapon, and upgrade domains")
        return ordered

    def resolved_as_of(self, clock: Callable[[], datetime] = utc_now_seconds) -> datetime:
        value = self.analysis_as_of_utc or clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_as_of_utc must be timezone-aware")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def effective_uploaded_end(self, as_of: datetime) -> datetime:
        requested = self.uploaded_at_utc_end
        value = as_of if requested is None else min(requested.astimezone(timezone.utc), as_of)
        if self.uploaded_at_utc_start is not None and self.uploaded_at_utc_start >= value:
            raise ValueError("uploaded_at_utc_start must be earlier than the effective upload cutoff")
        return value

    def to_scope(self, as_of: datetime) -> ContentVersionComparisonScope:
        return ContentVersionComparisonScope(
            environment=self.environment,
            baseline_content_version=self.baseline_content_version,
            candidate_content_version=self.candidate_content_version,
            stage_key=self.stage_key,
            domains=self.resolved_domains(),
            app_version=self.app_version,
            release_channel=self.release_channel,
            release_type=self.release_type,
            is_development_build=self.is_development_build,
            uploaded_at_utc_start=self.uploaded_at_utc_start,
            uploaded_at_utc_end=self.effective_uploaded_end(as_of),
            analysis_as_of_utc=as_of,
            previous_run_max_gap_minutes=self.previous_run_max_gap_minutes,
            next_run_max_gap_minutes=self.next_run_max_gap_minutes,
            post_run_max_gap_minutes=self.post_run_max_gap_minutes,
        )


@dataclass(frozen=True, slots=True)
class _SourcePlan:
    side: str
    domain: str
    request: Any
    query_names: tuple[str, ...]


def _typed(value: Any, bigquery_type: str) -> QueryParameterValue:
    return QueryParameterValue(value, bigquery_type)


def _source_request(
    request: ContentVersionCompareRequest,
    domain: str,
    content_version: int,
    as_of: datetime,
) -> Any:
    uploaded_end = request.effective_uploaded_end(as_of)
    common = dict(
        environment=request.environment,
        content_version=content_version,
        app_version=request.app_version,
        release_channel=request.release_channel,
        release_type=request.release_type,
        is_development_build=request.is_development_build,
        uploaded_at_utc_start=request.uploaded_at_utc_start,
        uploaded_at_utc_end=uploaded_end,
    )
    if domain == STAGE:
        return StageDifficultyRequest(stage_key=request.stage_key or "", **common)
    if domain == WEAPON:
        return WeaponPerformanceRequest(
            stage_key=request.stage_key or "", analysis_as_of_utc=as_of, **common
        )
    if domain == UPGRADE:
        return UpgradeChoiceRequest(
            stage_key=request.stage_key or "", analysis_as_of_utc=as_of, **common
        )
    if domain == PROGRESSION:
        return ProgressionNextRunRequest(
            previous_stage_key=request.stage_key,
            next_stage_key=request.stage_key,
            analysis_as_of_utc=as_of,
            previous_run_max_gap_minutes=request.previous_run_max_gap_minutes,
            next_run_max_gap_minutes=request.next_run_max_gap_minutes,
            **common,
        )
    if domain == POST_RUN:
        return PostRunBehaviorRequest(
            stage_key=request.stage_key,
            analysis_as_of_utc=as_of,
            post_run_max_gap_minutes=request.post_run_max_gap_minutes,
            **common,
        )
    raise ValueError(f"Unsupported comparison domain: {domain}")


def _source_queries(
    domain: str,
    request: Any,
    *,
    config: AnalyticsConfig | None,
    as_of: datetime,
) -> Mapping[str, QuerySpec]:
    if domain == STAGE:
        return build_stage_difficulty_queries(request, config=config)
    if domain == WEAPON:
        return build_weapon_performance_queries(
            request, config=config, analysis_as_of_utc=as_of
        )
    if domain == UPGRADE:
        return build_upgrade_choice_queries(
            request, config=config, analysis_as_of_utc=as_of
        )
    if domain == PROGRESSION:
        return build_progression_next_run_queries(
            request, config=config, analysis_as_of_utc=as_of
        )
    if domain == POST_RUN:
        return build_post_run_behavior_queries(
            request, config=config, analysis_as_of_utc=as_of
        )
    raise ValueError(f"Unsupported comparison domain: {domain}")


def _profile_query(
    request: ContentVersionCompareRequest,
    as_of: datetime,
    *,
    config: AnalyticsConfig | None,
) -> QuerySpec:
    return load_query(
        PROFILE_SQL,
        config=config,
        parameters={
            "environment": request.environment,
            "baseline_content_version": _typed(request.baseline_content_version, "INT64"),
            "candidate_content_version": _typed(request.candidate_content_version, "INT64"),
            "stage_key": _typed(request.stage_key, "STRING"),
            "app_version": _typed(request.app_version, "STRING"),
            "release_channel": _typed(request.release_channel, "STRING"),
            "release_type": _typed(request.release_type, "STRING"),
            "is_development_build": _typed(request.is_development_build, "BOOL"),
            "uploaded_start_utc": _typed(request.uploaded_at_utc_start, "TIMESTAMP"),
            "uploaded_end_utc": _typed(request.effective_uploaded_end(as_of), "TIMESTAMP"),
        },
    )


def build_content_version_comparison_queries(
    request: ContentVersionCompareRequest,
    *,
    config: AnalyticsConfig | None = None,
    analysis_as_of_utc: datetime | None = None,
    clock: Callable[[], datetime] = utc_now_seconds,
) -> tuple[dict[str, QuerySpec], tuple[_SourcePlan, ...]]:
    as_of = analysis_as_of_utc or request.resolved_as_of(clock)
    queries: dict[str, QuerySpec] = {"profile": _profile_query(request, as_of, config=config)}
    plans: list[_SourcePlan] = []
    for side, version in (
        ("baseline", request.baseline_content_version),
        ("candidate", request.candidate_content_version),
    ):
        for domain in request.resolved_domains():
            source_request = _source_request(request, domain, version, as_of)
            source_queries = _source_queries(domain, source_request, config=config, as_of=as_of)
            names: list[str] = []
            for name, query in source_queries.items():
                qualified = f"{side}__{domain}__{name}"
                queries[qualified] = query
                names.append(qualified)
            plans.append(_SourcePlan(side, domain, source_request, tuple(names)))
    return queries, tuple(plans)


def _profile_lookup(frame: pd.DataFrame) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        side = str(row.get("side", ""))
        domain = str(row.get("domain", ""))
        normalized: dict[str, Any] = {}
        array_fields = {
            "appVersions", "releaseIds", "releaseChannels", "releaseTypes", "catalogHashes",
        }
        for key, value in row.items():
            if key in {"side", "domain"}:
                continue
            if key in array_fields:
                if value is None:
                    normalized[key] = []
                elif hasattr(value, "tolist"):
                    listed = value.tolist()
                    normalized[key] = listed if isinstance(listed, list) else [listed]
                elif isinstance(value, (list, tuple)):
                    normalized[key] = list(value)
                else:
                    normalized[key] = [value]
            else:
                normalized[key] = to_external(value)
        result[(side, domain)] = normalized
    return result


def _assemble_source_bundle(
    plan: _SourcePlan,
    frames: Mapping[str, pd.DataFrame],
    *,
    as_of: datetime,
    generated_at: datetime,
    estimated_bytes: int,
) -> ReportBundle:
    request = plan.request
    if plan.domain == STAGE:
        return _assemble_stage(
            request,
            frames,
            estimated_bytes=estimated_bytes,
            thresholds=WarningThresholds(),
            clock=lambda: generated_at,
        )
    if plan.domain == WEAPON:
        return _assemble_weapon(
            request,
            as_of,
            frames,
            estimated_bytes=estimated_bytes,
            thresholds=WeaponThresholds(),
            generated_at_utc=generated_at,
        )
    if plan.domain == UPGRADE:
        return _assemble_upgrade(
            request,
            as_of,
            frames,
            estimated_bytes=estimated_bytes,
            thresholds=UpgradeThresholds(),
            generated_at_utc=generated_at,
        )
    if plan.domain == PROGRESSION:
        return _assemble_progression(
            request,
            as_of,
            frames,
            estimated_bytes=estimated_bytes,
            thresholds=ProgressionThresholds(),
            generated_at_utc=generated_at,
        )
    if plan.domain == POST_RUN:
        return _assemble_post_run(
            request,
            as_of,
            frames,
            estimated_bytes=estimated_bytes,
            thresholds=PostRunThresholds(),
            generated_at_utc=generated_at,
        )
    raise ValueError(f"Unsupported comparison domain: {plan.domain}")


def _snapshot(
    plan: _SourcePlan,
    bundle: ReportBundle,
    *,
    as_of: datetime,
    profile: Mapping[str, Any],
) -> SourceAnalysisSnapshot:
    metadata = bundle.metadata
    stage = plan.domain == STAGE
    manifest = SourceAnalysisManifest(
        domain=plan.domain,
        side=plan.side,
        analysis_type=metadata.analysis_type,
        analysis_version=metadata.analysis_version,
        report_contract_version=metadata.report_contract_version,
        scope=to_external(metadata.scope),
        generated_at_utc=metadata.generated_at_utc,
        snapshot_mode="uploadedAtUtcUpperBound" if stage else "analysisAsOfUtcParameter",
        snapshot_cutoff_utc=as_of,
        snapshot_guarantee=STAGE_SNAPSHOT_GUARANTEE if stage else AS_OF_SNAPSHOT_GUARANTEE,
        sample=to_external(metadata.sample),
        quality=to_external(metadata.quality),
        definitions=to_external(metadata.definitions),
        warnings=tuple(to_external(item) for item in metadata.warnings),
        cohort_profile=profile,
    )
    return SourceAnalysisSnapshot(
        plan.domain,
        int(plan.request.content_version),
        manifest,
        to_external(bundle.metrics),
        bundle.tables,
    )


def _value(mapping: Mapping[str, Any], path: str) -> Any:
    value: Any = mapping
    for name in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(name)
    return value


PRIMARY_SAMPLE = {
    STAGE: "finalAttempts",
    WEAPON: "detailEligibleAttempts",
    UPGRADE: "completeExposures",
    PROGRESSION: "matureEpisodes",
    POST_RUN: "matureWindows",
}


def _sample(snapshot: SourceAnalysisSnapshot | None, domain: str) -> int:
    if snapshot is None:
        return 0
    value = snapshot.manifest.sample.get(PRIMARY_SAMPLE[domain], 0)
    return int(value or 0)


def _minimum_sample(domain: str, thresholds: ComparisonThresholds) -> int:
    return {
        STAGE: thresholds.min_stage_final_attempts,
        WEAPON: thresholds.min_weapon_detail_attempts,
        UPGRADE: thresholds.min_upgrade_complete_exposures,
        PROGRESSION: thresholds.min_progression_mature_episodes,
        POST_RUN: thresholds.min_post_run_mature_windows,
    }[domain]


COVERAGE_KEYS = {
    STAGE: ("telemetryCompleteRate", "detailCoverageRate"),
    WEAPON: (
        "telemetryCompleteRate", "detailCoverageRate", "weaponSummaryCoverage",
        "finalWeaponStateCoverage", "dpsCoverageAmongCombatObservedInstanceSegments",
    ),
    UPGRADE: (
        "telemetryCompleteRate", "detailCoverageRate", "choiceCoverageRate",
        "attemptChoiceCoverageRate",
    ),
}


def _domain_warning_codes(
    domain: str,
    baseline: SourceAnalysisSnapshot | None,
    candidate: SourceAnalysisSnapshot | None,
    thresholds: ComparisonThresholds,
) -> tuple[str, ...]:
    codes: list[str] = []
    if baseline is None:
        codes.append("BASELINE_DOMAIN_UNAVAILABLE")
    if candidate is None:
        codes.append("CANDIDATE_DOMAIN_UNAVAILABLE")
    if baseline is None or candidate is None:
        return sort_comparison_codes(codes)
    expected_type = domain
    if (
        baseline.manifest.analysis_type != expected_type
        or candidate.manifest.analysis_type != expected_type
    ):
        codes.append("INCOMPATIBLE_METRIC_DEFINITION")
    if (
        baseline.manifest.report_contract_version != REPORT_CONTRACT_VERSION
        or candidate.manifest.report_contract_version != REPORT_CONTRACT_VERSION
        or baseline.manifest.report_contract_version != candidate.manifest.report_contract_version
    ):
        codes.append("INCOMPATIBLE_REPORT_CONTRACT")
    if (
        baseline.manifest.analysis_version != ANALYSIS_VERSION
        or candidate.manifest.analysis_version != ANALYSIS_VERSION
        or baseline.manifest.analysis_version != candidate.manifest.analysis_version
    ):
        codes.append("INCOMPATIBLE_ANALYSIS_VERSION")
    if baseline.manifest.definitions != candidate.manifest.definitions:
        codes.append("INCOMPATIBLE_METRIC_DEFINITION")
    if (
        baseline.manifest.snapshot_mode != candidate.manifest.snapshot_mode
        or baseline.manifest.snapshot_cutoff_utc != candidate.manifest.snapshot_cutoff_utc
    ):
        codes.append("INCOMPATIBLE_METRIC_DEFINITION")
    ignored_scope = {"contentVersion"}
    baseline_scope = {
        key: value for key, value in baseline.manifest.scope.items() if key not in ignored_scope
    }
    candidate_scope = {
        key: value for key, value in candidate.manifest.scope.items() if key not in ignored_scope
    }
    if baseline_scope != candidate_scope:
        codes.append("INCOMPATIBLE_METRIC_DEFINITION")
    if domain == PROGRESSION:
        for key in ("previousRunMaxGapMinutes", "nextRunMaxGapMinutes"):
            if baseline.manifest.scope.get(key) != candidate.manifest.scope.get(key):
                codes.append("INCOMPATIBLE_WINDOW_DEFINITION")
    if domain == POST_RUN and (
        baseline.manifest.scope.get("postRunMaxGapMinutes")
        != candidate.manifest.scope.get("postRunMaxGapMinutes")
    ):
        codes.append("INCOMPATIBLE_WINDOW_DEFINITION")
    baseline_sample = _sample(baseline, domain)
    candidate_sample = _sample(candidate, domain)
    minimum = _minimum_sample(domain, thresholds)
    if baseline_sample < minimum:
        codes.append("LOW_BASELINE_SAMPLE")
    if candidate_sample < minimum:
        codes.append("LOW_CANDIDATE_SAMPLE")
    if baseline_sample and candidate_sample:
        imbalance = max(baseline_sample, candidate_sample) / min(baseline_sample, candidate_sample)
        if imbalance >= thresholds.material_sample_imbalance_ratio:
            codes.append("MATERIAL_SAMPLE_IMBALANCE")
    if baseline.manifest.warnings:
        codes.append("BASELINE_SOURCE_WARNING")
    if candidate.manifest.warnings:
        codes.append("CANDIDATE_SOURCE_WARNING")
    for key in COVERAGE_KEYS.get(domain, ()):
        left = metric_ratio_from_mapping(baseline.manifest.quality.get(key))
        right = metric_ratio_from_mapping(candidate.manifest.quality.get(key))
        if left and right and left.ratio is not None and right.ratio is not None:
            if abs(right.ratio - left.ratio) >= thresholds.material_coverage_difference:
                codes.append("MATERIAL_COVERAGE_DIFFERENCE")
    left_profile = baseline.manifest.cohort_profile
    right_profile = candidate.manifest.cohort_profile
    left_start, left_end = left_profile.get("observedAtUtcMin"), left_profile.get("observedAtUtcMax")
    right_start, right_end = right_profile.get("observedAtUtcMin"), right_profile.get("observedAtUtcMax")
    if all(isinstance(item, str) for item in (left_start, left_end, right_start, right_end)):
        left_start_dt = datetime.fromisoformat(left_start.replace("Z", "+00:00"))
        left_end_dt = datetime.fromisoformat(left_end.replace("Z", "+00:00"))
        right_start_dt = datetime.fromisoformat(right_start.replace("Z", "+00:00"))
        right_end_dt = datetime.fromisoformat(right_end.replace("Z", "+00:00"))
        left_seconds = max(0.0, (left_end_dt - left_start_dt).total_seconds())
        right_seconds = max(0.0, (right_end_dt - right_start_dt).total_seconds())
        disjoint = left_end_dt < right_start_dt or right_end_dt < left_start_dt
        duration_mismatch = (
            min(left_seconds, right_seconds) == 0 < max(left_seconds, right_seconds)
            or (
                min(left_seconds, right_seconds) > 0
                and max(left_seconds, right_seconds) / min(left_seconds, right_seconds) >= 5
            )
        )
        if disjoint or duration_mismatch:
            codes.append("COHORT_TIME_RANGE_DIFFERENCE")
    identity_keys = ("appVersions", "releaseIds", "releaseChannels", "releaseTypes", "catalogHashes")
    if any(
        set(left_profile.get(key, ())) != set(right_profile.get(key, ()))
        for key in identity_keys
    ):
        codes.append("CROSS_RELEASE_IDENTITY_DIFFERENCE")
    return sort_comparison_codes(codes)


SUMMARY_SPECS: Mapping[str, tuple[tuple[str, str, str, str, str], ...]] = {
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
class _TableSpec:
    filename: str
    metric_family: str
    keys: tuple[str, ...]
    metrics: tuple[tuple[str, str, str | None, str | None, str], ...]
    entity_type: str
    observation_unit: str


TABLE_COMPARE_SPECS: Mapping[str, tuple[_TableSpec, ...]] = {
    STAGE: (
        _TableSpec("deaths_by_time_bucket.csv", "deathTiming", ("bucket",), (("deathBucketRate", "ratio", "count", "denominator", "ratio"),), "timeBucket", "timedDeaths"),
        _TableSpec("deaths_by_phase.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "phase", "attributedDeaths"),
        _TableSpec("deaths_by_floor.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "floor", "attributedDeaths"),
        _TableSpec("deaths_by_wave.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "wave", "attributedDeaths"),
        _TableSpec("deaths_by_zone.csv", "deathConcentration", ("value",), (("deathShare", "ratio", "count", "denominator", "ratio"),), "zone", "attributedDeaths"),
        _TableSpec("final_deaths_by_source.csv", "deathCauses", ("label",), (("finalDeathShare", "ratio", "count", "denominator", "ratio"),), "sourceCategory", "attributedDeaths"),
        _TableSpec("deaths_by_enemy.csv", "deathCauses", ("label",), (("finalDeathShare", "ratio", "count", "denominator", "ratio"),), "enemyDefinition", "attributedDeaths"),
        _TableSpec("incoming_damage_by_enemy.csv", "incomingDamage", ("label",), (("totalAppliedDamage", "scalar", None, None, "totalAppliedDamage"), ("hitCount", "scalar", None, None, "hitCount")), "enemyDefinition", "eligibleRuns"),
        _TableSpec("threat_by_outcome.csv", "threat", ("outcome", "metric"), (("median", "scalar", None, None, "median"), ("mean", "scalar", None, None, "mean")), "threatMetric", "eligibleRunSegments"),
    ),
    WEAPON: (
        _TableSpec("weapon_adoption.csv", "adoption", ("weaponFamilyId",), (("attemptInclusionRate", "ratio", "combatObservedAttempts", "eligibleAttempts", "attemptInclusionRatio"), ("startingLoadoutRate", "ratio", "startingLoadoutAttempts", "startLoadoutAssessedAttempts", "startingLoadoutRatio")), "weaponFamily", "eligibleAttempts"),
        _TableSpec("weapon_final_ownership.csv", "finalOwnership", ("weaponFamilyId",), (("finalOwnershipRate", "ratio", "finalOwnedAttempts", "finalStateAssessedAttempts", "finalOwnershipRatio"), ("finalLevelMedian", "scalar", None, None, "finalLevelMedian")), "weaponFamily", "finalStateAssessedAttempts"),
        _TableSpec("weapon_combat_performance.csv", "combat", ("aggregationLevel", "weaponFamilyId", "weaponId"), (("totalDamage", "scalar", None, None, "totalDamage"), ("damageMedian", "scalar", None, None, "damageMedian"), ("damageShareMean", "scalar", None, None, "damageShareMean")), "weapon", "combatObservedAttempts"),
        _TableSpec("weapon_dps.csv", "dps", ("aggregationLevel", "weaponFamilyId", "weaponId"), (("dpsCoverageAmongCombatObservedInstanceSegments", "ratio", "instanceSegmentsWithValidDpsSample", "combatObservedInstanceSegments", "dpsCoverageAmongCombatObservedInstanceSegments"), ("effectiveDpsWeighted", "scalar", None, None, "effectiveDpsWeighted"), ("uptimeMedian", "scalar", None, None, "uptimeMedian")), "weapon", "combatObservedInstanceSegments"),
        _TableSpec("weapon_boss_performance.csv", "boss", ("weaponFamilyId",), (("totalBossDamage", "scalar", None, None, "totalBossDamage"), ("bossDpsWeighted", "scalar", None, None, "bossDpsWeighted")), "weaponFamily", "bossEligibleSegments"),
        _TableSpec("weapon_outcome_association.csv", "outcomeAssociation", ("associationDefinition", "weaponFamilyId"), (("presentClearRate", "ratio", "presentClears", "presentDeaths", "presentClearRate"), ("absentClearRate", "ratio", "absentClears", "absentDeaths", "absentClearRate")), "weaponFamilyAssociation", "finalAttempts"),
    ),
    UPGRADE: (
        _TableSpec("upgrade_candidate_exposure.csv", "candidate", ("candidateKey",), (("pickRate", "ratio", "pickRateCount", "pickRateDenominator", "pickRate"), ("completeExposureCount", "scalar", None, None, "completeExposureCount")), "upgradeCandidate", "completeExposures"),
        _TableSpec("upgrade_candidate_position.csv", "position", ("candidateKey", "dimension", "dimensionValue"), (("pickRate", "ratio", "pickRateCount", "pickRateDenominator", "pickRate"),), "upgradeCandidatePosition", "completeExposures"),
        _TableSpec("upgrade_head_to_head.csv", "headToHead", ("candidateA", "candidateB"), (("aConditionalPreference", "ratio", "aConditionalPreferenceCount", "aConditionalPreferenceDenominator", "aConditionalPreference"), ("bConditionalPreference", "ratio", "bConditionalPreferenceCount", "bConditionalPreferenceDenominator", "bConditionalPreference")), "upgradeCandidatePair", "coExposures"),
        _TableSpec("upgrade_outcome_association.csv", "outcomeAssociation", ("candidateKey",), (("selectedClearRate", "ratio", "selectedClears", "selectedDeaths", "selectedClearRate"), ("exposedNotSelectedClearRate", "ratio", "exposedNotSelectedClears", "exposedNotSelectedDeaths", "exposedNotSelectedClearRate"), ("alternativeSelectedAttempts", "scalar", None, None, "alternativeSelectedAttempts"), ("noSelectionOnlyAttempts", "scalar", None, None, "noSelectionOnlyAttempts")), "upgradeCandidate", "fullyChoiceCoveredAttempts"),
    ),
    PROGRESSION: (
        _TableSpec("progression_activity.csv", "activity", ("progressionKind", "targetId", "secondaryId"), (("eventCount", "scalar", None, None, "eventCount"), ("nextRunRate", "scalar", None, None, "nextRunRate")), "progressionTarget", "progressionEvents"),
        _TableSpec("progression_next_run.csv", "nextRun", ("dimension", "progressionKind"), (("nextRunRate", "ratio", "nextRunCount", "nextRunRateDenominator", "nextRunRate"), ("sameStageRate", "ratio", "sameStageCount", "sameStageDenominator", "sameStageRate"), ("timeToNextRunMedian", "scalar", None, None, "timeToNextRunMedian")), "progressionCohort", "matureEpisodes"),
        _TableSpec("progression_outcome_transitions.csv", "outcomeTransition", ("pairScope", "previousOutcome", "nextOutcome"), (("transitionRate", "ratio", "transitionCount", "transitionDenominator", "transitionRatio"),), "outcomeTransition", "pairedEpisodes"),
        _TableSpec("progression_paired_elapsed.csv", "pairedElapsed", ("pairScope",), (("deltaMedian", "scalar", None, None, "deltaMedian"), ("pairedCount", "scalar", None, None, "pairedCount")), "pairScope", "pairedEpisodes"),
        _TableSpec("progression_cooccurrence.csv", "coOccurrence", ("kindA", "kindB"), (("coOccurrenceShare", "ratio", "coOccurrenceEpisodeCount", "shareDenominator", "shareOfMultiProgressionEpisodes"),), "progressionKindPair", "multiProgressionEpisodes"),
    ),
    POST_RUN: (
        _TableSpec("post_run_navigation.csv", "navigation", ("anchorOutcome", "dimension"), (("viewedRate", "ratio", "viewedWindows", "viewedDenominator", "viewedRate"), ("userNavigatedRate", "ratio", "userNavigatedWindows", "userNavigatedDenominator", "userNavigatedRate")), "navigationDimension", "matureWindows"),
        _TableSpec("post_run_feedback_behavior.csv", "feedback", ("anchorOutcome", "feedbackCohort"), (("shopPresentedRate", "ratio", "shopPresentedCount", "windowCount", "shopPresentedRate"), ("shopUserNavigatedRate", "ratio", "shopUserNavigatedCount", "windowCount", "shopUserNavigatedRate"), ("committedSuccessWindowRate", "ratio", "committedSuccessCount", "windowCount", "committedSuccessWindowRate"), ("nextRunRate", "ratio", "nextRunCount", "windowCount", "nextRunRate")), "feedbackCohort", "matureWindows"),
        _TableSpec("post_run_commerce.csv", "commerce", ("rowType", "anchorOutcome", "dimension"), (("observedAttemptSuccessRate", "ratio", "linkedSucceededAttemptCount", "observedAttemptCount", "observedAttemptSuccessRate"), ("committedSuccessWindowRate", "ratio", "committedSuccessWindows", "matureWindows", "committedSuccessWindowRate")), "commerceCategory", "commerceOperationsOrWindows"),
        _TableSpec("post_run_progression.csv", "progression", ("anchorOutcome", "progressionKind"), (("progressionRate", "ratio", "progressionWindows", "windowCount", "progressionRate"),), "progressionKind", "matureWindows"),
        _TableSpec("post_run_next_run.csv", "nextRun", ("anchorOutcome",), (("nextRunRate", "ratio", "nextRunWithinWindow", "nextRunRateDenominator", "nextRunRate"), ("sameStageRetryRate", "ratio", "sameStageRetryCount", "sameStageRetryDenominator", "sameStageRetryRate"), ("timeToNextRunMedian", "scalar", None, None, "timeToNextRunMedian")), "anchorOutcome", "matureWindows"),
        _TableSpec("post_run_action_transitions.csv", "actionSequence", ("anchorOutcome", "fromAction", "toAction"), (("transitionRate", "ratio", "actionCount", "denominator", "ratio"),), "actionPair", "sourceActions"),
    ),
}


def _frame_rows(frame: Any, keys: Sequence[str]) -> dict[tuple[str, ...], Mapping[str, Any]]:
    if frame is None:
        return {}
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)
    result: dict[tuple[str, ...], Mapping[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        key = tuple("" if row.get(name) is None else str(row.get(name)) for name in keys)
        result[key] = row
    return result


def _ratio_from_row(
    row: Mapping[str, Any] | None,
    count_column: str,
    denominator_column: str,
    ratio_column: str,
) -> MetricRatio | None:
    if row is None:
        return None
    count = row.get(count_column)
    denominator = row.get(denominator_column)
    if count is None or denominator is None:
        return None
    # Some source tables expose clears and deaths separately; the rate denominator is their sum.
    if ratio_column in {"presentClearRate", "absentClearRate", "selectedClearRate", "exposedNotSelectedClearRate"}:
        denominator = int(count or 0) + int(denominator or 0)
    return MetricRatio(
        int(count or 0),
        int(denominator or 0),
        None if row.get(ratio_column) is None else float(row[ratio_column]),
    )


def _table_comparison_rows(
    domain: str,
    baseline: SourceAnalysisSnapshot,
    candidate: SourceAnalysisSnapshot,
    base_codes: tuple[str, ...],
    thresholds: ComparisonThresholds,
) -> list[ComparisonRow]:
    rows: list[ComparisonRow] = []
    for spec in COMPARISON_TABLE_SPECS.get(domain, ()):
        left = _frame_rows(baseline.tables.get(spec.filename), spec.keys)
        right = _frame_rows(candidate.tables.get(spec.filename), spec.keys)
        for key in sorted(set(left).union(right)):
            left_row, right_row = left.get(key), right.get(key)
            entity_key = "|".join(key)
            dimension = spec.keys[-1] if len(spec.keys) > 1 else None
            dimension_value = key[-1] if len(spec.keys) > 1 else None
            for metric, kind, count_col, denominator_col, value_col in spec.metrics:
                if kind == "ratio":
                    left_ratio = _ratio_from_row(left_row, count_col or "", denominator_col or "", value_col)
                    right_ratio = _ratio_from_row(right_row, count_col or "", denominator_col or "", value_col)
                    row_codes = list(base_codes)
                    for side_ratio, side_code in (
                        (left_ratio, "LOW_BASELINE_SAMPLE"),
                        (right_ratio, "LOW_CANDIDATE_SAMPLE"),
                    ):
                        if side_ratio is not None and side_ratio.denominator < thresholds.min_entity_denominator:
                            row_codes.append(side_code)
                    rows.append(ratio_comparison_row(
                        domain=domain, metric_family=spec.metric_family, metric=metric,
                        baseline=left_ratio, candidate=right_ratio,
                        observation_unit=spec.observation_unit, warning_codes=row_codes,
                        entity_type=spec.entity_type, entity_key=entity_key,
                        dimension=dimension, dimension_value=dimension_value,
                        tolerance=thresholds.numerical_tolerance,
                    ))
                else:
                    rows.append(scalar_comparison_row(
                        domain=domain, metric_family=spec.metric_family, metric=metric,
                        baseline=None if left_row is None else left_row.get(value_col),
                        candidate=None if right_row is None else right_row.get(value_col),
                        unit="ratio" if "Share" in metric or "Rate" in metric else metric,
                        observation_unit=spec.observation_unit, warning_codes=base_codes,
                        entity_type=spec.entity_type, entity_key=entity_key,
                        dimension=dimension, dimension_value=dimension_value,
                        tolerance=thresholds.numerical_tolerance,
                    ))
    return rows


def _domain_comparison(
    domain: str,
    baseline: SourceAnalysisSnapshot | None,
    candidate: SourceAnalysisSnapshot | None,
    thresholds: ComparisonThresholds,
) -> DomainComparison:
    codes = _domain_warning_codes(domain, baseline, candidate, thresholds)
    if baseline is None or candidate is None:
        return DomainComparison(
            domain, comparison_status(codes), codes,
            _sample(baseline, domain), _sample(candidate, domain), (), (),
        )
    rows: list[ComparisonRow] = []
    for family, metric, path, kind, unit in COMPARISON_SUMMARY_SPECS[domain]:
        left, right = _value(baseline.metrics, path), _value(candidate.metrics, path)
        if kind == "ratio":
            rows.append(ratio_comparison_row(
                domain=domain, metric_family=family, metric=metric,
                baseline=metric_ratio_from_mapping(left),
                candidate=metric_ratio_from_mapping(right),
                observation_unit=unit, warning_codes=codes,
                tolerance=thresholds.numerical_tolerance,
            ))
        else:
            rows.append(scalar_comparison_row(
                domain=domain, metric_family=family, metric=metric,
                baseline=left, candidate=right, unit=unit, observation_unit=unit,
                warning_codes=codes, tolerance=thresholds.numerical_tolerance,
            ))
    rows.extend(_table_comparison_rows(domain, baseline, candidate, codes, thresholds))
    top_candidates = [
        row for row in rows
        if row.value_type == "ratio"
        and row.status == "Comparable"
        and row.warning_codes == ()
        and row.baseline_denominator is not None
        and row.candidate_denominator is not None
        and row.baseline_denominator >= thresholds.min_entity_denominator
        and row.candidate_denominator >= thresholds.min_entity_denominator
        and row.percentage_point_delta is not None
        and abs(row.percentage_point_delta) >= thresholds.notable_percentage_point_delta
    ]
    top_candidates.sort(key=lambda row: (
        -abs(row.percentage_point_delta or 0),
        -min(row.baseline_denominator or 0, row.candidate_denominator or 0),
        row.metric, row.entity_key or "",
    ))
    top_changes = tuple({
        "metric": row.metric,
        "entityKey": row.entity_key,
        "baselineCount": row.baseline_count,
        "baselineDenominator": row.baseline_denominator,
        "baselineRatio": row.baseline_value,
        "candidateCount": row.candidate_count,
        "candidateDenominator": row.candidate_denominator,
        "candidateRatio": row.candidate_value,
        "percentagePointDelta": row.percentage_point_delta,
        "direction": row.direction,
    } for row in top_candidates[:5])
    if any(code.startswith("INCOMPATIBLE_") for code in codes):
        status = "Incompatible"
    elif all(row.status == "Unavailable" for row in rows):
        status = "Unavailable"
    elif codes or any(row.status == "Limited" for row in rows):
        status = "Limited"
    else:
        status = "Comparable"
    return DomainComparison(
        domain, status, codes, _sample(baseline, domain), _sample(candidate, domain),
        tuple(rows), top_changes,
    )


def compare_content_version_snapshots(
    request: ContentVersionCompareRequest,
    baseline_snapshot: ContentVersionAnalysisSnapshot,
    candidate_snapshot: ContentVersionAnalysisSnapshot,
    *,
    thresholds: ComparisonThresholds = ComparisonThresholds(),
) -> dict[str, DomainComparison]:
    if baseline_snapshot.content_version != request.baseline_content_version:
        raise ValueError("baseline snapshot contentVersion does not match the request")
    if candidate_snapshot.content_version != request.candidate_content_version:
        raise ValueError("candidate snapshot contentVersion does not match the request")
    return {
        domain: _domain_comparison(
            domain,
            baseline_snapshot.sources.get(domain),
            candidate_snapshot.sources.get(domain),
            thresholds,
        )
        for domain in request.resolved_domains()
    }


def _comparison_quality(
    comparisons: Mapping[str, DomainComparison],
    baseline: ContentVersionAnalysisSnapshot,
    candidate: ContentVersionAnalysisSnapshot,
) -> ComparisonDataQuality:
    statuses = [item.status for item in comparisons.values()]
    return ComparisonDataQuality(
        domains_requested=len(statuses),
        domains_comparable=statuses.count("Comparable"),
        domains_limited=statuses.count("Limited"),
        domains_unavailable=statuses.count("Unavailable"),
        domains_incompatible=statuses.count("Incompatible"),
        baseline_source_warning_count=sum(
            len(item.manifest.warnings) for item in baseline.sources.values()
        ),
        candidate_source_warning_count=sum(
            len(item.manifest.warnings) for item in candidate.sources.values()
        ),
    )


def _report_warnings(comparisons: Mapping[str, DomainComparison]) -> tuple[ComparisonWarning, ...]:
    affected: dict[str, list[str]] = {}
    for domain, comparison in comparisons.items():
        for code in comparison.warning_codes:
            affected.setdefault(code, []).append(domain)
        for row in comparison.rows:
            for code in row.warning_codes:
                affected.setdefault(code, []).append(domain)
    if not any(item.status in {"Comparable", "Limited"} for item in comparisons.values()):
        affected.setdefault("NO_COMPARABLE_DOMAINS", []).extend(comparisons)
    messages = {
        "NO_COMPARABLE_DOMAINS": "No requested domain produced a compatible two-sided comparison.",
        "LOW_BASELINE_SAMPLE": "One or more baseline samples are below their comparison threshold.",
        "LOW_CANDIDATE_SAMPLE": "One or more candidate samples are below their comparison threshold.",
        "BASELINE_VALUE_MISSING": "One or more metrics were not observed in the baseline source.",
        "CANDIDATE_VALUE_MISSING": "One or more metrics were not observed in the candidate source.",
        "MATERIAL_SAMPLE_IMBALANCE": "One or more domain samples differ by the configured material ratio.",
        "MATERIAL_COVERAGE_DIFFERENCE": "One or more comparable coverage ratios differ materially.",
        "BASELINE_SOURCE_WARNING": "Baseline source analysis warnings remain applicable.",
        "CANDIDATE_SOURCE_WARNING": "Candidate source analysis warnings remain applicable.",
    }
    priority = {code: index for index, code in enumerate(COMPARISON_WARNING_PRIORITY)}
    return tuple(
        ComparisonWarning(
            code,
            messages.get(code, code.replace("_", " ").capitalize() + "."),
            {"domains": sorted(set(domains))},
        )
        for code, domains in sorted(
            affected.items(), key=lambda item: (priority.get(item[0], len(priority)), item[0])
        )
    )


def _comparison_frames(
    comparisons: Mapping[str, DomainComparison],
    baseline_snapshot: ContentVersionAnalysisSnapshot,
    candidate_snapshot: ContentVersionAnalysisSnapshot,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    filename = {
        STAGE: "comparison_stage.csv",
        WEAPON: "comparison_weapon.csv",
        UPGRADE: "comparison_upgrade.csv",
        PROGRESSION: "comparison_progression.csv",
        POST_RUN: "comparison_post_run.csv",
    }
    tables: dict[str, pd.DataFrame] = {}
    quality_rows: list[dict[str, Any]] = []
    for domain in DOMAIN_ORDER:
        comparison = comparisons.get(domain)
        external_rows: list[Mapping[str, Any]] = []
        if comparison is not None:
            for row in comparison.rows:
                item = to_external(row)
                item["warningCodes"] = "|".join(row.warning_codes)
                external_rows.append(item)
            baseline_source = baseline_snapshot.sources.get(domain)
            candidate_source = candidate_snapshot.sources.get(domain)
            quality_rows.extend((
                {
                    "domain": domain, "metric": "primarySample",
                    "baselineValue": comparison.baseline_sample,
                    "candidateValue": comparison.candidate_sample,
                    "absoluteDelta": comparison.candidate_sample - comparison.baseline_sample,
                    "status": comparison.status,
                    "warningCodes": "|".join(comparison.warning_codes),
                },
                {
                    "domain": domain, "metric": "sourceWarningCount",
                    "baselineValue": 0 if baseline_source is None else len(baseline_source.manifest.warnings),
                    "candidateValue": 0 if candidate_source is None else len(candidate_source.manifest.warnings),
                    "absoluteDelta": (
                        (0 if candidate_source is None else len(candidate_source.manifest.warnings))
                        - (0 if baseline_source is None else len(baseline_source.manifest.warnings))
                    ),
                    "status": comparison.status,
                    "warningCodes": "|".join(comparison.warning_codes),
                },
            ))
            if baseline_source is not None and candidate_source is not None:
                for key in COVERAGE_KEYS.get(domain, ()):
                    left = metric_ratio_from_mapping(baseline_source.manifest.quality.get(key))
                    right = metric_ratio_from_mapping(candidate_source.manifest.quality.get(key))
                    left_value = None if left is None else left.ratio
                    right_value = None if right is None else right.ratio
                    quality_rows.append({
                        "domain": domain,
                        "metric": key,
                        "baselineValue": left_value,
                        "candidateValue": right_value,
                        "absoluteDelta": (
                            None if left_value is None or right_value is None
                            else right_value - left_value
                        ),
                        "status": comparison.status,
                        "warningCodes": "|".join(comparison.warning_codes),
                    })
        tables[filename[domain]] = pd.DataFrame(external_rows)
    return tables, pd.DataFrame(quality_rows)


def _metrics_payload(
    comparisons: Mapping[str, DomainComparison],
    sample: ContentVersionComparisonSample,
    quality: ComparisonDataQuality,
) -> Mapping[str, Any]:
    def domain_payload(domain: str) -> Mapping[str, Any] | None:
        comparison = comparisons.get(domain)
        if comparison is None:
            return None
        summary = [row for row in comparison.rows if row.entity_type is None][:20]
        return {
            "status": comparison.status,
            "warningCodes": comparison.warning_codes,
            "baselineSample": comparison.baseline_sample,
            "candidateSample": comparison.candidate_sample,
            "summaryDeltas": summary,
            "topChanges": comparison.top_changes,
        }
    return {
        "sample": sample,
        "stageDifficulty": domain_payload(STAGE),
        "weaponPerformance": domain_payload(WEAPON),
        "upgradeChoice": domain_payload(UPGRADE),
        "progressionNextRun": domain_payload(PROGRESSION),
        "postRunBehavior": domain_payload(POST_RUN),
        "comparisonDataQuality": quality,
    }


def _markdown(
    request: ContentVersionCompareRequest,
    comparisons: Mapping[str, DomainComparison],
    warnings: Sequence[ComparisonWarning],
    tables: Iterable[str],
) -> str:
    lines = [
        "# ContentVersion Comparison Report",
        "",
        "## Scope",
        "",
        f"- Environment: `{request.environment}`",
        f"- Baseline contentVersion: `{request.baseline_content_version}`",
        f"- Candidate contentVersion: `{request.candidate_content_version}`",
        f"- Stage: `{request.stage_key or 'all-stages'}`",
        "- Comparison direction: `candidate - baseline`",
        "",
        "## Source Comparability",
        "",
        f"- Stage Difficulty snapshot: `{STAGE_SNAPSHOT_GUARANTEE}`",
        f"- Other source snapshots: `{AS_OF_SNAPSHOT_GUARANTEE}`",
    ]
    for domain in request.resolved_domains():
        comparison = comparisons[domain]
        codes = ", ".join(comparison.warning_codes) or "none"
        lines.append(
            f"- {domain}: {comparison.status}; baseline sample "
            f"{comparison.baseline_sample}, candidate sample {comparison.candidate_sample}; "
            f"warnings: {codes}."
        )
    lines.extend(("", "## Sample & Data Quality", ""))
    if warnings:
        lines.extend(f"- `{warning.code}`: {warning.message}" for warning in warnings)
    else:
        lines.append("- No comparison-level warnings were generated.")
    title = {
        STAGE: "Stage Difficulty", WEAPON: "Weapon Performance", UPGRADE: "Upgrade Choice",
        PROGRESSION: "Progression Next-Run", POST_RUN: "Post-Run Behavior",
    }
    for domain in request.resolved_domains():
        comparison = comparisons[domain]
        lines.extend(("", f"## {title[domain]}", ""))
        if comparison.top_changes:
            for change in comparison.top_changes:
                entity = f" for {change['entityKey']}" if change.get("entityKey") else ""
                lines.append(
                    f"- {change['metric']}{entity}: baseline "
                    f"{change['baselineCount']}/{change['baselineDenominator']}, candidate "
                    f"{change['candidateCount']}/{change['candidateDenominator']}, "
                    f"difference {change['percentagePointDelta']:.1f} percentage points."
                )
        else:
            lines.append("- No warning-free ratio change met the deterministic signal threshold.")
    lines.extend((
        "", "## Observed-Only Entities", "",
        "- Open content identities observed on only one side remain missing on the other side; they are not converted to zero.",
        "", "## Notable Statistical Changes", "",
        "- Signals above are descriptive candidate-minus-baseline differences with explicit counts and denominators.",
        "", "## Caveats", "",
        "- ContentVersion comparisons are observational and do not establish cause.",
        "- Count differences can reflect sample size, coverage, timing, or release identity differences.",
        "- Shop presentation remains distinct from user-initiated Shop navigation.",
        "- Observed commerce-attempt resolution remains distinct from durable committed-success presence.",
        "- Missing best-effort telemetry does not prove that an action did not occur.",
        "", "## Attached Tables", "", attached_tables_markdown(f"tables/{name}" for name in tables),
    ))
    return "\n".join(lines) + "\n"


def analyze_content_version_comparison(
    request: ContentVersionCompareRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: ComparisonThresholds = ComparisonThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> ContentVersionComparisonAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be non-negative")
    generated_at = clock()
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    generated_at = generated_at.astimezone(timezone.utc).replace(microsecond=0)
    as_of = request.resolved_as_of(lambda: generated_at)
    queries, plans = build_content_version_comparison_queries(
        request, config=config, analysis_as_of_utc=as_of
    )
    prepared = prepare_query_batch(queries, client=client, config=config)
    total = prepared.total_estimated_bytes
    if total > maximum_total_bytes:
        raise RuntimeError(
            f"ContentVersion comparison dry-run estimate {total:,} bytes exceeds maximum "
            f"{maximum_total_bytes:,} bytes; no analysis query was executed"
        )
    all_frames = execute_prepared_query_batch(
        prepared, client=client, config=config, maximum_bytes_billed=maximum_total_bytes
    )
    profiles = _profile_lookup(all_frames["profile"])
    by_side: dict[str, dict[str, SourceAnalysisSnapshot]] = {
        "baseline": {}, "candidate": {},
    }
    for plan in plans:
        local_frames = {
            qualified.rsplit("__", 1)[-1]: all_frames[qualified]
            for qualified in plan.query_names
        }
        estimated = sum(prepared.estimated_bytes[name] for name in plan.query_names)
        bundle = _assemble_source_bundle(
            plan, local_frames, as_of=as_of, generated_at=generated_at,
            estimated_bytes=estimated,
        )
        by_side[plan.side][plan.domain] = _snapshot(
            plan, bundle, as_of=as_of, profile=profiles.get((plan.side, plan.domain), {})
        )
    baseline = ContentVersionAnalysisSnapshot(
        request.baseline_content_version, by_side["baseline"]
    )
    candidate = ContentVersionAnalysisSnapshot(
        request.candidate_content_version, by_side["candidate"]
    )
    comparisons = compare_content_version_snapshots(
        request, baseline, candidate, thresholds=thresholds
    )
    quality = _comparison_quality(comparisons, baseline, candidate)
    sample = ContentVersionComparisonSample(
        {domain: _sample(baseline.sources.get(domain), domain) for domain in request.resolved_domains()},
        {domain: _sample(candidate.sources.get(domain), domain) for domain in request.resolved_domains()},
    )
    warnings = _report_warnings(comparisons)
    tables, quality_table = _comparison_frames(comparisons, baseline, candidate)
    tables["comparison_data_quality.csv"] = quality_table
    manifests = tuple(
        snapshot.manifest
        for side in (baseline, candidate)
        for domain in request.resolved_domains()
        if (snapshot := side.sources.get(domain)) is not None
    )
    metadata = ContentVersionComparisonReportMetadata(
        REPORT_CONTRACT_VERSION,
        "contentVersionCompare",
        ANALYSIS_VERSION,
        generated_at,
        request.to_scope(as_of),
        sample,
        quality,
        ContentVersionComparisonDefinitions(notes=(
            "Metric status is a coarse comparability state; warningCodes preserve simultaneous quality limitations.",
            "Only the final content-version comparison bundle is written to disk.",
        )),
        manifests,
        warnings,
        total,
    )
    bundle = ReportBundle(
        metadata,
        _metrics_payload(comparisons, sample, quality),
        _markdown(request, comparisons, warnings, tables),
        tables,
    )
    return ContentVersionComparisonAnalysis(
        bundle, baseline, candidate, comparisons, total, prepared.estimated_bytes
    )


def generate_content_version_comparison_report(
    request: ContentVersionCompareRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: ComparisonThresholds = ComparisonThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    analysis = analyze_content_version_comparison(
        request,
        client=client,
        config=config,
        maximum_total_bytes=maximum_total_bytes,
        thresholds=thresholds,
        clock=clock,
    )
    # B-6 intentionally performs exactly one artifact write: the final comparison bundle.
    return write_report_bundle(
        analysis.bundle,
        output_root=output_root,
        overwrite=overwrite,
        table_specs=TABLE_SPECS,
    )
