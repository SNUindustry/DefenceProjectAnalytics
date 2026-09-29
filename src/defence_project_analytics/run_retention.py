"""Read-only run-based retention analysis and aggregate report generation."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
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
    RunRetentionAnalysisScope,
    RunRetentionDataQuality,
    RunRetentionDefinitions,
    RunRetentionDistributionSummary,
    RunRetentionLatencyMetrics,
    RunRetentionMetrics,
    RunRetentionNextRunMetrics,
    RunRetentionObservationMetrics,
    RunRetentionSampleSummary,
    RunRetentionThresholdMetrics,
    utc_now_seconds,
)
from defence_project_analytics.reporting.renderers import attached_tables_markdown, camel_case, to_external
from defence_project_analytics.reporting.warnings import RunRetentionThresholds
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
POPULATION_FRAGMENT = "sql/analysis/_run_retention_population_ctes_v1.sql"
NEXT_ATTEMPT_FRAGMENT = "sql/analysis/_next_new_attempt_ctes_v1.sql"
POPULATION_INCLUDE_MARKER = "-- @include run_retention_population_ctes_v1"
NEXT_ATTEMPT_INCLUDE_MARKER = "-- @include next_new_attempt_ctes_v1"
SQL_FILES = {
    "population": "sql/analysis/run_retention_population_v1.sql",
    "cohorts": "sql/analysis/run_retention_cohorts_v1.sql",
    "nextContext": "sql/analysis/run_retention_next_context_v1.sql",
}
RESTRICTED_SQL_FILE = "sql/analysis/run_retention_restricted_v1.sql"

TABLE_SPECS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "run_retention_summary.csv": ((
        "cohortType", "cohortValue", "anchorFinalAttempts", "eligibleAnchors",
        "nextRunObservedCount", "noNextRunObservedAsOfCount", "nextRunObservedRate",
        "latencyRightCensoredCount", "latencyRightCensoredRate",
        "returnedWithinThresholdCount", "returnedAfterThresholdCount",
        "noNextRunBeyondThresholdCount", "thresholdRightCensoredCount",
        "thresholdExceededCount", "thresholdResolvedDenominator", "thresholdExceededRate",
    ), ("cohortType", "cohortValue")),
    "run_retention_by_outcome.csv": ((
        "cohortType", "cohortValue", "anchorFinalAttempts", "eligibleAnchors",
        "nextRunObservedCount", "noNextRunObservedAsOfCount", "nextRunObservedRate",
        "latencyRightCensoredCount", "latencyRightCensoredRate",
        "returnedWithinThresholdCount", "returnedAfterThresholdCount",
        "noNextRunBeyondThresholdCount", "thresholdRightCensoredCount",
        "thresholdExceededCount", "thresholdResolvedDenominator", "thresholdExceededRate",
    ), ("cohortValue",)),
    "run_retention_by_stage.csv": ((
        "cohortType", "cohortValue", "anchorFinalAttempts", "eligibleAnchors",
        "nextRunObservedCount", "noNextRunObservedAsOfCount", "nextRunObservedRate",
        "latencyRightCensoredCount", "latencyRightCensoredRate",
        "returnedWithinThresholdCount", "returnedAfterThresholdCount",
        "noNextRunBeyondThresholdCount", "thresholdRightCensoredCount",
        "thresholdExceededCount", "thresholdResolvedDenominator", "thresholdExceededRate",
    ), ("cohortValue",)),
    "run_retention_latency.csv": ((
        "distribution", "observedCount", "missingCount", "p50Seconds", "p75Seconds",
        "p90Seconds", "p95Seconds",
    ), ("distribution",)),
    "run_retention_data_quality.csv": (
        ("metric", "count", "denominator", "ratio"), ("metric",)
    ),
}

WARNING_PRIORITY = (
    "NO_RETENTION_ANCHORS",
    "LOW_RETENTION_SAMPLE",
    "MISSING_RETENTION_PLAYER_IDENTITY",
    "CONFLICTING_RETENTION_IDENTITY",
    "INVALID_RETENTION_TIMING",
    "RIGHT_CENSORED_RETENTION",
    "AMBIGUOUS_NEXT_ATTEMPT_ORDER",
    "RUN_START_SNAPSHOT_COVERAGE_LIMITED",
)


@dataclass(frozen=True, slots=True)
class RunRetentionRequest:
    environment: str
    content_version: int
    stage_key: str | None = None
    final_outcome: str | None = None
    app_version: str | None = None
    release_id: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    run_ended_at_utc_start: datetime | None = None
    run_ended_at_utc_end: datetime | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime | None = None
    long_term_no_next_run_threshold_days: int | None = None
    source_upload_grace_hours: int | None = None

    def __post_init__(self) -> None:
        if self.environment not in {"Production", "Test"}:
            raise ValueError("environment must be Production or Test")
        if isinstance(self.content_version, bool) or not isinstance(self.content_version, int):
            raise ValueError("content_version must be an integer")
        if self.final_outcome not in {None, "Clear", "Dead", "Abandon"}:
            raise ValueError("final_outcome must be Clear, Dead, Abandon, or omitted")
        for name in ("stage_key", "app_version", "release_id", "release_channel", "release_type"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be omitted or non-empty")
        for name in (
            "run_ended_at_utc_start", "run_ended_at_utc_end", "uploaded_at_utc_start",
            "uploaded_at_utc_end", "analysis_as_of_utc",
        ):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        for start_name, end_name in (
            ("run_ended_at_utc_start", "run_ended_at_utc_end"),
            ("uploaded_at_utc_start", "uploaded_at_utc_end"),
        ):
            start, end = getattr(self, start_name), getattr(self, end_name)
            if start is not None and end is not None and start >= end:
                raise ValueError(f"{start_name} must be earlier than {end_name}")
        threshold = self.long_term_no_next_run_threshold_days
        grace = self.source_upload_grace_hours
        if (threshold is None) != (grace is None):
            raise ValueError("long-term threshold and source upload grace must be provided together")
        if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0):
            raise ValueError("long_term_no_next_run_threshold_days must be a positive integer")
        if grace is not None and (isinstance(grace, bool) or not isinstance(grace, int) or grace < 0):
            raise ValueError("source_upload_grace_hours must be a non-negative integer")

    def resolved_as_of(self, clock: Callable[[], datetime] = utc_now_seconds) -> datetime:
        value = self.analysis_as_of_utc or clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_as_of_utc must be timezone-aware")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def to_scope(self, analysis_as_of_utc: datetime) -> RunRetentionAnalysisScope:
        return RunRetentionAnalysisScope(
            environment=self.environment,
            content_version=self.content_version,
            stage_key=self.stage_key,
            final_outcome=self.final_outcome,
            app_version=self.app_version,
            release_id=self.release_id,
            release_channel=self.release_channel,
            release_type=self.release_type,
            is_development_build=self.is_development_build,
            run_ended_at_utc_start=self.run_ended_at_utc_start,
            run_ended_at_utc_end=self.run_ended_at_utc_end,
            uploaded_at_utc_start=self.uploaded_at_utc_start,
            uploaded_at_utc_end=self.uploaded_at_utc_end,
            analysis_as_of_utc=analysis_as_of_utc,
            long_term_no_next_run_threshold_days=self.long_term_no_next_run_threshold_days,
            source_upload_grace_hours=self.source_upload_grace_hours,
        )


@dataclass(frozen=True, slots=True)
class RunRetentionAnalysis:
    bundle: ReportBundle
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


def _typed(value: Any, bigquery_type: str) -> QueryParameterValue:
    return QueryParameterValue(value, bigquery_type)


def run_retention_parameters(request: RunRetentionRequest, as_of: datetime) -> dict[str, Any]:
    return {
        "environment": request.environment,
        "content_version": _typed(request.content_version, "INT64"),
        "stage_key": _typed(request.stage_key, "STRING"),
        "final_outcome": _typed(request.final_outcome, "STRING"),
        "app_version": _typed(request.app_version, "STRING"),
        "release_id": _typed(request.release_id, "STRING"),
        "release_channel": _typed(request.release_channel, "STRING"),
        "release_type": _typed(request.release_type, "STRING"),
        "is_development_build": _typed(request.is_development_build, "BOOL"),
        "run_ended_start_utc": _typed(request.run_ended_at_utc_start, "TIMESTAMP"),
        "run_ended_end_utc": _typed(request.run_ended_at_utc_end, "TIMESTAMP"),
        "uploaded_start_utc": _typed(request.uploaded_at_utc_start, "TIMESTAMP"),
        "uploaded_end_utc": _typed(request.uploaded_at_utc_end, "TIMESTAMP"),
        "analysis_as_of_utc": _typed(as_of, "TIMESTAMP"),
        "long_term_threshold_days": _typed(
            request.long_term_no_next_run_threshold_days, "INT64"
        ),
        "source_upload_grace_hours": _typed(request.source_upload_grace_hours, "INT64"),
    }


def build_run_retention_queries(
    request: RunRetentionRequest,
    *,
    config: AnalyticsConfig | None = None,
    analysis_as_of_utc: datetime | None = None,
    clock: Callable[[], datetime] = utc_now_seconds,
) -> dict[str, QuerySpec]:
    as_of = analysis_as_of_utc or request.resolved_as_of(clock)
    population = load_sql(POPULATION_FRAGMENT, config=config)
    next_attempt = load_sql(NEXT_ATTEMPT_FRAGMENT, config=config)
    if population.count(NEXT_ATTEMPT_INCLUDE_MARKER) != 1:
        raise ValueError("Run-retention population SQL must contain one next-attempt marker")
    population = population.replace(NEXT_ATTEMPT_INCLUDE_MARKER, next_attempt)
    parameters = run_retention_parameters(request, as_of)
    result: dict[str, QuerySpec] = {}
    for name, path in SQL_FILES.items():
        sql = load_sql(path, config=config)
        if sql.count(POPULATION_INCLUDE_MARKER) != 1:
            raise ValueError(f"Run-retention SQL must contain one population marker: {path}")
        sql = sql.replace(POPULATION_INCLUDE_MARKER, population)
        expected = named_parameter_names(sql)
        supplied = {key: value for key, value in parameters.items() if key in expected}
        missing = expected.difference(supplied)
        if missing:
            raise ValueError(f"Missing named BigQuery parameters: {', '.join(sorted(missing))}")
        result[name] = QuerySpec(sql, supplied)
    return result


def build_run_retention_restricted_query(
    request: RunRetentionRequest,
    *,
    config: AnalyticsConfig,
    analysis_as_of_utc: datetime | None = None,
    clock: Callable[[], datetime] = utc_now_seconds,
) -> QuerySpec:
    """Build the additive row-level R4 adapter from the unchanged B-7 CTEs."""
    config.require_environment(request.environment)
    if config.backend_name not in {"test", "production"}:
        raise ValueError("B-7 restricted adapter requires an explicit backend")
    as_of = analysis_as_of_utc or request.resolved_as_of(clock)
    population = load_sql(POPULATION_FRAGMENT, config=config)
    next_attempt = load_sql(NEXT_ATTEMPT_FRAGMENT, config=config)
    if population.count(NEXT_ATTEMPT_INCLUDE_MARKER) != 1:
        raise ValueError("Run-retention population SQL must contain one next-attempt marker")
    population = population.replace(NEXT_ATTEMPT_INCLUDE_MARKER, next_attempt)
    sql = load_sql(RESTRICTED_SQL_FILE, config=config)
    if sql.count(POPULATION_INCLUDE_MARKER) != 1:
        raise ValueError("B-7 restricted SQL must contain one population marker")
    sql = sql.replace(POPULATION_INCLUDE_MARKER, population)
    parameters = run_retention_parameters(request, as_of)
    parameters["telemetry_backend"] = QueryParameterValue(config.backend_name, "STRING")
    names = named_parameter_names(sql)
    missing = names - parameters.keys()
    if missing:
        raise ValueError(f"Missing named BigQuery parameters: {', '.join(sorted(missing))}")
    return QuerySpec(sql, {key: parameters[key] for key in names})


def _value(row: pd.Series, name: str, default: Any = None) -> Any:
    value = row.get(name, default)
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return default
    return value.item() if hasattr(value, "item") else value


def _int(row: pd.Series, name: str) -> int:
    return int(_value(row, name, 0))


def _nullable_int(row: pd.Series, name: str) -> int | None:
    value = _value(row, name)
    return None if value is None else int(value)


def _float(row: pd.Series, name: str) -> float | None:
    value = _value(row, name)
    return None if value is None else float(value)


def _external_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns={name: camel_case(str(name)) for name in frame.columns})


def _quality_table(quality: RunRetentionDataQuality) -> pd.DataFrame:
    return pd.DataFrame([
        {"metric": camel_case(item.name), "count": getattr(quality, item.name),
         "denominator": None, "ratio": None}
        for item in fields(quality)
    ])


def _warnings(
    sample: RunRetentionSampleSummary,
    quality: RunRetentionDataQuality,
    threshold_metrics: RunRetentionThresholdMetrics,
    thresholds: RunRetentionThresholds,
) -> tuple[ReportWarning, ...]:
    result: list[ReportWarning] = []

    def add(code: str, message: str) -> None:
        result.append(ReportWarning(code, message))

    if sample.anchor_final_attempts == 0:
        add("NO_RETENTION_ANCHORS", "No canonical final-attempt anchors matched the scope.")
    elif (
        sample.eligible_anchors < thresholds.eligible_anchors
        or sample.next_run_observed < thresholds.observed_next_attempts
        or (
            threshold_metrics.enabled
            and (threshold_metrics.threshold_resolved_denominator or 0)
            < thresholds.threshold_resolved_denominator
        )
    ):
        threshold_sample = (
            f"/threshold-resolved {threshold_metrics.threshold_resolved_denominator}"
            if threshold_metrics.enabled else ""
        )
        add(
            "LOW_RETENTION_SAMPLE",
            f"Eligible/observed samples are {sample.eligible_anchors}/"
            f"{sample.next_run_observed}{threshold_sample}.",
        )
    if quality.missing_player_identity_anchors:
        add("MISSING_RETENTION_PLAYER_IDENTITY", f"{quality.missing_player_identity_anchors} anchors lack linkable player identity.")
    if quality.conflicting_identity_anchors:
        add("CONFLICTING_RETENTION_IDENTITY", f"{quality.conflicting_identity_anchors} anchors have conflicting attempt identity.")
    if quality.invalid_timing_anchors:
        add("INVALID_RETENTION_TIMING", f"{quality.invalid_timing_anchors} anchors have invalid snapshot timing.")
    if quality.latency_right_censored_anchors:
        add("RIGHT_CENSORED_RETENTION", f"{quality.latency_right_censored_anchors} eligible anchors have no observed next new attempt by the snapshot.")
    if quality.ambiguous_next_attempt_order_groups:
        add("AMBIGUOUS_NEXT_ATTEMPT_ORDER", f"Applied deterministic ordering to {quality.ambiguous_next_attempt_order_groups} same-start candidate groups.")
    if quality.missing_run_start_snapshot_rows:
        add("RUN_START_SNAPSHOT_COVERAGE_LIMITED", f"{quality.missing_run_start_snapshot_rows} candidate rows lack an observed run-start snapshot; only rows marked as resume are excluded.")
    priority = {code: index for index, code in enumerate(WARNING_PRIORITY)}
    return tuple(sorted(result, key=lambda item: (priority.get(item.code, len(priority)), item.code)))


def _render_markdown(
    request: RunRetentionRequest,
    sample: RunRetentionSampleSummary,
    latency: RunRetentionLatencyMetrics,
    threshold: RunRetentionThresholdMetrics,
    warnings: tuple[ReportWarning, ...],
    tables: Mapping[str, pd.DataFrame],
) -> str:
    warning_lines = "\n".join(f"- `{item.code}`: {item.message}" for item in warnings) or "- None."
    threshold_lines = (
        "- Threshold classification: disabled; no default threshold was applied."
        if not threshold.enabled else
        f"- Threshold/grace: {threshold.threshold_days} days / {threshold.source_upload_grace_hours} hours\n"
        f"- Returned within/after threshold: {threshold.returned_within_threshold_count}/{threshold.returned_after_threshold_count}\n"
        f"- No next run beyond threshold: {threshold.no_next_run_beyond_threshold_count}\n"
        f"- Threshold exceeded: {threshold.threshold_exceeded_count}/{threshold.threshold_resolved_denominator}"
    )
    return f"""# Run Retention Report

