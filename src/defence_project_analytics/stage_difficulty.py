"""Read-only Stage Difficulty analysis and common report generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from defence_project_analytics.bigquery_client import dry_run_query, query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec
from defence_project_analytics.reporting.models import (
    REPORT_CONTRACT_VERSION,
    AnalysisScope,
    DataQuality,
    DistributionSummary,
    MetricRatio,
    ReportBundle,
    ReportDefinitions,
    ReportMetadata,
    SampleSummary,
    utc_now_seconds,
)
from defence_project_analytics.reporting.renderers import attached_tables_markdown, camel_case, to_external
from defence_project_analytics.reporting.warnings import WarningThresholds, build_warnings
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_query


ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
SQL_FILES = {
    "summary": "sql/analysis/stage_difficulty_summary_v1.sql",
    "death_timing": "sql/analysis/death_timing_v1.sql",
    "death_concentration": "sql/analysis/death_concentration_v1.sql",
    "death_causes": "sql/analysis/death_causes_v1.sql",
    "incoming_damage": "sql/analysis/incoming_damage_v1.sql",
    "threat": "sql/analysis/threat_by_outcome_v1.sql",
    "death_state": "sql/analysis/death_state_v1.sql",
}

TABLE_SPECS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "deaths_by_time_bucket.csv": (("bucketOrder", "bucket", "count", "denominator", "ratio"), ("bucketOrder",)),
    "deaths_by_phase.csv": (("value", "exactCount", "approximateCount", "count", "denominator", "ratio"), ("-count", "value")),
    "deaths_by_floor.csv": (("value", "exactCount", "approximateCount", "count", "denominator", "ratio"), ("-count", "value")),
    "deaths_by_wave.csv": (("value", "exactCount", "approximateCount", "count", "denominator", "ratio"), ("-count", "value")),
    "deaths_by_zone.csv": (("value", "exactCount", "approximateCount", "count", "denominator", "ratio"), ("-count", "value")),
    "final_deaths_by_source.csv": (("label", "count", "denominator", "ratio"), ("-count", "label")),
    "deaths_by_enemy.csv": (("label", "enemyTier", "enemyClassification", "count", "denominator", "ratio"), ("-count", "label")),
    "final_deaths_by_environment_effect.csv": (("label", "count", "denominator", "ratio"), ("-count", "label")),
    "incoming_damage_by_enemy.csv": (("label", "enemyTier", "enemyClassification", "totalAppliedDamage", "hitCount", "lethalHitEvents", "affectedRuns", "lethalRuns", "damageRatio"), ("-totalAppliedDamage", "label")),
    "damage_by_source_category.csv": (("label", "totalAppliedDamage", "hitCount", "lethalHitEvents", "affectedRuns", "lethalRuns", "damageRatio"), ("-totalAppliedDamage", "label")),
    "threat_by_outcome.csv": (("outcome", "metric", "eligibleRunCount", "observedCount", "missingCount", "mean", "p25", "median", "p75"), ("outcome", "metric")),
    "player_state_at_death.csv": (("metric", "eligibleDeaths", "selectedSnapshots", "observedCount", "missingCount", "mean", "p25", "median", "p75", "trueCount"), ("metric",)),
}


@dataclass(frozen=True, slots=True)
class StageDifficultyRequest:
    environment: str
    stage_key: str
    content_version: int
    app_version: str | None = None
    release_id: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None

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
        for name in ("uploaded_at_utc_start", "uploaded_at_utc_end"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.uploaded_at_utc_start and self.uploaded_at_utc_end and self.uploaded_at_utc_start >= self.uploaded_at_utc_end:
            raise ValueError("uploaded_at_utc_start must be earlier than uploaded_at_utc_end")

    def to_scope(self) -> AnalysisScope:
        return AnalysisScope(
            environment=self.environment,
            stage_key=self.stage_key,
            content_version=self.content_version,
            app_version=self.app_version,
            release_id=self.release_id,
            release_channel=self.release_channel,
            release_type=self.release_type,
            is_development_build=self.is_development_build,
            uploaded_at_utc_start=self.uploaded_at_utc_start,
            uploaded_at_utc_end=self.uploaded_at_utc_end,
        )


@dataclass(frozen=True, slots=True)
class StageDifficultyAnalysis:
    bundle: ReportBundle
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


def _typed(value: Any, bigquery_type: str) -> QueryParameterValue:
    return QueryParameterValue(value, bigquery_type)


def stage_difficulty_parameters(request: StageDifficultyRequest) -> dict[str, Any]:
    return {
        "environment": request.environment,
        "stage_key": request.stage_key,
        "content_version": _typed(request.content_version, "INT64"),
        "app_version": _typed(request.app_version, "STRING"),
        "release_id": _typed(request.release_id, "STRING"),
        "release_channel": _typed(request.release_channel, "STRING"),
        "release_type": _typed(request.release_type, "STRING"),
        "is_development_build": _typed(request.is_development_build, "BOOL"),
        "uploaded_start_utc": _typed(request.uploaded_at_utc_start, "TIMESTAMP"),
        "uploaded_end_utc": _typed(request.uploaded_at_utc_end, "TIMESTAMP"),
    }


def build_stage_difficulty_queries(
    request: StageDifficultyRequest, *, config: AnalyticsConfig | None = None
) -> dict[str, QuerySpec]:
    parameters = stage_difficulty_parameters(request)
    return {name: load_query(path, config=config, parameters=parameters) for name, path in SQL_FILES.items()}


def _value(row: pd.Series, name: str, default: Any = None) -> Any:
    value = row.get(name, default)
    if value is None or pd.isna(value):
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


def _subset(frame: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    if column not in frame:
        return frame.iloc[0:0].copy()
    return frame[frame[column].eq(value)].drop(columns=[column]).reset_index(drop=True)


def _top_rows(frame: pd.DataFrame, *, limit: int = 10) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    key = "count" if "count" in frame else "total_applied_damage"
    ordered = frame.sort_values([key, "label"], ascending=[False, True], kind="mergesort").head(limit)
    return _external_frame(ordered).to_dict(orient="records")


def _concentration_metrics(frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    metrics: dict[str, Any] = {}
    tables: dict[str, pd.DataFrame] = {}
    for dimension in ("phase", "floor", "wave", "zone", "boss"):
        rows = _subset(frame, "dimension", dimension)
        ordered = rows.sort_values(["count", "value"], ascending=[False, True], kind="mergesort") if not rows.empty else rows
        metrics[dimension] = {
            "top": (_external_frame(ordered.head(1)).to_dict(orient="records")[0] if not ordered.empty else None),
            "attributed": int(pd.to_numeric(rows.get("count", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
            "denominator": int(rows["denominator"].iloc[0]) if not rows.empty else 0,
        }
        if dimension != "boss":
            tables[f"deaths_by_{dimension}.csv"] = _external_frame(rows)
    return metrics, tables


def _descriptive_metrics(
    frame: pd.DataFrame,
    *,
    group_column: str | None = None,
) -> dict[str, Any]:
    """Create small JSON summaries while the CSV remains the canonical breakdown."""

    result: dict[str, Any] = {}
    groups = [(None, frame)] if group_column is None else [
        (str(value), frame[frame[group_column].eq(value)])
        for value in sorted(frame.get(group_column, pd.Series(dtype=str)).dropna().unique())
    ]
    for group, rows in groups:
        metrics: dict[str, Any] = {}
        for _, row in rows.iterrows():
            metric = str(row.get("metric"))
            metrics[metric] = {
                "eligible_count": _int(row, "eligible_run_count") if "eligible_run_count" in row else _int(row, "eligible_deaths"),
                "distribution": DistributionSummary(
                    _int(row, "observed_count"), _int(row, "missing_count"),
                    mean=_float(row, "mean"), p25=_float(row, "p25"),
                    median=_float(row, "median"), p75=_float(row, "p75"),
                ),
            }
        if group is None:
            result = metrics
        else:
            result[group] = metrics
    return result


def _render_markdown(
    request: StageDifficultyRequest,
    metrics: Mapping[str, Any],
    warnings: tuple[Any, ...],
    tables: Mapping[str, pd.DataFrame],
    *,
    death_threshold: int,
) -> str:
    outcome = metrics["outcome"]
    timing = metrics["death_timing"]
    signals: list[str] = []
    for dimension in ("phase", "floor", "wave", "zone"):
        top = metrics["death_concentration"][dimension]["top"]
        if top and top.get("denominator", 0) >= death_threshold and (top.get("ratio") or 0) >= 0.5:
            signals.append(f"{top['ratio']:.1%} of attributed deaths are in {dimension} {top['value']} ({top['count']}/{top['denominator']}).")
    for row in metrics["death_causes"]["top_enemies"]:
        if row.get("count", 0) >= 5 and (row.get("ratio") or 0) >= 0.2:
            signals.append(f"Enemy {row['label']} accounts for {row['ratio']:.1%} of final deaths ({row['count']}/{row['denominator']}).")
    for row in metrics["incoming_damage"]["top_enemies"]:
        if row.get("affectedRuns", 0) >= 5 and (row.get("damageRatio") or 0) >= 0.2:
            signals.append(f"Enemy {row['label']} accounts for {row['damageRatio']:.1%} of incoming applied damage across {row['affectedRuns']} runs.")
    warning_lines = "\n".join(f"- `{item.code}`: {item.message}" for item in warnings) or "- None."
    signal_lines = "\n".join(f"- {item}" for item in signals) or "- No deterministic signal crossed the configured reporting thresholds."
    clear = outcome["clear_rate"].ratio
    clear_display = "n/a" if clear is None else f"{clear:.1%}"
    median = timing["attempt"].median
    median_display = "n/a" if median is None else f"{median:.1f}s"
    return f"""# Stage Difficulty Report

