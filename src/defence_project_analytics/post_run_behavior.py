"""Read-only Post-Run Behavior analysis and aggregate report generation."""

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
    ActionSequenceMetrics,
    CommerceMetrics,
    DistributionSummary,
    MetricRatio,
    NavigationMetrics,
    PostRunAnalysisScope,
    PostRunBehaviorMetrics,
    PostRunDataQuality,
    PostRunNextRunMetrics,
    PostRunProgressionMetrics,
    PostRunReportDefinitions,
    PostRunSampleSummary,
    PostRunWindowMetrics,
    ReportBundle,
    ReportMetadata,
    ReportWarning,
    ShopFunnelMetrics,
    utc_now_seconds,
)
from defence_project_analytics.reporting.renderers import attached_tables_markdown, camel_case, to_external
from defence_project_analytics.reporting.warnings import PostRunThresholds, sort_post_run_warnings
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_VERSION = "1.1.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
POPULATION_FRAGMENT = "sql/analysis/_post_run_population_ctes_v1.sql"
NEXT_ATTEMPT_FRAGMENT = "sql/analysis/_next_new_attempt_ctes_v1.sql"
INCLUDE_MARKER = "-- @include post_run_population_ctes_v1"
NEXT_ATTEMPT_INCLUDE_MARKER = "-- @include next_new_attempt_ctes_v1"
SQL_FILES = {
    "population": "sql/analysis/post_run_population_v1.sql",
    "navigation": "sql/analysis/post_run_navigation_v1.sql",
    "shopCommerce": "sql/analysis/post_run_shop_commerce_v1.sql",
    "progression": "sql/analysis/post_run_progression_v1.sql",
    "nextRun": "sql/analysis/post_run_next_run_v1.sql",
    "actionSequence": "sql/analysis/post_run_action_sequence_v1.sql",
}

TABLE_SPECS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "post_run_first_action.csv": ((
        "rowType", "anchorOutcome", "fromAction", "toAction", "actionCount",
        "denominator", "ratio",
    ), ("rowType", "anchorOutcome", "-actionCount", "fromAction")),
    "post_run_navigation.csv": ((
        "anchorOutcome", "dimension", "viewedWindows", "viewedDenominator", "viewedRate",
        "userNavigatedWindows", "userNavigatedDenominator", "userNavigatedRate",
        "initialViewedWindows", "programmaticViewedWindows", "eventCount",
    ), ("anchorOutcome", "dimension")),
    "post_run_shop_funnel.csv": ((
        "rowType", "anchorOutcome", "dimension", "matureWindows", "shopPresentedWindows",
        "shopUserNavigatedWindows", "offerExposedWindows", "offerSelectedWindows",
        "commerceAttemptObservedWindows", "observedAttemptLinkedSucceededWindows",
        "committedSuccessWindows", "shopPresentedRate", "shopUserNavigatedRate",
        "shopPresentationToExposure", "exposureToSelection", "selectionToObservedAttempt",
    ), ("anchorOutcome",)),
    "post_run_offer_funnel.csv": ((
        "rowType", "anchorOutcome", "dimension", "exposedWindows", "selectedWindows",
        "attemptedWindows", "succeededWindows",
    ), ("-exposedWindows", "anchorOutcome", "dimension")),
    "post_run_commerce.csv": ((
        "rowType", "anchorOutcome", "dimension", "matureWindows", "observedAttemptCount",
        "linkedSucceededAttemptCount", "observedAttemptSuccessRate", "blockedAttemptCount",
        "cancelledAttemptCount", "failedAttemptCount", "duplicateAttemptCount",
        "unresolvedAttemptCount", "committedSuccessResultCount", "committedSuccessWindows",
        "committedSuccessWindowRate", "committedSuccessWithoutObservedAttemptResults",
        "committedSuccessWithoutObservedAttemptWindows", "eventWindows", "eventCount",
    ), ("rowType", "anchorOutcome", "dimension")),
    "post_run_progression.csv": ((
        "anchorOutcome", "progressionKind", "windowCount", "progressionWindows",
        "progressionEventCount", "progressionRate", "timeToProgressionP25",
        "timeToProgressionMedian", "timeToProgressionP75",
    ), ("anchorOutcome", "progressionKind")),
    "post_run_next_run.csv": ((
        "anchorOutcome", "matureWindows", "rightCensoredWindows", "nextRunWithinWindow",
        "noNextRunWithinWindow", "laterNextRunOutsideWindow", "nextRunRateDenominator",
        "nextRunRate", "sameStageRetryCount", "sameStageRetryDenominator",
        "sameStageRetryRate", "sameContentNextRunCount", "sameContentNextRunDenominator",
        "sameContentNextRunRate", "nextClears", "nextDeaths", "nextAbandons",
        "nextOutcomePending", "timeToNextRunP25", "timeToNextRunMedian",
        "timeToNextRunP75", "timeToNextRunP90",
    ), ("anchorOutcome",)),
    "post_run_action_transitions.csv": ((
        "rowType", "anchorOutcome", "fromAction", "toAction", "actionCount",
        "denominator", "ratio",
    ), ("anchorOutcome", "-actionCount", "fromAction", "toAction")),
    "post_run_data_quality.csv": (("metric", "count", "denominator", "ratio"), ("metric",)),
}