## Scope

- Environment: `{request.environment}`
- Content version: `{request.content_version}`
- Stage: `{request.stage_key or 'all-stages'}`
- Return definition: `NewAttempt`

## Sample and Observation

- Final-attempt anchors: {sample.anchor_final_attempts}
- Eligible/ineligible anchors: {sample.eligible_anchors}/{sample.ineligible_anchors}
- Next new attempt observed: {sample.next_run_observed}/{sample.eligible_anchors}
- No next new attempt observed as of the snapshot: {sample.no_next_run_observed_as_of}

{warning_lines}

## Observed Latency

- Observed next-attempt delay P50/P75/P90/P95 seconds: {latency.observed_delay_seconds.p50}/{latency.observed_delay_seconds.p75}/{latency.observed_delay_seconds.p90}/{latency.observed_delay_seconds.p95}
- Percentiles are conditional on an observed next new attempt and are not a censor-adjusted population distribution.

## Threshold Facts

{threshold_lines}

## Interpretation Constraints

- A return means a new gameplay attempt, not an app foreground event or session start.
- A resume segment is continuation of the same attempt and is not a return.
- No observed next run is not equivalent to player churn or uninstall.
- Player continuity is limited to the current telemetry identity; reinstall or profile reset can break linkage.
- Offline uploads after the snapshot can change a later execution of this report.

