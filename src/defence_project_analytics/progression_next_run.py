"""Read-only Progression Next-Run analysis and aggregate report generation."""

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
    DistributionSummary,
    MetricRatio,
    NextRunEngagementMetrics,
    PairedElapsedMetrics,
    PairedOutcomeMetrics,
    ProgressionActivityMetrics,
    ProgressionAnalysisScope,
    ProgressionCoOccurrenceMetrics,
    ProgressionDataQuality,
    ProgressionEpisodeMetrics,
    ProgressionNextRunMetrics,
    ProgressionReportDefinitions,
    ProgressionSampleSummary,
    ReportBundle,
    ReportMetadata,
    ReportWarning,
    utc_now_seconds,
)
from defence_project_analytics.reporting.renderers import attached_tables_markdown, camel_case, to_external
from defence_project_analytics.reporting.warnings import ProgressionThresholds, sort_progression_warnings
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
POPULATION_FRAGMENT = "sql/analysis/_progression_population_ctes_v1.sql"
INCLUDE_MARKER = "-- @include progression_population_ctes_v1"
SQL_FILES = {
    "population": "sql/analysis/progression_population_v1.sql",
    "activity": "sql/analysis/progression_activity_v1.sql",
    "episodes": "sql/analysis/progression_episode_v1.sql",
    "nextRun": "sql/analysis/progression_next_run_v1.sql",
    "paired": "sql/analysis/progression_paired_outcome_v1.sql",
    "coOccurrence": "sql/analysis/progression_cooccurrence_v1.sql",
    "stateTransition": "sql/analysis/progression_state_transition_v1.sql",
}

TABLE_SPECS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "progression_activity.csv": ((
        "progressionKind", "targetId", "secondaryId", "identityStatus", "eventCount",
        "boundedEpisodeCount", "unboundedEventCount", "singleProgressionEpisodeCount",
        "multiProgressionEpisodeCount", "transactionLinkedCount", "standaloneCount",
        "invalidLinkageCount", "unassessedLinkageCount", "matureEpisodeCount",
        "nextRunCount", "nextRunRate",
        "sameStagePairCount",
    ), ("-eventCount", "progressionKind", "targetId", "secondaryId")),
    "progression_state_transitions.csv": ((
        "progressionKind", "targetId", "secondaryId", "beforeState", "afterState",
        "beforeValue", "afterValue", "eventCount", "boundedEpisodeCount", "unboundedEventCount",
    ), ("-eventCount", "progressionKind", "targetId", "secondaryId", "beforeState", "afterState")),
    "progression_episode_composition.csv": ((
        "progressionKindSet", "episodeType", "boundaryType", "episodeCount", "eventCount",
        "sameTargetRepeatedEpisodeCount", "matureEpisodeCount", "nextRunCount",
        "nextRunRateDenominator", "nextRunRate", "sameStagePairCount",
        "episodeDurationP25", "episodeDurationMedian", "episodeDurationP75",
    ), ("-episodeCount", "progressionKindSet", "boundaryType")),
    "progression_cooccurrence.csv": ((
        "kindA", "kindB", "coOccurrenceEpisodeCount", "shareDenominator",
        "shareOfMultiProgressionEpisodes",
    ), ("-coOccurrenceEpisodeCount", "kindA", "kindB")),
    "progression_next_run.csv": ((
        "dimension", "progressionKind", "boundedEpisodeCount", "bothBoundaryEpisodeCount",
        "previousOnlyEpisodeCount", "nextOnlyEpisodeCount", "matureEpisodeCount",
        "rightCensoredEpisodeCount", "nextRunCount", "noNextRunWithinWindowCount",
        "laterNextRunOutsideWindowCount", "nextRunRateDenominator", "nextRunRate",
        "sameStageCount", "sameStageDenominator", "sameStageRate", "timeToNextRunP25",
        "timeToNextRunMedian", "timeToNextRunP75", "timeToNextRunP90",
        "previousToProgressionP25", "previousToProgressionMedian",
        "previousToProgressionP75", "previousToProgressionP90",
    ), ("dimension", "progressionKind")),
    "progression_outcome_transitions.csv": ((
        "pairScope", "previousOutcome", "nextOutcome", "transitionCount",
        "transitionDenominator", "transitionRatio",
    ), ("pairScope", "previousOutcome", "nextOutcome")),
    "progression_paired_elapsed.csv": ((
        "pairScope", "pairedCount", "previousElapsedP25", "previousElapsedMedian",
        "previousElapsedP75", "nextElapsedP25", "nextElapsedMedian", "nextElapsedP75",
        "deltaMean", "deltaP25", "deltaMedian", "deltaP75", "previousClears",
        "previousDeaths", "nextClears", "nextDeaths",
    ), ("pairScope",)),
    "progression_data_quality.csv": (("metric", "count", "denominator", "ratio"), ("metric",)),
}