@dataclass(frozen=True, slots=True)
class PostRunBehaviorRequest:
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
    post_run_max_gap_minutes: int = 30

    def __post_init__(self) -> None:
        if self.environment not in {"Production", "Test"}:
            raise ValueError("environment must be Production or Test")
        if isinstance(self.content_version, bool) or not isinstance(self.content_version, int):
            raise ValueError("content_version must be an integer")
        if self.final_outcome not in {None, "Clear", "Dead", "Abandon"}:
            raise ValueError("final_outcome must be Clear, Dead, Abandon, or omitted")
        for name in (
            "stage_key", "app_version", "release_id", "release_channel", "release_type",
        ):
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
        if self.post_run_max_gap_minutes <= 0:
            raise ValueError("post_run_max_gap_minutes must be positive")

    def resolved_as_of(self, clock: Callable[[], datetime] = utc_now_seconds) -> datetime:
        value = self.analysis_as_of_utc or clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_as_of_utc must be timezone-aware")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def to_scope(self, analysis_as_of_utc: datetime) -> PostRunAnalysisScope:
        return PostRunAnalysisScope(
            environment=self.environment, content_version=self.content_version,
            stage_key=self.stage_key, final_outcome=self.final_outcome,
            app_version=self.app_version, release_id=self.release_id,
            release_channel=self.release_channel, release_type=self.release_type,
            is_development_build=self.is_development_build,
            run_ended_at_utc_start=self.run_ended_at_utc_start,
            run_ended_at_utc_end=self.run_ended_at_utc_end,
            uploaded_at_utc_start=self.uploaded_at_utc_start,
            uploaded_at_utc_end=self.uploaded_at_utc_end,
            analysis_as_of_utc=analysis_as_of_utc,
            post_run_max_gap_minutes=self.post_run_max_gap_minutes,
        )


@dataclass(frozen=True, slots=True)
class PostRunBehaviorAnalysis:
    bundle: ReportBundle
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


def _typed(value: Any, bigquery_type: str) -> QueryParameterValue:
    return QueryParameterValue(value, bigquery_type)


def post_run_parameters(request: PostRunBehaviorRequest, as_of: datetime) -> dict[str, Any]:
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
        "post_run_max_gap_minutes": _typed(request.post_run_max_gap_minutes, "INT64"),
    }


def build_post_run_behavior_queries(
    request: PostRunBehaviorRequest,
    *,
    config: AnalyticsConfig | None = None,
    analysis_as_of_utc: datetime | None = None,
    clock: Callable[[], datetime] = utc_now_seconds,
) -> dict[str, QuerySpec]:
    as_of = analysis_as_of_utc or request.resolved_as_of(clock)
    fragment = load_sql(POPULATION_FRAGMENT, config=config)
    next_attempt_fragment = load_sql(NEXT_ATTEMPT_FRAGMENT, config=config)
    if fragment.count(NEXT_ATTEMPT_INCLUDE_MARKER) != 1:
        raise ValueError("Post-run population SQL must contain exactly one next-attempt marker")
    fragment = fragment.replace(NEXT_ATTEMPT_INCLUDE_MARKER, next_attempt_fragment)
    parameters = post_run_parameters(request, as_of)
    queries: dict[str, QuerySpec] = {}
    for name, path in SQL_FILES.items():
        sql = load_sql(path, config=config)
        if sql.count(INCLUDE_MARKER) != 1:
            raise ValueError(f"Post-run SQL must contain exactly one population marker: {path}")
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
    return value.item() if hasattr(value, "item") else value