## Attached Tables

{attached_tables_markdown(tables)}
"""


def _assemble_bundle(
    request: RunRetentionRequest,
    as_of: datetime,
    frames: Mapping[str, pd.DataFrame],
    *,
    estimated_bytes: int,
    thresholds: RunRetentionThresholds,
    generated_at_utc: datetime,
) -> ReportBundle:
    row = frames["population"].iloc[0] if not frames["population"].empty else pd.Series(dtype="object")
    sample = RunRetentionSampleSummary(
        _int(row, "anchorFinalAttempts"), _int(row, "uniquePlayers"),
        _int(row, "clears"), _int(row, "deaths"), _int(row, "abandons"),
        _int(row, "unrecognizedOutcomes"), _int(row, "eligibleAnchors"),
        _int(row, "ineligibleAnchors"), _int(row, "nextRunObserved"),
        _int(row, "noNextRunObservedAsOf"),
    )
    quality = RunRetentionDataQuality(
        _int(row, "physicalAnchorRows"), _int(row, "dedupedAnchorRows"),
        _int(row, "missingPlayerIdentityAnchors"), _int(row, "missingAnchorEndRows"),
        _int(row, "conflictingIdentityAnchors"), _int(row, "invalidTimingAnchors"),
        _int(row, "physicalNextCandidateRows"), _int(row, "dedupedNextCandidateRows"),
        _int(row, "resumeContinuationsExcluded"), _int(row, "missingRunStartSnapshotRows"),
        _int(row, "ambiguousNextAttemptOrderGroups"), _int(row, "latencyRightCensoredAnchors"),
        _int(row, "thresholdRightCensoredAnchors"), _int(row, "crossStageNextAttempts"),
        _int(row, "crossContentNextAttempts"), _int(row, "crossReleaseNextAttempts"),
        _int(row, "nextOutcomePendingAttempts"),
    )
    threshold_enabled = request.long_term_no_next_run_threshold_days is not None
    threshold_resolved = _nullable_int(row, "thresholdResolvedDenominator") if threshold_enabled else None
    threshold_exceeded = _nullable_int(row, "thresholdExceededCount") if threshold_enabled else None
    threshold_metrics = RunRetentionThresholdMetrics(
        threshold_enabled,
        request.long_term_no_next_run_threshold_days,
        request.source_upload_grace_hours,
        _nullable_int(row, "returnedWithinThresholdCount") if threshold_enabled else None,
        _nullable_int(row, "returnedAfterThresholdCount") if threshold_enabled else None,
        _nullable_int(row, "noNextRunBeyondThresholdCount") if threshold_enabled else None,
        _nullable_int(row, "thresholdRightCensoredAnchors") if threshold_enabled else None,
        threshold_exceeded,
        threshold_resolved,
        (threshold_exceeded / threshold_resolved) if threshold_exceeded is not None and threshold_resolved else None,
    )
    context = frames["nextContext"].iloc[0] if not frames["nextContext"].empty else pd.Series(dtype="object")
    latency = RunRetentionLatencyMetrics(
        RunRetentionDistributionSummary(
            _int(context, "observedNextCount"), sample.no_next_run_observed_as_of,
            _float(context, "nextRunDelayP50"), _float(context, "nextRunDelayP75"),
            _float(context, "nextRunDelayP90"), _float(context, "nextRunDelayP95"),
        ),
        RunRetentionDistributionSummary(
            _int(context, "censoredCount"), sample.next_run_observed,
            _float(context, "censoredObservationAgeP50"),
            _float(context, "censoredObservationAgeP75"),
            _float(context, "censoredObservationAgeP90"),
            _float(context, "censoredObservationAgeP95"),
        ),
    )
    metrics = RunRetentionMetrics(
        sample,
        RunRetentionObservationMetrics(
            sample.eligible_anchors, sample.ineligible_anchors,
            MetricRatio.from_counts(quality.latency_right_censored_anchors, sample.eligible_anchors),
        ),
        RunRetentionNextRunMetrics(
            MetricRatio.from_counts(sample.next_run_observed, sample.eligible_anchors),
            MetricRatio.from_counts(_int(context, "sameStageNextRunCount"), sample.next_run_observed),
            MetricRatio.from_counts(_int(context, "sameContentNextRunCount"), sample.next_run_observed),
            quality.cross_stage_next_attempts, quality.cross_content_next_attempts,
            quality.cross_release_next_attempts, quality.next_outcome_pending_attempts,
        ),
        latency,
        threshold_metrics,
        quality,
    )
    warnings = _warnings(sample, quality, threshold_metrics, thresholds)
    cohort = _external_frame(frames["cohorts"])
    latency_table = pd.DataFrame([
        {
            "distribution": "observedNextRunDelaySeconds",
            "observedCount": latency.observed_delay_seconds.observed_count,
            "missingCount": latency.observed_delay_seconds.missing_count,
            "p50Seconds": latency.observed_delay_seconds.p50,
            "p75Seconds": latency.observed_delay_seconds.p75,
            "p90Seconds": latency.observed_delay_seconds.p90,
            "p95Seconds": latency.observed_delay_seconds.p95,
        },
        {
            "distribution": "rightCensoredObservationAgeSeconds",
            "observedCount": latency.censored_observation_age_seconds.observed_count,
            "missingCount": latency.censored_observation_age_seconds.missing_count,
            "p50Seconds": latency.censored_observation_age_seconds.p50,
            "p75Seconds": latency.censored_observation_age_seconds.p75,
            "p90Seconds": latency.censored_observation_age_seconds.p90,
            "p95Seconds": latency.censored_observation_age_seconds.p95,
        },
    ])
    tables = {
        "run_retention_summary.csv": cohort.loc[cohort.get("cohortType", pd.Series(dtype=str)) == "overall"].copy(),
        "run_retention_by_outcome.csv": cohort.loc[cohort.get("cohortType", pd.Series(dtype=str)) == "outcome"].copy(),
        "run_retention_by_stage.csv": cohort.loc[cohort.get("cohortType", pd.Series(dtype=str)) == "stage"].copy(),
        "run_retention_latency.csv": latency_table,
        "run_retention_data_quality.csv": _quality_table(quality),
    }
    definitions = RunRetentionDefinitions(
        "NewAttempt",
        "linkageEligibleCanonicalFinalAttempt",
        "anchorWithObservedNextNewAttempt",
        "conditionalOnObservedNextRun",
        "Earliest later Gameplay segmentIndex=1 attempt not marked as resume for the same telemetry player.",
        "Telemetry rows uploaded before analysisAsOfUtc; this is not BigQuery historical system time.",
        "No global source complete-through authority exists; optional grace only delays threshold maturity.",
        "Player continuity uses the persistent telemetry profile identity and can break after reinstall or profile reset.",
        notes=(
            "No-next facts describe the supplied telemetry snapshot, not application return or uninstall.",
            "Right-censored anchors are excluded from thresholdResolvedDenominator.",
            "ThresholdExceeded includes returned-after-threshold and mature no-next anchors while preserving both counts.",
        ),
    )
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION, "runRetention", ANALYSIS_VERSION, generated_at_utc,
        request.to_scope(as_of), sample, quality, definitions, warnings, estimated_bytes,
    )
    return ReportBundle(
        metadata,
        to_external(metrics),
        _render_markdown(request, sample, latency, threshold_metrics, warnings, tables),
        tables,
    )


def analyze_run_retention(
    request: RunRetentionRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: RunRetentionThresholds = RunRetentionThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> RunRetentionAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be non-negative")
    started = clock()
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    started = started.astimezone(timezone.utc).replace(microsecond=0)
    as_of = request.resolved_as_of(lambda: started)
    resolved = replace(request, analysis_as_of_utc=as_of)
    queries = build_run_retention_queries(resolved, config=config, analysis_as_of_utc=as_of)
    estimates = {
        name: dry_run_query(query, client=client, config=config).total_bytes_processed
        for name, query in queries.items()
    }
    total = sum(estimates.values())
    if total > maximum_total_bytes:
        raise RuntimeError(
            f"Run Retention dry-run estimate {total:,} bytes exceeds maximum "
            f"{maximum_total_bytes:,} bytes; no analysis query was executed"
        )
    frames = {
        name: query_dataframe(
            query, client=client, config=config, maximum_bytes_billed=maximum_total_bytes
        )
        for name, query in queries.items()
    }
    bundle = _assemble_bundle(
        resolved, as_of, frames, estimated_bytes=total, thresholds=thresholds,
        generated_at_utc=started,
    )
    return RunRetentionAnalysis(bundle, total, estimates)


def generate_run_retention_report(
    request: RunRetentionRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: RunRetentionThresholds = RunRetentionThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    analysis = analyze_run_retention(
        request, client=client, config=config, maximum_total_bytes=maximum_total_bytes,
        thresholds=thresholds, clock=clock,
    )
    return write_report_bundle(
        analysis.bundle, output_root=output_root, overwrite=overwrite, table_specs=TABLE_SPECS
    )