@dataclass(frozen=True, slots=True)
class ProgressionNextRunRequest:
    environment: str
    content_version: int
    progression_kind: str | None = None
    previous_stage_key: str | None = None
    next_stage_key: str | None = None
    app_version: str | None = None
    release_id: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    progression_occurred_at_utc_start: datetime | None = None
    progression_occurred_at_utc_end: datetime | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime | None = None
    previous_run_max_gap_minutes: int = 30
    next_run_max_gap_minutes: int = 30

    def __post_init__(self) -> None:
        if self.environment not in {"Production", "Test"}:
            raise ValueError("environment must be Production or Test")
        if isinstance(self.content_version, bool) or not isinstance(self.content_version, int):
            raise ValueError("content_version must be an integer")
        for name in (
            "progression_kind", "previous_stage_key", "next_stage_key", "app_version",
            "release_id", "release_channel", "release_type",
        ):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be omitted or non-empty")
        for name in (
            "progression_occurred_at_utc_start", "progression_occurred_at_utc_end",
            "uploaded_at_utc_start", "uploaded_at_utc_end", "analysis_as_of_utc",
        ):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        for start_name, end_name in (
            ("progression_occurred_at_utc_start", "progression_occurred_at_utc_end"),
            ("uploaded_at_utc_start", "uploaded_at_utc_end"),
        ):
            start, end = getattr(self, start_name), getattr(self, end_name)
            if start is not None and end is not None and start >= end:
                raise ValueError(f"{start_name} must be earlier than {end_name}")
        if self.previous_run_max_gap_minutes <= 0 or self.next_run_max_gap_minutes <= 0:
            raise ValueError("run max-gap minutes must be positive")

    def resolved_as_of(self, clock: Callable[[], datetime] = utc_now_seconds) -> datetime:
        value = self.analysis_as_of_utc or clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_as_of_utc must be timezone-aware")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def to_scope(self, analysis_as_of_utc: datetime) -> ProgressionAnalysisScope:
        return ProgressionAnalysisScope(
            environment=self.environment, content_version=self.content_version,
            progression_kind=self.progression_kind, previous_stage_key=self.previous_stage_key,
            next_stage_key=self.next_stage_key, app_version=self.app_version,
            release_id=self.release_id, release_channel=self.release_channel,
            release_type=self.release_type, is_development_build=self.is_development_build,
            progression_occurred_at_utc_start=self.progression_occurred_at_utc_start,
            progression_occurred_at_utc_end=self.progression_occurred_at_utc_end,
            uploaded_at_utc_start=self.uploaded_at_utc_start,
            uploaded_at_utc_end=self.uploaded_at_utc_end,
            analysis_as_of_utc=analysis_as_of_utc,
            previous_run_max_gap_minutes=self.previous_run_max_gap_minutes,
            next_run_max_gap_minutes=self.next_run_max_gap_minutes,
        )


@dataclass(frozen=True, slots=True)
class ProgressionNextRunAnalysis:
    bundle: ReportBundle
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


def _typed(value: Any, bigquery_type: str) -> QueryParameterValue:
    return QueryParameterValue(value, bigquery_type)