def _int(row: pd.Series, name: str) -> int:
    return int(_value(row, name, 0))


def _float(row: pd.Series, name: str) -> float | None:
    value = _value(row, name)
    return None if value is None else float(value)


def _external_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns={name: camel_case(str(name)) for name in frame.columns})


def _rows(frame: pd.DataFrame, limit: int = 10) -> tuple[Mapping[str, Any], ...]:
    return tuple(frame.head(limit).to_dict(orient="records"))


def _quality_table(quality: PostRunDataQuality) -> pd.DataFrame:
    return pd.DataFrame([
        {"metric": camel_case(item.name), "count": getattr(quality, item.name),
         "denominator": None, "ratio": None}
        for item in fields(quality)
    ])


def _build_warnings(
    sample: PostRunSampleSummary,
    quality: PostRunDataQuality,
    thresholds: PostRunThresholds,
) -> tuple[ReportWarning, ...]:
    result: list[ReportWarning] = []

    def add(code: str, message: str) -> None:
        result.append(ReportWarning(code, message))

    if sample.anchor_final_runs == 0:
        add("NO_POST_RUN_WINDOWS", "No final-attempt anchors matched the requested scope.")
    elif sample.anchor_final_runs < thresholds.anchor_final_runs or sample.mature_windows < thresholds.mature_windows:
        add("LOW_POST_RUN_SAMPLE", f"Anchor/mature windows are {sample.anchor_final_runs}/{sample.mature_windows}.")
    if quality.missing_player_identity_anchors:
        add("MISSING_POST_RUN_PLAYER_IDENTITY", f"{quality.missing_player_identity_anchors} anchors cannot be linked by player identity.")
    if sample.right_censored_windows:
        add("RIGHT_CENSORED_POST_RUN_WINDOW", f"Excluded {sample.right_censored_windows} recent windows from mature denominators.")
    if quality.windows_without_lobby_activity_observed:
        add("BEST_EFFORT_ACTIVITY_ABSENCE", f"No lobby activity was observed in {quality.windows_without_lobby_activity_observed} mature windows; absence is best-effort evidence only.")
    if quality.cross_content_actions:
        add("CROSS_CONTENT_POST_RUN_ACTION", f"Observed {quality.cross_content_actions} cross-content actions; product metrics exclude them.")
    if quality.cross_release_actions:
        add("CROSS_RELEASE_POST_RUN_ACTION", f"Observed {quality.cross_release_actions} cross-release actions.")
    if quality.unrecognized_action_rows:
        add("UNRECOGNIZED_POST_RUN_ACTION", f"Preserved {quality.unrecognized_action_rows} unrecognized action rows.")
    if quality.same_timestamp_action_groups:
        add("AMBIGUOUS_POST_RUN_ACTION_ORDER", f"Applied deterministic ordering to {quality.same_timestamp_action_groups} same-timestamp action groups.")
    if quality.offer_selection_without_exposure:
        add("OFFER_SELECTION_WITHOUT_EXPOSURE", f"{quality.offer_selection_without_exposure} selections lack an observed exposure header.")
    if quality.commerce_attempt_without_shop_selection:
        add("COMMERCE_ATTEMPT_WITHOUT_SHOP_SELECTION", f"{quality.commerce_attempt_without_shop_selection} commerce Attempts lack a matching observed selection.")
    if quality.transaction_attempt_without_result:
        add("TRANSACTION_ATTEMPT_WITHOUT_RESULT", f"{quality.transaction_attempt_without_result} observed Attempts have no Result by the snapshot.")
    if quality.transaction_result_without_observed_attempt:
        add("TRANSACTION_RESULT_WITHOUT_OBSERVED_ATTEMPT", f"{quality.transaction_result_without_observed_attempt} Results have no observed best-effort Attempt; this is an Attempt-availability warning and does not change the durable Result fact.")
    if quality.non_commerce_system_rewards_excluded:
        add("NON_COMMERCE_SYSTEM_REWARD_EXCLUDED", f"Excluded {quality.non_commerce_system_rewards_excluded} non-commerce system reward transaction rows from commerce.")
    if quality.progression_transactions_excluded:
        add("PROGRESSION_TRANSACTION_EXCLUDED_FROM_COMMERCE", f"Excluded {quality.progression_transactions_excluded} progression transaction rows from commerce.")
    if sample.commerce_attempt_windows < thresholds.observed_commerce_attempts:
        add("LOW_COMMERCE_SAMPLE", f"Observed commerce Attempt windows ({sample.commerce_attempt_windows}) are below {thresholds.observed_commerce_attempts}.")
    if sample.progression_windows < thresholds.progression_windows:
        add("LOW_PROGRESSION_SAMPLE", f"Progression windows ({sample.progression_windows}) are below {thresholds.progression_windows}.")
    if sample.next_run_within_window_windows < thresholds.next_run_linked_windows:
        add("LOW_NEXT_RUN_SAMPLE", f"Next-run-linked windows ({sample.next_run_within_window_windows}) are below {thresholds.next_run_linked_windows}.")
    if quality.next_outcome_pending_windows:
        add("NEXT_RUN_OUTCOME_PENDING", f"{quality.next_outcome_pending_windows} next attempts have no final outcome by the snapshot.")
    return sort_post_run_warnings(result)


