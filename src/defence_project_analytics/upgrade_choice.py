"""Read-only Upgrade Choice analysis and aggregate report generation."""

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
    UpgradeAnalysisScope,
    UpgradeChoiceContextMetrics,
    UpgradeChoiceMetrics,
    UpgradeDataQuality,
    UpgradeExposureMetrics,
    UpgradeHeadToHeadMetrics,
    UpgradeOutcomeAssociationMetrics,
    UpgradeReportDefinitions,
    UpgradeSampleSummary,
    UpgradeSelectionMetrics,
    utc_now_seconds,
)
from defence_project_analytics.reporting.renderers import attached_tables_markdown, camel_case, to_external
from defence_project_analytics.reporting.warnings import UpgradeThresholds, sort_upgrade_warnings
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
POPULATION_FRAGMENT = "sql/analysis/_upgrade_population_ctes_v1.sql"
INCLUDE_MARKER = "-- @include upgrade_population_ctes_v1"
SQL_FILES = {
    "population": "sql/analysis/upgrade_population_v1.sql",
    "exposure": "sql/analysis/upgrade_candidate_exposure_v1.sql",
    "selection": "sql/analysis/upgrade_selection_v1.sql",
    "context": "sql/analysis/upgrade_choice_context_v1.sql",
    "headToHead": "sql/analysis/upgrade_head_to_head_v1.sql",
    "outcome": "sql/analysis/upgrade_outcome_association_v1.sql",
}

TABLE_SPECS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "upgrade_candidate_exposure.csv": ((
        "candidateKey", "categoryCode", "category", "contentId", "weaponFamilyId",
        "grantWeaponId", "cardType", "weaponSourceType", "allObservedExposureCount",
        "completeExposureCount", "linkedSelectionCount", "selectionCountIncludingLegacy",
        "pickRateCount", "pickRateDenominator", "pickRate", "exposureElapsedP25",
        "exposureElapsedMedian", "exposureElapsedP75",
    ), ("candidateKey",)),
    "upgrade_candidate_selection.csv": ((
        "candidateKey", "exposureCount", "selectionCount", "pickRateCount",
        "pickRateDenominator", "pickRate", "exposedAttempts", "selectedAttempts",
        "repeatedExposureAttempts", "repeatedSelectionAttempts",
    ), ("candidateKey",)),
    "upgrade_candidate_position.csv": ((
        "candidateKey", "dimension", "dimensionValue", "exposureCount", "selectionCount",
        "pickRateCount", "pickRateDenominator", "pickRate",
    ), ("candidateKey", "dimension", "dimensionValue")),
    "upgrade_head_to_head.csv": ((
        "candidateA", "candidateB", "coExposureCount", "aSelectedCount", "bSelectedCount",
        "otherSelectedCount", "unresolvedSelectionCount", "aOverallSelectionShareCount",
        "aOverallSelectionShareDenominator", "aOverallSelectionShare",
        "bOverallSelectionShareCount", "bOverallSelectionShareDenominator",
        "bOverallSelectionShare", "aConditionalPreferenceCount",
        "aConditionalPreferenceDenominator", "aConditionalPreference",
        "bConditionalPreferenceCount", "bConditionalPreferenceDenominator",
        "bConditionalPreference",
    ), ("candidateA", "candidateB")),
    "upgrade_outcome_association.csv": ((
        "candidateKey", "selectedAttempts", "selectedClears", "selectedDeaths",
        "selectedAbandons", "selectedUnrecognizedOutcomes", "selectedClearRate",
        "selectedElapsedP25", "selectedElapsedMedian", "selectedElapsedP75",
        "selectedRemainingFromExposureP25", "selectedRemainingFromExposureMedian",
        "selectedRemainingFromExposureP75", "selectedRemainingOutcomeSecondsP25",
        "selectedRemainingOutcomeSecondsMedian", "selectedRemainingOutcomeSecondsP75",
        "exposedNotSelectedAttempts", "alternativeSelectedAttempts", "noSelectionOnlyAttempts",
        "exposedNotSelectedClears", "exposedNotSelectedDeaths", "exposedNotSelectedAbandons",
        "exposedNotSelectedUnrecognizedOutcomes", "exposedNotSelectedClearRate",
        "exposedNotSelectedElapsedP25", "exposedNotSelectedElapsedMedian",
        "exposedNotSelectedElapsedP75", "exposedNotSelectedRemainingFromExposureP25",
        "exposedNotSelectedRemainingFromExposureMedian",
        "exposedNotSelectedRemainingFromExposureP75", "clearRateDifferencePp",
        "invalidOutcomeTimeRows",
    ), ("candidateKey",)),
    "upgrade_context.csv": ((
        "candidateKey", "dimension", "dimensionValue", "attributionSource", "exposureCount",
        "selectionCount", "pickRateCount", "pickRateDenominator", "pickRate",
    ), ("candidateKey", "dimension", "dimensionValue", "attributionSource")),
    "upgrade_data_quality.csv": (("metric", "count", "denominator", "ratio"), ("metric",)),
}