def progression_next_run_parameters(
    request: ProgressionNextRunRequest, analysis_as_of_utc: datetime
) -> dict[str, Any]:
    return {
        "environment": request.environment,
        "content_version": _typed(request.content_version, "INT64"),
        "progression_kind": _typed(request.progression_kind, "STRING"),
        "previous_stage_key": _typed(request.previous_stage_key, "STRING"),
        "next_stage_key": _typed(request.next_stage_key, "STRING"),
        "app_version": _typed(request.app_version, "STRING"),
        "release_id": _typed(request.release_id, "STRING"),
        "release_channel": _typed(request.release_channel, "STRING"),
        "release_type": _typed(request.release_type, "STRING"),
        "is_development_build": _typed(request.is_development_build, "BOOL"),
        "progression_occurred_start_utc": _typed(
            request.progression_occurred_at_utc_start, "TIMESTAMP"
        ),
        "progression_occurred_end_utc": _typed(
            request.progression_occurred_at_utc_end, "TIMESTAMP"
        ),
        "uploaded_start_utc": _typed(request.uploaded_at_utc_start, "TIMESTAMP"),
        "uploaded_end_utc": _typed(request.uploaded_at_utc_end, "TIMESTAMP"),
        "analysis_as_of_utc": _typed(analysis_as_of_utc, "TIMESTAMP"),
        "previous_run_max_gap_minutes": _typed(request.previous_run_max_gap_minutes, "INT64"),
        "next_run_max_gap_minutes": _typed(request.next_run_max_gap_minutes, "INT64"),
    }


def build_progression_next_run_queries(
    request: ProgressionNextRunRequest,
    *,
    config: AnalyticsConfig | None = None,
    analysis_as_of_utc: datetime | None = None,
    clock: Callable[[], datetime] = utc_now_seconds,
) -> dict[str, QuerySpec]:
    as_of = analysis_as_of_utc or request.resolved_as_of(clock)
    fragment = load_sql(POPULATION_FRAGMENT, config=config)
    parameters = progression_next_run_parameters(request, as_of)
    queries: dict[str, QuerySpec] = {}
    for name, path in SQL_FILES.items():
        sql = load_sql(path, config=config)
        if sql.count(INCLUDE_MARKER) != 1:
            raise ValueError(f"Progression SQL must contain exactly one population marker: {path}")
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
    return tuple(frame.head(limit).to_dict(orient="records"))


def _quality_table(quality: ProgressionDataQuality) -> pd.DataFrame:
    return pd.DataFrame([
        {"metric": camel_case(item.name), "count": getattr(quality, item.name),
         "denominator": None, "ratio": None}
        for item in fields(quality)
    ])