def _render_markdown(
    request: PostRunBehaviorRequest,
    sample: PostRunSampleSummary,
    commerce: CommerceMetrics,
    warnings: tuple[ReportWarning, ...],
    signals: list[str],
    tables: Mapping[str, pd.DataFrame],
) -> str:
    warning_lines = "\n".join(f"- `{item.code}`: {item.message}" for item in warnings) or "- None."
    signal_lines = "\n".join(f"- {item}" for item in signals) or "- No deterministic signal crossed the configured thresholds."
    return f"""# Post-Run Behavior Report

## Scope

- Environment: `{request.environment}`
- Content version: `{request.content_version}`
- Stage: `{request.stage_key or 'all-stages'}`
- Immediate post-run window: {request.post_run_max_gap_minutes} minutes

## Sample & Data Quality

- Final-attempt anchors: {sample.anchor_final_runs}
- Mature/right-censored windows: {sample.mature_windows}/{sample.right_censored_windows}

{warning_lines}

## Anchor Outcomes

- Clear/Dead/Abandon: {sample.clears}/{sample.deaths}/{sample.abandons}

## First Post-Run Actions

- First observed actions include automatic presentation. First user actions do not.

## Lobby Navigation

- Shop presentation and user-initiated Shop-tab navigation are separate metrics.
- Shop section presentation alone does not establish user navigation or a direct section click.

## Shop Funnel

- OfferSelected, an observed transaction Attempt, and Succeeded are distinct stages.

## Commerce

- Observed Attempt success: {commerce.linked_succeeded_attempts}/{commerce.observed_commerce_attempts}.
- Durable committed-success windows: {commerce.durable_committed_success_windows}/{sample.mature_windows}.
- A durable Succeeded Result without an observed best-effort Attempt remains committed presence; Attempt availability does not change the durable Result fact.
- Non-commerce system rewards and progression spends are excluded from commerce.

## Progression

- Progression event reach and progression-spend transaction activity use separate domain counts.

## Next Run

- A next run is a new gameplay attempt, not a resume segment, lifecycle terminal, or Battle-tab view.
- No next run within the window is not equivalent to churn and is based on telemetry uploaded by the analysis as-of.

## Action Transitions

- Consecutive high-level user-action pairs are descriptive frequencies, not a Markov model.

## Notable Statistical Signals

{signal_lines}

## Caveats

- Post-run behavior is observational and not causal.
- Initial and programmatic navigation are not user-selected navigation.
- Lobby, shop, IAP, and transaction Attempt telemetry is best-effort; absence does not prove behavior absence.
- Same-timestamp cross-domain ordering is deterministic analytical ordering, not recovered click chronology.

## Attached Tables

{attached_tables_markdown(tables)}
"""