@dataclass(frozen=True, slots=True)
class UpgradeChoiceRequest:
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
        for start_name, end_name in (
            ("segment_ended_at_utc_start", "segment_ended_at_utc_end"),
            ("uploaded_at_utc_start", "uploaded_at_utc_end"),
        ):
            start, end = getattr(self, start_name), getattr(self, end_name)
            if start is not None and end is not None and start >= end:
                raise ValueError(f"{start_name} must be earlier than {end_name}")

    def resolved_as_of(self, clock: Callable[[], datetime] = utc_now_seconds) -> datetime:
        value = self.analysis_as_of_utc or clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_as_of_utc must be timezone-aware")
        return value.astimezone(timezone.utc).replace(microsecond=0)

    def to_scope(self, analysis_as_of_utc: datetime) -> UpgradeAnalysisScope:
        return UpgradeAnalysisScope(
            environment=self.environment, stage_key=self.stage_key,
            content_version=self.content_version, app_version=self.app_version,
            release_id=self.release_id, release_channel=self.release_channel,
            release_type=self.release_type, is_development_build=self.is_development_build,
            uploaded_at_utc_start=self.uploaded_at_utc_start,
            uploaded_at_utc_end=self.uploaded_at_utc_end,
            segment_ended_at_utc_start=self.segment_ended_at_utc_start,
            segment_ended_at_utc_end=self.segment_ended_at_utc_end,
            analysis_as_of_utc=analysis_as_of_utc,
        )


@dataclass(frozen=True, slots=True)
class UpgradeChoiceAnalysis:
    bundle: ReportBundle
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


def _typed(value: Any, bigquery_type: str) -> QueryParameterValue:
    return QueryParameterValue(value, bigquery_type)


def _segment_selection_count_matches(
    expected_by_segment: Mapping[tuple[str, str], int | None],
    observed_by_segment: Mapping[tuple[str, str], int],
) -> dict[tuple[str, str], bool]:
    """Mirror the SQL's environment + gameplay-segment count contract for fixtures."""

    return {
        key: expected is not None and expected == observed_by_segment.get(key, 0)
        for key, expected in expected_by_segment.items()
    }


def upgrade_choice_parameters(
    request: UpgradeChoiceRequest,
    analysis_as_of_utc: datetime,
    thresholds: UpgradeThresholds = UpgradeThresholds(),
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
        "max_context_snapshot_lag_seconds": _typed(
            thresholds.max_context_snapshot_lag_seconds, "FLOAT64"
        ),
    }