def _warnings(
    sample: ProgressionSampleSummary,
    quality: ProgressionDataQuality,
    thresholds: ProgressionThresholds,
) -> tuple[ReportWarning, ...]:
    result: list[ReportWarning] = []

    def add(code: str, message: str) -> None:
        result.append(ReportWarning(code, message))

    if sample.progression_events == 0:
        add("NO_PROGRESSION_EVENTS", "No committed progression events matched the requested scope.")
    elif sample.progression_events < thresholds.progression_events:
        add("LOW_PROGRESSION_SAMPLE", f"Progression events ({sample.progression_events}) are below {thresholds.progression_events}.")
    if sample.bounded_episodes < thresholds.bounded_episodes:
        add("LOW_PROGRESSION_EPISODE_SAMPLE", f"Bounded episodes ({sample.bounded_episodes}) are below {thresholds.bounded_episodes}.")
    if quality.missing_player_identity_events:
        add("MISSING_PROGRESSION_PLAYER_IDENTITY", f"{quality.missing_player_identity_events} events lack player identity.")
    if quality.missing_target_identity_events:
        add("MISSING_PROGRESSION_TARGET_IDENTITY", f"{quality.missing_target_identity_events} events lack target identity.")
    if quality.unrecognized_progression_kind_events:
        add("UNRECOGNIZED_PROGRESSION_KIND", f"Preserved {quality.unrecognized_progression_kind_events} events with unrecognized kinds.")
    if quality.invalid_transaction_link_events:
        add("INVALID_PROGRESSION_TRANSACTION_LINK", f"{quality.invalid_transaction_link_events} linked events do not reference a Succeeded Result.")
    if quality.unexpected_standalone_events:
        add("UNEXPECTED_STANDALONE_PROGRESSION", f"{quality.unexpected_standalone_events} recognized events have unexpected standalone linkage.")
    if quality.unbounded_progression_events:
        add("UNBOUNDED_PROGRESSION_EPISODE_EXCLUDED", f"Kept {quality.unbounded_progression_events} unbounded events in activity and excluded them from episode association.")
    missing_previous = sample.bounded_episodes - sample.episodes_with_previous_run
    if missing_previous:
        add("PREVIOUS_RUN_CONTEXT_MISSING", f"{missing_previous} bounded episodes have no structural previous final attempt.")
    if quality.open_attempt_episodes_excluded_from_pairing:
        add("OPEN_ATTEMPT_PAIR_EXCLUDED", f"Excluded {quality.open_attempt_episodes_excluded_from_pairing} open-attempt episodes from paired analysis.")
    if quality.resume_continuations_skipped:
        add("RESUME_CONTINUATION_EXCLUDED", f"Skipped {quality.resume_continuations_skipped} resume gameplay segments while resolving next new attempts.")
    if quality.right_censored_episodes:
        add("RIGHT_CENSORED_PROGRESSION", f"Excluded {quality.right_censored_episodes} recent episodes from mature no-next denominators.")
    if quality.no_next_run_within_window_episodes:
        add("NEXT_RUN_WITHIN_WINDOW_MISSING", f"No qualifying next new attempt was observed within the window for {quality.no_next_run_within_window_episodes} mature episodes.")
    if quality.cross_content_performance_pairs_excluded:
        add("CROSS_CONTENT_PAIR_EXCLUDED", f"Excluded {quality.cross_content_performance_pairs_excluded} cross-content pairs from primary comparison.")
    if quality.invalid_timing_rows or quality.ambiguous_timestamp_events:
        add("INVALID_PROGRESSION_TIMING", f"Found {quality.invalid_timing_rows} invalid timing rows and {quality.ambiguous_timestamp_events} ambiguous timestamp events.")
    if sample.multi_progression_episodes:
        add("MULTI_PROGRESSION_CONFOUNDING", f"{sample.multi_progression_episodes} episodes contain multiple progression events.")
    if sample.mature_episodes < thresholds.mature_episodes:
        add("LOW_NEXT_RUN_SAMPLE", f"Mature episodes ({sample.mature_episodes}) are below {thresholds.mature_episodes}.")
    if sample.same_stage_paired_episodes < thresholds.same_stage_paired_episodes:
        add("LOW_PAIRED_RUN_SAMPLE", f"Same-stage pairs ({sample.same_stage_paired_episodes}) are below {thresholds.same_stage_paired_episodes}.")
    return sort_progression_warnings(result)