def _assemble_bundle(
    request: PostRunBehaviorRequest,
    analysis_as_of_utc: datetime,
    frames: Mapping[str, pd.DataFrame],
    *,
    estimated_bytes: int,
    thresholds: PostRunThresholds,
    generated_at_utc: datetime,
) -> ReportBundle:
    population = frames["population"]
    row = population.iloc[0] if not population.empty else pd.Series(dtype="object")
    sample = PostRunSampleSummary(
        _int(row, "anchorFinalRuns"), _int(row, "uniquePlayers"), _int(row, "clears"),
        _int(row, "deaths"), _int(row, "abandons"), _int(row, "unrecognizedOutcomes"),
        _int(row, "linkageEligibleWindows"), _int(row, "matureWindows"),
        _int(row, "rightCensoredWindows"), _int(row, "shopPresentedWindows"),
        _int(row, "shopUserNavigatedWindows"), _int(row, "commerceAttemptWindows"),
        _int(row, "committedSuccessWindows"), _int(row, "progressionWindows"),
        _int(row, "nextRunWithinWindowWindows"),
    )
    quality = PostRunDataQuality(
        _int(row, "missingPlayerIdentityAnchors"), _int(row, "missingAnchorEndRows"),
        _int(row, "resolvedWindows"), _int(row, "matureNoNextWindows"),
        _int(row, "laterNextRunOutsideWindowWindows"), _int(row, "windowsWithObservedAction"),
        _int(row, "windowsWithUserAction"), _int(row, "windowsWithoutObservedAction"),
        _int(row, "windowsWithoutLobbyActivityObserved"),
        _int(row, "physicalLobbyRows"), _int(row, "dedupedLobbyRows"),
        _int(row, "physicalShopRows"), _int(row, "dedupedShopRows"),
        _int(row, "physicalTransactionRows"), _int(row, "dedupedTransactionRows"),
        _int(row, "physicalProgressionRows"), _int(row, "dedupedProgressionRows"),
        _int(row, "physicalIapRows"), _int(row, "dedupedIapRows"),
        _int(row, "crossContentActions"), _int(row, "crossReleaseActions"),
        _int(row, "offerSelectionWithoutExposure"), _int(row, "commerceAttemptWithoutShopSelection"),
        _int(row, "transactionAttemptWithoutResult"), _int(row, "transactionResultWithoutObservedAttempt"),
        _int(row, "committedSuccessWithoutObservedAttemptResults"),
        _int(row, "committedSuccessWithoutObservedAttemptWindows"),
        _int(row, "nonCommerceSystemRewardsExcluded"), _int(row, "progressionTransactionsExcluded"),
        _int(row, "sameTimestampActionGroups"), _int(row, "unrecognizedActionRows"),
        _int(row, "nextOutcomePendingWindows"),
    )
    navigation_frame = _external_frame(frames["navigation"])
    shop_frame = _external_frame(frames["shopCommerce"])
    progression_frame = _external_frame(frames["progression"])
    next_frame = _external_frame(frames["nextRun"])
    sequence_frame = _external_frame(frames["actionSequence"])
    if not sequence_frame.empty:
        from_action = sequence_frame.get("fromAction", pd.Series(dtype=str)).fillna("").astype(str)
        to_action = sequence_frame.get("toAction", pd.Series(dtype=str)).fillna("").astype(str)
        sequence_frame = sequence_frame.loc[
            ~from_action.str.startswith("Feedback")
            & ~to_action.str.startswith("Feedback")
        ].copy()
    commerce_rows = shop_frame.loc[shop_frame.get("rowType", pd.Series(dtype=str)) == "commerce"] if not shop_frame.empty else shop_frame
    observed_attempts = int(commerce_rows.get("observedAttemptCount", pd.Series(dtype=float)).fillna(0).sum())
    linked_succeeded = int(commerce_rows.get("linkedSucceededAttemptCount", pd.Series(dtype=float)).fillna(0).sum())
    metrics = PostRunBehaviorMetrics(
        sample=sample,
        window=PostRunWindowMetrics(
            sample.linkage_eligible_windows, sample.mature_windows, quality.resolved_windows,
            quality.mature_no_next_windows, sample.right_censored_windows,
            quality.later_next_run_outside_window_windows,
        ),
        navigation=NavigationMetrics(
            sample.shop_presented_windows,
            MetricRatio.from_counts(sample.shop_presented_windows, sample.mature_windows),
            sample.shop_user_navigated_windows,
            MetricRatio.from_counts(sample.shop_user_navigated_windows, sample.mature_windows),
            _rows(navigation_frame.sort_values(["viewedWindows", "dimension"], ascending=[False, True]) if not navigation_frame.empty else navigation_frame),
        ),
        shop=ShopFunnelMetrics(
            int(shop_frame.get("offerExposedWindows", pd.Series(dtype=float)).fillna(0).max() or 0) if not shop_frame.empty else 0,
            int(shop_frame.get("offerSelectedWindows", pd.Series(dtype=float)).fillna(0).max() or 0) if not shop_frame.empty else 0,
            sample.commerce_attempt_windows,
            int(shop_frame.get("observedAttemptLinkedSucceededWindows", pd.Series(dtype=float)).fillna(0).sum()) if not shop_frame.empty else 0,
            MetricRatio.from_counts(int(shop_frame.get("offerExposedWindows", pd.Series(dtype=float)).fillna(0).max() or 0) if not shop_frame.empty else 0, sample.shop_presented_windows),
            MetricRatio.from_counts(int(shop_frame.get("offerSelectedWindows", pd.Series(dtype=float)).fillna(0).max() or 0) if not shop_frame.empty else 0, int(shop_frame.get("offerExposedWindows", pd.Series(dtype=float)).fillna(0).max() or 0) if not shop_frame.empty else 0),
            MetricRatio.from_counts(sample.commerce_attempt_windows, int(shop_frame.get("offerSelectedWindows", pd.Series(dtype=float)).fillna(0).max() or 0) if not shop_frame.empty else 0),
        ),
        commerce=CommerceMetrics(
            observed_attempts, linked_succeeded,
            MetricRatio.from_counts(linked_succeeded, observed_attempts),
            sample.committed_success_windows,
            MetricRatio.from_counts(sample.committed_success_windows, sample.mature_windows),
            quality.committed_success_without_observed_attempt_results,
            quality.committed_success_without_observed_attempt_windows,
            _rows(commerce_rows),
        ),
        progression=PostRunProgressionMetrics(
            sample.progression_windows,
            int(progression_frame.get("progressionEventCount", pd.Series(dtype=float)).fillna(0).sum()) if not progression_frame.empty else 0,
            _rows(progression_frame),
        ),
        next_run=PostRunNextRunMetrics(
            sample.mature_windows, sample.right_censored_windows,
            MetricRatio.from_counts(sample.next_run_within_window_windows, sample.mature_windows),
            MetricRatio.from_counts(int(next_frame.get("sameStageRetryCount", pd.Series(dtype=float)).fillna(0).sum()) if not next_frame.empty else 0, sample.next_run_within_window_windows),
            MetricRatio.from_counts(int(next_frame.get("sameContentNextRunCount", pd.Series(dtype=float)).fillna(0).sum()) if not next_frame.empty else 0, sample.next_run_within_window_windows),
            DistributionSummary(
                sample.next_run_within_window_windows,
                sample.mature_windows - sample.next_run_within_window_windows,
                p25=float(next_frame.iloc[0]["timeToNextRunP25"]) if not next_frame.empty and pd.notna(next_frame.iloc[0].get("timeToNextRunP25")) else None,
                median=float(next_frame.iloc[0]["timeToNextRunMedian"]) if not next_frame.empty and pd.notna(next_frame.iloc[0].get("timeToNextRunMedian")) else None,
                p75=float(next_frame.iloc[0]["timeToNextRunP75"]) if not next_frame.empty and pd.notna(next_frame.iloc[0].get("timeToNextRunP75")) else None,
                p90=float(next_frame.iloc[0]["timeToNextRunP90"]) if not next_frame.empty and pd.notna(next_frame.iloc[0].get("timeToNextRunP90")) else None,
            ),
        ),
        action_sequence=ActionSequenceMetrics(
            quality.windows_with_observed_action, quality.windows_with_user_action,
            _rows(sequence_frame.loc[sequence_frame.get("rowType", pd.Series(dtype=str)) == "firstObserved"]),
            _rows(sequence_frame.loc[sequence_frame.get("rowType", pd.Series(dtype=str)) == "firstUser"]),
            _rows(sequence_frame.loc[sequence_frame.get("rowType", pd.Series(dtype=str)) == "transition"]),
        ),
        data_quality=quality,
    )
    warnings = _build_warnings(sample, quality, thresholds)
    signals: list[str] = []
    if sample.mature_windows >= thresholds.signal_denominator:
        presented = sample.shop_presented_windows / sample.mature_windows
        navigated = sample.shop_user_navigated_windows / sample.mature_windows
        if presented >= thresholds.signal_ratio:
            signals.append(f"Shop presentation was observed in {presented:.1%} of mature windows ({sample.shop_presented_windows}/{sample.mature_windows}); this does not imply user navigation.")
        if navigated >= thresholds.signal_ratio:
            signals.append(f"User-initiated Shop-tab navigation was observed in {navigated:.1%} of mature windows ({sample.shop_user_navigated_windows}/{sample.mature_windows}).")
    definitions = PostRunReportDefinitions(
        "linkageEligibleFinalAttemptWindow",
        "maturePostRunWindowWithObservedShopPresentation",
        "maturePostRunWindowWithUserShopTabNavigation",
        "observedCommerceAttemptOperation",
        "maturePostRunWindow",
        "consecutiveHighLevelUserActionPairWithinPostRunWindow",
        "Observed in telemetry uploaded by analysisAsOfUtc.",
        "Linked Succeeded results divided by observed best-effort commerce Attempt operations.",
        "Mature windows with at least one durable commerce Succeeded Result divided by relevant mature windows.",
        notes=(
            "Shop section presentation alone does not establish user-initiated Shop navigation.",
            "A durable success without an observed Attempt remains committed presence; Attempt availability does not change the durable Result fact.",
            "Best-effort event absence does not prove behavior absence.",
        ),
    )
    tables: dict[str, pd.DataFrame] = {
        "post_run_first_action.csv": sequence_frame.loc[sequence_frame.get("rowType", pd.Series(dtype=str)).isin(["firstObserved", "firstUser"])].copy(),
        "post_run_navigation.csv": navigation_frame,
        "post_run_shop_funnel.csv": shop_frame.loc[shop_frame.get("rowType", pd.Series(dtype=str)) == "funnel"].copy(),
        "post_run_offer_funnel.csv": shop_frame.loc[shop_frame.get("rowType", pd.Series(dtype=str)) == "offer"].copy(),
        "post_run_commerce.csv": shop_frame.loc[shop_frame.get("rowType", pd.Series(dtype=str)).isin(["commerce", "iapLifecycle"])].copy(),
        "post_run_progression.csv": progression_frame,
        "post_run_next_run.csv": next_frame,
        "post_run_action_transitions.csv": sequence_frame.loc[sequence_frame.get("rowType", pd.Series(dtype=str)) == "transition"].copy(),
        "post_run_data_quality.csv": _quality_table(quality),
    }
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION, "postRunBehavior", ANALYSIS_VERSION,
        generated_at_utc, request.to_scope(analysis_as_of_utc), sample, quality,
        definitions, warnings, estimated_bytes,
    )
    return ReportBundle(
        metadata, to_external(metrics),
        _render_markdown(request, sample, metrics.commerce, warnings, signals, tables), tables,
    )