## Scope

- Environment: `{request.environment}`
- Stage: `{request.stage_key}`
- Content version: `{request.content_version}`

## Sample & Data Quality

- Final attempts: {outcome['final_attempts']}
- Unique telemetry players: {outcome['unique_players']}
- Detail-eligible runs: {metrics['data_quality']['eligible_runs']}

## Outcome

- Clears: {outcome['clears']}; deaths: {outcome['deaths']}; abandons: {outcome['abandons']}
- Clear rate (Clear / (Clear + Dead)): {clear_display}

## Survival / Death Timing

- Median valid death attempt time: {median_display}

## Death Concentration

- Full aggregate distributions are attached by phase, floor, wave, and zone.

## Final Death Causes

- Missing final source attribution: {metrics['death_causes']['missing_attribution']}

## Incoming Damage

- Total applied damage: {metrics['incoming_damage']['total_applied_damage']:.1f}
- Lethal-hit events are descriptive events and are not treated as final death counts.

## Threat / Near-Death

- Dead and Clear run-segment summaries are kept separate in the attached table.

## Player State at Death

- Snapshot-derived state is marked as an approximation of state at death.

## Notable Statistical Signals

{signal_lines}

## Caveats

{warning_lines}

## Attached Tables

{attached_tables_markdown(f'tables/{name}' for name in tables)}
"""


def _assemble_bundle(
    request: StageDifficultyRequest,
    frames: Mapping[str, pd.DataFrame],
    *,
    estimated_bytes: int,
    thresholds: WarningThresholds,
    clock: Callable[[], datetime],
) -> ReportBundle:
    if frames["summary"].empty:
        raise RuntimeError("Stage Difficulty summary query returned no aggregate row")
    summary = frames["summary"].iloc[0]
    timing_frame = frames["death_timing"]
    timing = timing_frame.iloc[0] if not timing_frame.empty else pd.Series(dtype=object)
    cause_frame = frames["death_causes"]
    incoming_frame = frames["incoming_damage"]
    state_frame = frames["death_state"]

    final_attempts = _int(summary, "final_attempts")
    unique_players = _int(summary, "unique_players")
    clears = _int(summary, "clears")
    deaths = _int(summary, "deaths")
    eligible_runs = _int(summary, "eligible_runs")
    distinct_attempts = _int(summary, "distinct_attempts")
    partial = _int(summary, "partially_covered_attempts")
    source_rows = _subset(cause_frame, "row_type", "source")
    enemy_deaths = _subset(cause_frame, "row_type", "enemy")
    environment_effects = _subset(cause_frame, "row_type", "environmentEffect")
    damage_sources = _subset(incoming_frame, "row_type", "source")
    damage_enemies = _subset(incoming_frame, "row_type", "enemy")
    missing_attribution = int(source_rows.loc[source_rows.get("label", pd.Series(dtype=str)).eq("Missing"), "count"].sum()) if not source_rows.empty else deaths
    unrecognized_final = int(source_rows.loc[source_rows.get("label", pd.Series(dtype=str)).astype(str).str.startswith("Unrecognized:"), "count"].sum()) if not source_rows.empty else 0
    unrecognized_incoming = int(damage_sources.get("label", pd.Series(dtype=str)).astype(str).str.startswith("Unrecognized:").sum()) if not damage_sources.empty else 0
    unrecognized_damage = unrecognized_final + unrecognized_incoming
    selected_snapshots = int(state_frame["selected_snapshots"].max()) if not state_frame.empty else 0
    eligible_deaths = int(state_frame["eligible_deaths"].max()) if not state_frame.empty else 0

    quality = DataQuality(
        telemetry_complete_rate=MetricRatio.from_counts(_int(summary, "complete_detail_rows"), _int(summary, "assessed_detail_rows")),
        detail_coverage_rate=MetricRatio.from_counts(eligible_runs, _int(summary, "eligible_runs") + _int(summary, "mixed_content_detail_rows") + _int(summary, "excluded_incomplete_detail_rows") + _int(summary, "unassessed_legacy_detail_rows")),
        unresolved_release_rows=_int(summary, "unresolved_release_rows"),
        excluded_incomplete_detail_rows=_int(summary, "excluded_incomplete_detail_rows"),
        unassessed_legacy_detail_rows=_int(summary, "unassessed_legacy_detail_rows"),
        mixed_content_detail_rows=_int(summary, "mixed_content_detail_rows"),
        partially_covered_attempts=partial,
        missing_death_attribution_rows=missing_attribution,
        approximate_death_state_rows=selected_snapshots,
        missing_death_state_rows=max(eligible_deaths - selected_snapshots, 0),
        invalid_death_time_rows=_int(timing, "invalid_death_time_rows"),
    )
    sample = SampleSummary(final_attempts, unique_players, deaths, eligible_runs, distinct_attempts, partial)
    warnings = build_warnings(
        sample, quality, thresholds=thresholds,
        unrecognized_outcomes=_int(summary, "unrecognized_outcomes"),
        unrecognized_damage_sources=unrecognized_damage,
    )
    concentration, concentration_tables = _concentration_metrics(frames["death_concentration"])
    buckets = timing_frame[[name for name in ("bucket_order", "bucket", "count", "denominator", "ratio") if name in timing_frame]].copy()
    outcome = {
        "final_attempts": final_attempts, "unique_players": unique_players,
        "clears": clears, "deaths": deaths, "abandons": _int(summary, "abandons"),
        "unrecognized_outcomes": _int(summary, "unrecognized_outcomes"),
        "clear_rate": MetricRatio.from_counts(clears, clears + deaths),
    }
    metrics: dict[str, Any] = {
        "outcome": outcome,
        "survival": DistributionSummary(_int(summary, "valid_survival_rows"), _int(summary, "invalid_survival_rows"), p25=_float(summary, "survival_p25"), median=_float(summary, "survival_p50"), p75=_float(summary, "survival_p75"), p90=_float(summary, "survival_p90")),
        "death_timing": {
            "death_count": _int(timing, "death_count"), "invalid_time": _int(timing, "invalid_death_time_rows"),
            "attempt": DistributionSummary(_int(timing, "death_count") - _int(timing, "invalid_death_time_rows"), _int(timing, "invalid_death_time_rows"), p10=_float(timing, "attempt_p10"), p25=_float(timing, "attempt_p25"), median=_float(timing, "attempt_p50"), p75=_float(timing, "attempt_p75"), p90=_float(timing, "attempt_p90")),
            "final_segment": DistributionSummary(_int(timing, "valid_final_segment_times"), max(_int(timing, "death_count") - _int(timing, "valid_final_segment_times"), 0), p10=_float(timing, "final_segment_p10"), p25=_float(timing, "final_segment_p25"), median=_float(timing, "final_segment_p50"), p75=_float(timing, "final_segment_p75"), p90=_float(timing, "final_segment_p90")),
            "buckets": [{"bucket": row.get("bucket"), "metric": MetricRatio.from_counts(int(row.get("count", 0)), int(row.get("denominator", 0)))} for _, row in buckets.iterrows()],
        },
        "death_concentration": concentration,
        "death_causes": {
            "source_distribution": _top_rows(source_rows, limit=10), "missing_attribution": missing_attribution,
            "top_enemies": _top_rows(enemy_deaths), "environment_effects": _top_rows(environment_effects),
        },
        "incoming_damage": {
            "total_applied_damage": float(pd.to_numeric(damage_sources.get("total_applied_damage", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
            "hits": int(pd.to_numeric(damage_sources.get("hit_count", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
            "lethal_hit_events": int(pd.to_numeric(damage_sources.get("lethal_hit_events", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
            "top_enemies": _top_rows(damage_enemies),
        },
        "threat": {"by_outcome": _descriptive_metrics(frames["threat"], group_column="outcome")},
        "player_state_at_death": {
            "eligible_deaths": eligible_deaths, "selected_snapshots": selected_snapshots,
            "death_state_approximation": selected_snapshots > 0,
            "distributions": _descriptive_metrics(state_frame),
        },
        "data_quality": {"eligible_runs": eligible_runs, **to_external(quality)},
    }
    tables = {
        "deaths_by_time_bucket.csv": _external_frame(buckets),
        **concentration_tables,
        "final_deaths_by_source.csv": _external_frame(source_rows),
        "deaths_by_enemy.csv": _external_frame(enemy_deaths),
        "final_deaths_by_environment_effect.csv": _external_frame(environment_effects),
        "incoming_damage_by_enemy.csv": _external_frame(damage_enemies),
        "damage_by_source_category.csv": _external_frame(damage_sources),
        "threat_by_outcome.csv": _external_frame(frames["threat"]),
        "player_state_at_death.csv": _external_frame(state_frame),
    }
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION, "stageDifficulty", ANALYSIS_VERSION, clock(), request.to_scope(), sample, quality,
        ReportDefinitions(
            population="One row per final attempt from telemetry_attempt_outcomes_v1.",
            clear_rate="Clear / (Clear + Dead); Abandon is excluded.",
            detail_eligibility="All matching attempt segments with telemetryComplete=TRUE; incomplete, unassessed, and mixed-scope rows are excluded.",
            notes=("Threat uses run segments as observations.", "Snapshot state is an approximation, not the lethal event itself."),
        ), warnings, estimated_bytes,
    )
    markdown = _render_markdown(request, metrics, warnings, tables, death_threshold=thresholds.min_deaths)
    return ReportBundle(metadata, metrics, markdown, tables)


def analyze_stage_difficulty(
    request: StageDifficultyRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: WarningThresholds = WarningThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> StageDifficultyAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be non-negative")
    queries = build_stage_difficulty_queries(request, config=config)
    estimates = {name: dry_run_query(query, client=client, config=config).total_bytes_processed for name, query in queries.items()}
    total = sum(estimates.values())
    if total > maximum_total_bytes:
        raise RuntimeError(f"Stage Difficulty dry-run estimate {total:,} bytes exceeds maximum {maximum_total_bytes:,} bytes; no analysis query was executed")
    frames = {name: query_dataframe(query, client=client, config=config, maximum_bytes_billed=maximum_total_bytes) for name, query in queries.items()}
    return StageDifficultyAnalysis(_assemble_bundle(request, frames, estimated_bytes=total, thresholds=thresholds, clock=clock), total, estimates)


def generate_stage_difficulty_report(
    request: StageDifficultyRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: WarningThresholds = WarningThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    analysis = analyze_stage_difficulty(request, client=client, config=config, maximum_total_bytes=maximum_total_bytes, thresholds=thresholds, clock=clock)
    path = write_report_bundle(analysis.bundle, output_root=output_root, overwrite=overwrite, table_specs=TABLE_SPECS)
    return path