def _render_markdown(
    request: ProgressionNextRunRequest,
    sample: ProgressionSampleSummary,
    warnings: tuple[ReportWarning, ...],
    signals: list[str],
    tables: Mapping[str, pd.DataFrame],
) -> str:
    warning_lines = "\n".join(f"- `{item.code}`: {item.message}" for item in warnings) or "- None."
    signal_lines = "\n".join(f"- {item}" for item in signals) or "- No deterministic signal crossed the configured thresholds."
    return f"""# Progression Next-Run Report

## Scope

- Environment: `{request.environment}`
- Content version: `{request.content_version}`
- Previous/next immediate windows: {request.previous_run_max_gap_minutes}/{request.next_run_max_gap_minutes} minutes

## Sample & Data Quality

- Progression events: {sample.progression_events}
- Structurally bounded episodes: {sample.bounded_episodes}
- Unbounded activity-only events: {sample.unbounded_progression_events}
- Mature/right-censored episodes: {sample.mature_episodes}/{sample.right_censored_episodes}

## Progression Activity

- Activity counts committed progression events. Event count and episode count are separate observation units.

## Between-Run Episodes

- Nearest structural previous and next boundaries are resolved before immediate-window eligibility.
- Events with neither boundary remain in activity and are excluded from episode association.

## Next-Run Engagement

- A next run is a new gameplay attempt (`segmentIndex=1`), not a resume segment or lifecycle terminal.
- No-next-within-window means no qualifying new attempt was observed in telemetry uploaded by the analysis as-of time.

## Previous / Next Run Associations

- Primary pairs use same-stage, same-content final attempts and exclude open-attempt overlap.

## Progression Co-occurrence

- Multiple progression events in one episode share the same next-run context and remain explicitly confounded.

## Notable Statistical Signals

{signal_lines}

## Caveats

- Progression events are player-selected or system-conditioned and are not randomized.
- Same-stage paired descriptions are observational and do not establish causation.
- No gameplay run observed within the configured window is not equivalent to churn.
- Offline gameplay may not be observable until its telemetry is uploaded.
- Right-censored recent episodes are excluded from mature no-next denominators.
- Paired elapsed differences are descriptive associations only.
{warning_lines}

## Attached Tables

{attached_tables_markdown(f'tables/{name}' for name in tables)}
"""