def build_upgrade_choice_queries(
    request: UpgradeChoiceRequest,
    *,
    config: AnalyticsConfig | None = None,
    analysis_as_of_utc: datetime | None = None,
    thresholds: UpgradeThresholds = UpgradeThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> dict[str, QuerySpec]:
    as_of = analysis_as_of_utc or request.resolved_as_of(clock)
    fragment = load_sql(POPULATION_FRAGMENT, config=config)
    parameters = upgrade_choice_parameters(request, as_of, thresholds)
    queries: dict[str, QuerySpec] = {}
    for name, path in SQL_FILES.items():
        sql = load_sql(path, config=config)
        if sql.count(INCLUDE_MARKER) != 1:
            raise ValueError(f"Upgrade SQL must contain exactly one population marker: {path}")
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


def _quality_table(quality: UpgradeDataQuality) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for metric, value in to_external(quality).items():
        if isinstance(value, dict) and set(value) == {"count", "denominator", "ratio"}:
            rows.append({"metric": metric, **value})
        else:
            rows.append({"metric": metric, "count": value, "denominator": None, "ratio": None})
    return pd.DataFrame(rows)


def _upgrade_warnings(
    sample: UpgradeSampleSummary,
    quality: UpgradeDataQuality,
    exposure: pd.DataFrame,
    head_to_head: pd.DataFrame,
    outcome: pd.DataFrame,
    thresholds: UpgradeThresholds,
) -> tuple[ReportWarning, ...]:
    warnings: list[ReportWarning] = []

    def add(code: str, message: str) -> None:
        warnings.append(ReportWarning(code, message))

    if sample.final_attempts == 0:
        add("NO_ATTEMPTS", "No final attempts matched the requested scope.")
    if sample.complete_exposures < thresholds.min_complete_exposures:
        add("LOW_UPGRADE_EXPOSURE_SAMPLE", f"Complete exposures ({sample.complete_exposures}) are below {thresholds.min_complete_exposures}.")
    if quality.incomplete_segments_excluded:
        add("INCOMPLETE_DETAIL_EXCLUDED", f"Excluded {quality.incomplete_segments_excluded} explicitly incomplete segments.")
    if quality.legacy_unassessed_segments:
        add("UNASSESSED_LEGACY_DETAIL", f"Excluded {quality.legacy_unassessed_segments} segments without upload status.")
    if quality.mixed_content_segments_excluded:
        add("MIXED_CONTENT_DETAIL_EXCLUDED", f"Excluded {quality.mixed_content_segments_excluded} mixed-scope segments.")
    if quality.truncated_exposures:
        add("TRUNCATED_UPGRADE_EXPOSURES", f"Excluded {quality.truncated_exposures} truncated exposures.")
    if quality.dropped_exposures or quality.omitted_candidates:
        add("UPGRADE_EXPOSURE_OVERFLOW", f"Telemetry reports {quality.dropped_exposures} dropped exposures and {quality.omitted_candidates} omitted candidates.")
    if quality.malformed_exposures:
        add("MALFORMED_UPGRADE_EXPOSURE", f"Excluded {quality.malformed_exposures} malformed exposures.")
    if quality.selection_count_mismatch_segments:
        add("UPGRADE_SELECTION_COUNT_MISMATCH", f"Excluded {quality.selection_count_mismatch_segments} segments whose same-run selection count did not match run_end.")
    if quality.missing_candidate_identity_rows:
        add("MISSING_CANDIDATE_IDENTITY", f"Found {quality.missing_candidate_identity_rows} candidate rows without category/content identity.")
    if quality.unrecognized_category_rows:
        add("UNRECOGNIZED_UPGRADE_CATEGORY", f"Preserved {quality.unrecognized_category_rows} candidate rows with unrecognized category codes.")
    if quality.legacy_unlinked_selections:
        add("UNLINKED_UPGRADE_SELECTION", f"Excluded {quality.legacy_unlinked_selections} selections without exposure linkage.")
    if quality.selections_without_exposure_match:
        add("SELECTION_WITHOUT_EXPOSURE_MATCH", f"Excluded {quality.selections_without_exposure_match} selections without a matching exposure candidate.")
    if quality.multiple_selection_exposures:
        add("MULTIPLE_SELECTIONS_FOR_EXPOSURE", f"Excluded {quality.multiple_selection_exposures} exposures with multiple selections.")
    if quality.approximate_context_rows or quality.stale_context_rows or quality.missing_context_rows:
        add("APPROXIMATE_CHOICE_CONTEXT", f"Context uses {quality.approximate_context_rows} approximate rows; {quality.stale_context_rows} stale and {quality.missing_context_rows} missing rows were recorded.")
    low_candidates = int((pd.to_numeric(exposure.get("completeExposureCount", pd.Series(dtype=float)), errors="coerce").fillna(0) < thresholds.min_candidate_exposures).sum()) if not exposure.empty else 0
    if low_candidates:
        add("LOW_CANDIDATE_SAMPLE", f"{low_candidates} candidates are below the complete-exposure threshold.")
    if sample.complete_exposures and (head_to_head.empty or int(pd.to_numeric(head_to_head.get("coExposureCount", pd.Series(dtype=float)), errors="coerce").fillna(0).max()) < thresholds.min_pair_co_exposures):
        add("LOW_HEAD_TO_HEAD_SAMPLE", "No candidate pair meets the configured co-exposure threshold.")
    if not outcome.empty:
        add("OUTCOME_ASSOCIATION_BIASED_SAMPLE", "Selection/outcome associations are observational and conditioned by eligibility, choice, and survival.")
    return sort_upgrade_warnings(warnings)


def _render_markdown(
    request: UpgradeChoiceRequest,
    sample: UpgradeSampleSummary,
    quality: UpgradeDataQuality,
    warnings: tuple[ReportWarning, ...],
    signals: list[str],
    outcome: pd.DataFrame,
    tables: Mapping[str, pd.DataFrame],
) -> str:
    warning_lines = "\n".join(f"- `{item.code}`: {item.message}" for item in warnings) or "- None."
    signal_lines = "\n".join(f"- {item}" for item in signals) or "- No deterministic signal crossed the configured thresholds."
    absent = int(pd.to_numeric(outcome.get("exposedNotSelectedAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not outcome.empty else 0
    alternative = int(pd.to_numeric(outcome.get("alternativeSelectedAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not outcome.empty else 0
    no_selection = int(pd.to_numeric(outcome.get("noSelectionOnlyAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not outcome.empty else 0
    return f"""# Upgrade Choice Report

## Scope

- Environment: `{request.environment}`
- Stage: `{request.stage_key}`
- Content version: `{request.content_version}`

## Sample & Data Quality

- Final attempts: {sample.final_attempts}
- Fully choice-covered attempts: {sample.fully_choice_covered_attempts}
- Complete exposures: {sample.complete_exposures}
- Choice-eligible gameplay segments: {sample.choice_eligible_segments}/{sample.detail_candidate_segments}

## Exposure

- Candidate rows are reconstructed into distinct upgrade presentations before counting exposures.

## Selection / Pick Rate

- Pick rate is linked selections divided by complete exposures containing the candidate.
- A complete no-selection presentation remains in the denominator and contributes to no candidate numerator.

## Choice Context

- Context uses exposure time. Player snapshots must precede the exposure by no more than 30 seconds.

## Head-to-Head

- Co-exposure includes multi-candidate presentations; `otherSelectedCount` remains explicit.

## Post-Selection Outcome Associations

- Exposed-not-selected attempts: {absent}; alternative selected: {alternative}; no-selection only: {no_selection}.
- The two component counts are mutually exclusive and sum to exposed-not-selected attempts for every candidate.

## Notable Statistical Signals

{signal_lines}

## Caveats

- Raw pick rate is conditional on candidate eligibility and exposure context; it is not a global preference probability.
- Head-to-head metrics are co-exposure descriptions, not controlled pairwise experiments.
- Selection/outcome associations are observational, not causal, and are affected by player choice, progression, survival time, position, and co-presented alternatives.
- The exposed-not-selected cohort combines attempts where another candidate was selected and attempts where no candidate was selected; the report exposes those components separately.
- Remaining outcome time includes every final outcome and is not death survival.
- Snapshot and transition context is an approximation of state at exposure time.
{warning_lines}

## Attached Tables

{attached_tables_markdown(f'tables/{name}' for name in tables)}
"""


def _assemble_bundle(
    request: UpgradeChoiceRequest,
    analysis_as_of_utc: datetime,
    frames: Mapping[str, pd.DataFrame],
    *,
    estimated_bytes: int,
    thresholds: UpgradeThresholds,
    generated_at_utc: datetime,
) -> ReportBundle:
    if frames["population"].empty:
        raise RuntimeError("Upgrade population query returned no aggregate row")
    population = frames["population"].iloc[0]
    exposure = _external_frame(frames["exposure"])
    selection_all = _external_frame(frames["selection"])
    context_all = _external_frame(frames["context"])
    head = _external_frame(frames["headToHead"])
    outcome = _external_frame(frames["outcome"])
    selection = selection_all[selection_all.get("rowType", pd.Series(dtype=str)).eq("candidate")].drop(columns=["rowType", "dimension", "dimensionValue"], errors="ignore")
    position = selection_all[selection_all.get("rowType", pd.Series(dtype=str)).eq("breakdown")].drop(columns=["rowType", "exposedAttempts", "selectedAttempts", "repeatedExposureAttempts", "repeatedSelectionAttempts"], errors="ignore")
    context = context_all[context_all.get("rowType", pd.Series(dtype=str)).eq("context")].drop(columns=["rowType", "approximateContextRows", "staleContextRows", "missingContextRows", "snapshotLagP50", "snapshotLagP75", "snapshotLagP90"], errors="ignore")
    context_quality_rows = context_all[context_all.get("rowType", pd.Series(dtype=str)).eq("quality")]
    context_quality = context_quality_rows.iloc[0] if not context_quality_rows.empty else pd.Series(dtype=object)

    candidate_attempts = _int(population, "detailCandidateAttempts")
    fully_covered = _int(population, "fullyChoiceCoveredAttempts")
    sample = UpgradeSampleSummary(
        final_attempts=_int(population, "finalAttempts"), unique_players=_int(population, "uniquePlayers"),
        clears=_int(population, "clears"), deaths=_int(population, "deaths"),
        abandons=_int(population, "abandons"), candidate_count=_int(population, "candidateCount"),
        detail_candidate_segments=_int(population, "detailCandidateSegments"),
        transport_eligible_segments=_int(population, "transportEligibleSegments"),
        choice_eligible_segments=_int(population, "choiceEligibleSegments"),
        fully_choice_covered_attempts=fully_covered,
        partially_covered_attempts=_int(population, "partiallyCoveredAttempts"),
        complete_exposures=_int(population, "completeExposures"),
    )
    invalid_times = int(pd.to_numeric(outcome.get("invalidOutcomeTimeRows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not outcome.empty else 0
    quality = UpgradeDataQuality(
        telemetry_complete_rate=MetricRatio.from_counts(_int(population, "transportEligibleSegments"), _int(population, "assessedSegments")),
        detail_coverage_rate=MetricRatio.from_counts(_int(population, "transportEligibleSegments"), sample.detail_candidate_segments),
        choice_coverage_rate=MetricRatio.from_counts(sample.choice_eligible_segments, sample.detail_candidate_segments),
        attempt_choice_coverage_rate=MetricRatio.from_counts(fully_covered, candidate_attempts),
        observed_exposures=_int(population, "observedExposures"), complete_exposures=sample.complete_exposures,
        truncated_exposures=_int(population, "truncatedExposures"), malformed_exposures=_int(population, "malformedExposures"),
        dropped_exposures=_int(population, "droppedExposures"), omitted_candidates=_int(population, "omittedCandidates"),
        incomplete_segments_excluded=_int(population, "incompleteSegmentsExcluded"),
        legacy_unassessed_segments=_int(population, "legacyUnassessedSegments"),
        mixed_content_segments_excluded=_int(population, "mixedContentSegmentsExcluded"),
        partially_covered_attempts=sample.partially_covered_attempts,
        linked_selections=_int(population, "linkedSelections"), legacy_unlinked_selections=_int(population, "legacyUnlinkedSelections"),
        selections_without_exposure_match=_int(population, "selectionsWithoutExposureMatch"),
        no_selection_exposures=_int(population, "noSelectionExposures"), multiple_selection_exposures=_int(population, "multipleSelectionExposures"),
        selection_count_mismatch_segments=_int(population, "selectionCountMismatchSegments"),
        missing_candidate_identity_rows=_int(population, "missingCandidateIdentityRows"),
        unrecognized_category_rows=_int(population, "unrecognizedCategoryRows"),
        approximate_context_rows=_int(context_quality, "approximateContextRows"),
        stale_context_rows=_int(context_quality, "staleContextRows"), missing_context_rows=_int(context_quality, "missingContextRows"),
        snapshot_lag_p50=_float(context_quality, "snapshotLagP50"), snapshot_lag_p75=_float(context_quality, "snapshotLagP75"),
        snapshot_lag_p90=_float(context_quality, "snapshotLagP90"), invalid_outcome_time_rows=invalid_times,
    )
    warnings = _upgrade_warnings(sample, quality, exposure, head, outcome, thresholds)

    exposure_ranked = exposure.sort_values(["completeExposureCount", "pickRateDenominator", "candidateKey"], ascending=[False, False, True], kind="mergesort") if not exposure.empty else exposure
    pick_signal = exposure[
        (pd.to_numeric(exposure.get("completeExposureCount", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_candidate_exposures)
        & ((pd.to_numeric(exposure.get("pickRate", pd.Series(dtype=float)), errors="coerce").fillna(-1) >= thresholds.high_pick_rate)
           | (pd.to_numeric(exposure.get("pickRate", pd.Series(dtype=float)), errors="coerce").fillna(2) <= thresholds.low_pick_rate))
    ] if not exposure.empty else exposure
    pair_signal = head[
        (pd.to_numeric(head.get("coExposureCount", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_pair_co_exposures)
        & (pd.to_numeric(head.get("aConditionalPreferenceDenominator", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_pair_selected)
        & ((pd.to_numeric(head.get("aConditionalPreference", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.notable_pair_preference)
           | (pd.to_numeric(head.get("bConditionalPreference", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.notable_pair_preference))
    ] if not head.empty else head
    outcome_signal = outcome[
        (pd.to_numeric(outcome.get("selectedAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_outcome_cohort_attempts)
        & (pd.to_numeric(outcome.get("exposedNotSelectedAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0) >= thresholds.min_outcome_cohort_attempts)
        & (pd.to_numeric(outcome.get("clearRateDifferencePp", pd.Series(dtype=float)), errors="coerce").abs().fillna(0) >= thresholds.notable_clear_rate_difference * 100)
    ] if not outcome.empty else outcome
    pair_ranked = pair_signal.assign(_metric=pair_signal[["aConditionalPreference", "bConditionalPreference"]].max(axis=1)).sort_values(["_metric", "coExposureCount", "candidateA", "candidateB"], ascending=[False, False, True, True], kind="mergesort").drop(columns="_metric") if not pair_signal.empty else pair_signal
    outcome_ranked = outcome_signal.assign(_metric=outcome_signal["clearRateDifferencePp"].abs()).sort_values(["_metric", "selectedAttempts", "candidateKey"], ascending=[False, False, True], kind="mergesort").drop(columns="_metric") if not outcome_signal.empty else outcome_signal

    signals: list[str] = []
    for _, row in pick_signal.sort_values(["pickRate", "pickRateDenominator", "candidateKey"], ascending=[False, False, True], kind="mergesort").head(10).iterrows():
        signals.append(f"{row['candidateKey']} was selected in {_int(row, 'pickRateCount')} of {_int(row, 'pickRateDenominator')} complete exposures ({_float(row, 'pickRate'):.1%}).")
    for _, row in pair_ranked.head(10).iterrows():
        preferred = "candidateA" if _float(row, "aConditionalPreference") >= _float(row, "bConditionalPreference") else "candidateB"
        prefix = "a" if preferred == "candidateA" else "b"
        signals.append(f"{row[preferred]} was selected in {_int(row, prefix + 'ConditionalPreferenceCount')} of {_int(row, prefix + 'ConditionalPreferenceDenominator')} exposures where either pair member was selected ({_float(row, prefix + 'ConditionalPreference'):.1%}); the pair was co-exposed {_int(row, 'coExposureCount')} times.")
    for _, row in outcome_ranked.head(10).iterrows():
        signals.append(f"{row['candidateKey']} selected-attempt clear rate differed from exposed-not-selected attempts by {_float(row, 'clearRateDifferencePp'):.1f} percentage points ({_int(row, 'selectedAttempts')} selected, {_int(row, 'exposedNotSelectedAttempts')} not selected: {_int(row, 'alternativeSelectedAttempts')} alternative-selected and {_int(row, 'noSelectionOnlyAttempts')} no-selection-only).")

    absent = int(pd.to_numeric(outcome.get("exposedNotSelectedAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not outcome.empty else 0
    alternative = int(pd.to_numeric(outcome.get("alternativeSelectedAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not outcome.empty else 0
    no_selection = int(pd.to_numeric(outcome.get("noSelectionOnlyAttempts", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not outcome.empty else 0
    if absent != alternative + no_selection:
        raise RuntimeError("Upgrade outcome composition invariant failed")

    metrics_model = UpgradeChoiceMetrics(
        sample=sample,
        exposure=UpgradeExposureMetrics(quality.observed_exposures, quality.complete_exposures, sample.candidate_count, _rows(exposure_ranked)),
        selection=UpgradeSelectionMetrics(quality.linked_selections, quality.no_selection_exposures, _rows(pick_signal)),
        choice_context=UpgradeChoiceContextMetrics(quality.approximate_context_rows, quality.stale_context_rows, quality.missing_context_rows, _rows(context.sort_values(["exposureCount", "candidateKey"], ascending=[False, True], kind="mergesort") if not context.empty else context)),
        head_to_head=UpgradeHeadToHeadMetrics(len(head), _rows(pair_ranked)),
        outcome_association=UpgradeOutcomeAssociationMetrics(len(outcome), absent, alternative, no_selection, _rows(outcome_ranked)),
        data_quality=quality,
    )
    tables = {
        "upgrade_candidate_exposure.csv": exposure,
        "upgrade_candidate_selection.csv": selection,
        "upgrade_candidate_position.csv": position,
        "upgrade_head_to_head.csv": head,
        "upgrade_outcome_association.csv": outcome,
        "upgrade_context.csv": context,
        "upgrade_data_quality.csv": _quality_table(quality),
    }
    definitions = UpgradeReportDefinitions(
        exposure_observation_unit="upgradeExposure",
        candidate_observation_unit="candidateWithinExposure",
        selection_observation_unit="linkedSelection",
        head_to_head_observation_unit="completeCoExposure",
        outcome_observation_unit="fullyChoiceCoveredFinalAttempt",
        context_observation_unit="exposureOrNearestPrecedingSnapshotOrTransition",
        pick_rate="Linked selections divided by complete exposures containing the candidate; no-selection exposures remain in the denominator.",
        clear_rate="Clear / (Clear + Dead); Abandon is excluded.",
        detail_eligibility="Every resume segment must match scope, be telemetryComplete, have no upgrade overflow, and pass same-run segment-local selection-count validation.",
        selection_count_scope="run_end upgradeSelectionCount and deduped selection rows are compared only within the same environment and gameplay-segment key.",
        exposed_not_selected_composition="alternativeSelectedAttempts and noSelectionOnlyAttempts are mutually exclusive and sum to exposedNotSelectedAttempts; mixed attempts use alternativeSelectedAttempts.",
        context_snapshot_max_lag_seconds=thresholds.max_context_snapshot_lag_seconds,
        notes=(
            "Candidate exposure is conditional on game state and eligibility.",
            "Multi-candidate co-exposure is not a controlled pairwise experiment.",
            "Selection/outcome association is observational and not causal.",
        ),
    )
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION, "upgradeChoice", ANALYSIS_VERSION, generated_at_utc,
        request.to_scope(analysis_as_of_utc), sample, quality, definitions, warnings, estimated_bytes,
    )
    return ReportBundle(metadata, to_external(metrics_model), _render_markdown(request, sample, quality, warnings, signals, outcome, tables), tables)


def analyze_upgrade_choice(
    request: UpgradeChoiceRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: UpgradeThresholds = UpgradeThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> UpgradeChoiceAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be non-negative")
    execution_started_at_utc = clock()
    if execution_started_at_utc.tzinfo is None or execution_started_at_utc.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    execution_started_at_utc = execution_started_at_utc.astimezone(timezone.utc).replace(microsecond=0)
    analysis_as_of_utc = request.resolved_as_of(lambda: execution_started_at_utc)
    resolved_request = replace(request, analysis_as_of_utc=analysis_as_of_utc)
    queries = build_upgrade_choice_queries(
        resolved_request, config=config, analysis_as_of_utc=analysis_as_of_utc,
        thresholds=thresholds,
    )
    estimates = {name: dry_run_query(query, client=client, config=config).total_bytes_processed for name, query in queries.items()}
    total = sum(estimates.values())
    if total > maximum_total_bytes:
        raise RuntimeError(
            f"Upgrade Choice dry-run estimate {total:,} bytes exceeds maximum "
            f"{maximum_total_bytes:,} bytes; no analysis query was executed"
        )
    frames = {
        name: query_dataframe(query, client=client, config=config, maximum_bytes_billed=maximum_total_bytes)
        for name, query in queries.items()
    }
    bundle = _assemble_bundle(
        resolved_request, analysis_as_of_utc, frames, estimated_bytes=total,
        thresholds=thresholds, generated_at_utc=execution_started_at_utc,
    )
    return UpgradeChoiceAnalysis(bundle, total, estimates)


def generate_upgrade_choice_report(
    request: UpgradeChoiceRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
    thresholds: UpgradeThresholds = UpgradeThresholds(),
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    analysis = analyze_upgrade_choice(
        request, client=client, config=config, maximum_total_bytes=maximum_total_bytes,
        thresholds=thresholds, clock=clock,
    )
    return write_report_bundle(
        analysis.bundle, output_root=output_root, overwrite=overwrite, table_specs=TABLE_SPECS
    )