def analyze_post_run_behavior(
    request: PostRunBehaviorRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: PostRunThresholds = PostRunThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> PostRunBehaviorAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be non-negative")
    started = clock()
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    started = started.astimezone(timezone.utc).replace(microsecond=0)
    as_of = request.resolved_as_of(lambda: started)
    resolved = replace(request, analysis_as_of_utc=as_of)
    queries = build_post_run_behavior_queries(resolved, config=config, analysis_as_of_utc=as_of)
    estimates = {
        name: dry_run_query(query, client=client, config=config).total_bytes_processed
        for name, query in queries.items()
    }
    total = sum(estimates.values())
    if total > maximum_total_bytes:
        raise RuntimeError(
            f"Post-Run Behavior dry-run estimate {total:,} bytes exceeds maximum "
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
    return PostRunBehaviorAnalysis(bundle, total, estimates)


def generate_post_run_behavior_report(
    request: PostRunBehaviorRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: PostRunThresholds = PostRunThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    analysis = analyze_post_run_behavior(
        request, client=client, config=config, maximum_total_bytes=maximum_total_bytes,
        thresholds=thresholds, clock=clock,
    )
    return write_report_bundle(
        analysis.bundle, output_root=output_root, overwrite=overwrite, table_specs=TABLE_SPECS
    )