def _assemble_bundle(
    request: ProgressionNextRunRequest,
    analysis_as_of_utc: datetime,
    frames: Mapping[str, pd.DataFrame],
    *,
    estimated_bytes: int,
    thresholds: ProgressionThresholds,
    generated_at_utc: datetime,
) -> ReportBundle:
    if frames["population"].empty:
        raise RuntimeError("Progression population query returned no aggregate row")
    population = frames["population"].iloc[0]
    activity = _external_frame(frames["activity"])
    episodes = _external_frame(frames["episodes"])
    next_run = _external_frame(frames["nextRun"])
    paired = _external_frame(frames["paired"])
    co_occurrence = _external_frame(frames["coOccurrence"])
    state_transition = _external_frame(frames["stateTransition"])
    transitions = paired[paired.get("rowType", pd.Series(dtype=str)).eq("outcomeTransition")].drop(
        columns=["rowType", "pairedCount", "previousElapsedP25", "previousElapsedMedian",
                 "previousElapsedP75", "nextElapsedP25", "nextElapsedMedian", "nextElapsedP75",
                 "deltaMean", "deltaP25", "deltaMedian", "deltaP75", "previousClears",
                 "previousDeaths", "nextClears", "nextDeaths"], errors="ignore"
    )
    elapsed = paired[paired.get("rowType", pd.Series(dtype=str)).eq("elapsedSummary")].drop(
        columns=["rowType", "previousOutcome", "nextOutcome", "transitionCount",
                 "transitionDenominator", "transitionRatio"], errors="ignore"
    )

    sample = ProgressionSampleSummary(
        progression_events=_int(population, "progressionEvents"),
        unique_players=_int(population, "uniquePlayers"),
        progression_kinds=_int(population, "progressionKinds"),
        unique_targets=_int(population, "uniqueTargets"),
        bounded_episodes=_int(population, "boundedEpisodes"),
        unbounded_progression_events=_int(population, "unboundedProgressionEvents"),
        single_progression_episodes=_int(population, "singleProgressionEpisodes"),
        multi_progression_episodes=_int(population, "multiProgressionEpisodes"),
        mature_episodes=_int(population, "matureEpisodes"),
        right_censored_episodes=_int(population, "rightCensoredEpisodes"),
        episodes_with_previous_run=_int(population, "episodesWithPreviousRun"),
        episodes_with_next_run=_int(population, "episodesWithNextRun"),
        same_stage_paired_episodes=_int(population, "sameStagePairedEpisodes"),
    )
    quality = ProgressionDataQuality(
        physical_progression_rows=_int(population, "physicalProgressionRows"),
        deduped_progression_events=sample.progression_events,
        conflicting_progression_events=_int(population, "conflictingProgressionEvents"),
        missing_player_identity_events=_int(population, "missingPlayerIdentityEvents"),
        missing_occurred_at_events=_int(population, "missingOccurredAtEvents"),
        missing_target_identity_events=_int(population, "missingTargetIdentityEvents"),
        unrecognized_progression_kind_events=_int(population, "unrecognizedProgressionKindEvents"),
        transaction_linked_events=_int(population, "transactionLinkedEvents"),
        standalone_events=_int(population, "standaloneEvents"),
        invalid_transaction_link_events=_int(population, "invalidTransactionLinkEvents"),
        unexpected_standalone_events=_int(population, "unexpectedStandaloneEvents"),
        unassessed_unrecognized_linkage_events=_int(
            population, "unassessedUnrecognizedLinkageEvents"
        ),
        both_boundary_episodes=_int(population, "bothBoundaryEpisodes"),
        previous_only_episodes=_int(population, "previousOnlyEpisodes"),
        next_only_episodes=_int(population, "nextOnlyEpisodes"),
        unbounded_progression_events=sample.unbounded_progression_events,
        unbounded_progression_players=_int(population, "unboundedProgressionPlayers"),
        previous_context_eligible_episodes=_int(population, "previousContextEligibleEpisodes"),
        previous_run_beyond_window_episodes=_int(population, "previousRunBeyondWindowEpisodes"),
        previous_run_missing_episodes=_int(population, "previousRunMissingEpisodes"),
        right_censored_episodes=sample.right_censored_episodes,
        next_run_within_window_episodes=_int(population, "nextRunWithinWindowEpisodes"),
        no_next_run_within_window_episodes=_int(population, "noNextRunWithinWindowEpisodes"),
        later_next_run_outside_window_episodes=_int(population, "laterNextRunOutsideWindowEpisodes"),
        next_run_outcome_pending_episodes=_int(population, "nextRunOutcomePendingEpisodes"),
        resume_continuations_skipped=_int(population, "resumeContinuationsSkipped"),
        open_attempt_episodes_excluded_from_pairing=_int(population, "openAttemptEpisodesExcludedFromPairing"),
        lifecycle_terminals_between_progression_and_next_attempt=_int(
            population, "lifecycleTerminalsBetweenProgressionAndNextAttempt"
        ),
        ambiguous_timestamp_events=_int(population, "ambiguousTimestampEvents"),
        cross_content_previous_boundaries=_int(population, "crossContentPreviousBoundaries"),
        cross_content_next_boundaries=_int(population, "crossContentNextBoundaries"),
        cross_content_performance_pairs_excluded=_int(population, "crossContentPerformancePairsExcluded"),
        cross_release_pairs=_int(population, "crossReleasePairs"),
        invalid_timing_rows=_int(population, "invalidTimingRows"),
    )
    warnings = _warnings(sample, quality, thresholds)

    overall_rows = next_run[next_run.get("dimension", pd.Series(dtype=str)).eq("overall")]
    overall = overall_rows.iloc[0] if not overall_rows.empty else pd.Series(dtype=object)
    next_ratio = MetricRatio.from_counts(_int(overall, "nextRunCount"), _int(overall, "nextRunRateDenominator"))
    same_stage = MetricRatio.from_counts(_int(overall, "sameStageCount"), _int(overall, "sameStageDenominator"))
    time_distribution = DistributionSummary(
        observed_count=_int(overall, "nextRunCount"),
        missing_count=max(0, _int(overall, "boundedEpisodeCount") - _int(overall, "nextRunCount")),
        p25=_float(overall, "timeToNextRunP25"), median=_float(overall, "timeToNextRunMedian"),
        p75=_float(overall, "timeToNextRunP75"), p90=_float(overall, "timeToNextRunP90"),
    )
    primary_outcome_rows = elapsed[
        elapsed.get("pairScope", pd.Series(dtype=str)).eq("primarySameStageSameContent")
    ]
    primary_outcome = (
        primary_outcome_rows.iloc[0]
        if not primary_outcome_rows.empty
        else pd.Series(dtype=object)
    )
    primary_elapsed_rows = elapsed[
        elapsed.get("pairScope", pd.Series(dtype=str)).eq(
            "primarySameStageSameContentSingleProgression"
        )
    ]
    primary_elapsed = primary_elapsed_rows.iloc[0] if not primary_elapsed_rows.empty else pd.Series(dtype=object)
    previous_rate = MetricRatio.from_counts(
        _int(primary_outcome, "previousClears"),
        _int(primary_outcome, "previousClears") + _int(primary_outcome, "previousDeaths"),
    )
    next_rate = MetricRatio.from_counts(
        _int(primary_outcome, "nextClears"),
        _int(primary_outcome, "nextClears") + _int(primary_outcome, "nextDeaths"),
    )
    clear_difference = None if previous_rate.ratio is None or next_rate.ratio is None else 100 * (next_rate.ratio - previous_rate.ratio)

    activity_ranked = activity.sort_values(
        ["eventCount", "boundedEpisodeCount", "progressionKind", "targetId"],
        ascending=[False, False, True, True], kind="mergesort", na_position="last"
    ) if not activity.empty else activity
    episode_ranked = episodes.sort_values(
        ["episodeCount", "progressionKindSet"], ascending=[False, True], kind="mergesort"
    ) if not episodes.empty else episodes
    transition_ranked = transitions.sort_values(
        ["transitionCount", "pairScope", "previousOutcome", "nextOutcome"],
        ascending=[False, True, True, True], kind="mergesort"
    ) if not transitions.empty else transitions
    pair_ranked = co_occurrence.sort_values(
        ["coOccurrenceEpisodeCount", "kindA", "kindB"], ascending=[False, True, True], kind="mergesort"
    ) if not co_occurrence.empty else co_occurrence

    signals: list[str] = []
    if sample.mature_episodes >= thresholds.mature_episodes and next_ratio.ratio is not None:
        signals.append(
            f"A new gameplay attempt was observed within {request.next_run_max_gap_minutes} minutes "
            f"for {next_ratio.count} of {next_ratio.denominator} mature bounded episodes ({next_ratio.ratio:.1%})."
        )
    for _, row in activity_ranked[
        pd.to_numeric(activity_ranked.get("boundedEpisodeCount", pd.Series(dtype=float)), errors="coerce").fillna(0)
        >= thresholds.kind_specific_episodes
    ].head(10).iterrows():
        signals.append(
            f"{row['progressionKind']} / {row.get('targetId') or 'missing target'} appeared in "
            f"{_int(row, 'boundedEpisodeCount')} bounded episodes ({_int(row, 'eventCount')} events)."
        )
    delta_median = _float(primary_elapsed, "deltaMedian")
    if (
        _int(primary_elapsed, "pairedCount") > 0
        and _int(primary_elapsed, "pairedCount") >= thresholds.same_stage_paired_episodes
        and delta_median is not None
    ):
        signals.append(
            f"Among {_int(primary_elapsed, 'pairedCount')} primary same-stage pairs, the median paired "
            f"attempt elapsed difference was {delta_median:.1f} seconds."
        )

    metrics = ProgressionNextRunMetrics(
        sample=sample,
        progression_activity=ProgressionActivityMetrics(
            sample.progression_events, sample.progression_kinds, sample.unique_targets,
            quality.transaction_linked_events, quality.standalone_events, _rows(activity_ranked),
        ),
        episodes=ProgressionEpisodeMetrics(
            sample.bounded_episodes, sample.single_progression_episodes,
            sample.multi_progression_episodes, _rows(episode_ranked),
        ),
        next_run_engagement=NextRunEngagementMetrics(
            sample.mature_episodes, sample.right_censored_episodes,
            next_ratio, same_stage, time_distribution,
        ),
        paired_outcome=PairedOutcomeMetrics(
            _int(primary_outcome, "pairedCount"), previous_rate, next_rate,
            clear_difference, _rows(transition_ranked),
        ),
        paired_elapsed=PairedElapsedMetrics(
            _int(primary_elapsed, "pairedCount"), _float(primary_elapsed, "deltaMean"),
            _float(primary_elapsed, "deltaP25"), _float(primary_elapsed, "deltaMedian"),
            _float(primary_elapsed, "deltaP75"),
        ),
        co_occurrence=ProgressionCoOccurrenceMetrics(len(co_occurrence), _rows(pair_ranked)),
        data_quality=quality,
    )
    tables = {
        "progression_activity.csv": activity,
        "progression_state_transitions.csv": state_transition,
        "progression_episode_composition.csv": episodes,
        "progression_cooccurrence.csv": co_occurrence,
        "progression_next_run.csv": next_run,
        "progression_outcome_transitions.csv": transitions,
        "progression_paired_elapsed.csv": elapsed,
        "progression_data_quality.csv": _quality_table(quality),
    }
    definitions = ProgressionReportDefinitions(
        progression_observation_unit="committedProgressionEvent",
        episode_observation_unit="structurallyBoundedBetweenRunProgressionEpisode",
        next_run_engagement_observation_unit="matureBoundedProgressionEpisode",
        paired_outcome_observation_unit="previousNextFinalAttemptPair",
        paired_performance_primary_unit="sameStageSameContentPreviousNextPair",
        co_occurrence_observation_unit="progressionKindPairWithinBoundedEpisode",
        episode_boundary_semantics="Nearest previous final attempt and nearest next new attempt are resolved independently of linkage windows.",
        link_window_semantics="Configured max-gap values affect immediate context and engagement eligibility, not structural episode segmentation.",
        next_run_observation_semantics="Observed in telemetry uploaded by analysisAsOfUtc.",
        no_next_run_within_window="No qualifying next new attempt was observed in telemetry uploaded by analysisAsOfUtc within the configured window.",
        clear_rate="Clear / (Clear + Dead); Abandon is excluded.",
        notes=(
            "Unbounded progression events remain in activity and are excluded from episode association.",
            "No-next-within-window is not equivalent to churn.",
            "Same-stage paired descriptions are observational and not causal.",
        ),
    )
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION, "progressionNextRun", ANALYSIS_VERSION,
        generated_at_utc, request.to_scope(analysis_as_of_utc), sample,
        quality, definitions, warnings, estimated_bytes,
    )
    return ReportBundle(
        metadata, to_external(metrics),
        _render_markdown(request, sample, warnings, signals, tables), tables,
    )


def analyze_progression_next_run(
    request: ProgressionNextRunRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: ProgressionThresholds = ProgressionThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> ProgressionNextRunAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be non-negative")
    execution_started_at_utc = clock()
    if execution_started_at_utc.tzinfo is None or execution_started_at_utc.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    execution_started_at_utc = execution_started_at_utc.astimezone(timezone.utc).replace(microsecond=0)
    analysis_as_of_utc = request.resolved_as_of(lambda: execution_started_at_utc)
    resolved_request = replace(request, analysis_as_of_utc=analysis_as_of_utc)
    queries = build_progression_next_run_queries(
        resolved_request, config=config, analysis_as_of_utc=analysis_as_of_utc
    )
    estimates = {
        name: dry_run_query(query, client=client, config=config).total_bytes_processed
        for name, query in queries.items()
    }
    total = sum(estimates.values())
    if total > maximum_total_bytes:
        raise RuntimeError(
            f"Progression Next-Run dry-run estimate {total:,} bytes exceeds maximum "
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
    return ProgressionNextRunAnalysis(bundle, total, estimates)


def generate_progression_next_run_report(
    request: ProgressionNextRunRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: ProgressionThresholds = ProgressionThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    analysis = analyze_progression_next_run(
        request, client=client, config=config, maximum_total_bytes=maximum_total_bytes,
        thresholds=thresholds, clock=clock,
    )
    return write_report_bundle(
        analysis.bundle, output_root=output_root, overwrite=overwrite, table_specs=TABLE_SPECS
    )
