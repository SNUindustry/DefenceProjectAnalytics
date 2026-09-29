"""B-8 factual observed app-return analysis; raw identities never leave SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from defence_project_analytics.bigquery_client import dry_run_query, query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec
from defence_project_analytics.reporting.models import (
    REPORT_CONTRACT_VERSION, ObservedAppReturnScope, ReportBundle, ReportMetadata,
    ReportWarning,
)
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
FRAGMENT = "sql/analysis/_observed_app_return_ctes_v1.sql"
MARKER = "-- @include observed_app_return_ctes_v1"
RESTRICTED_SQL_FILE = "sql/analysis/observed_app_return_restricted_v1.sql"
SQL_FILES = {
    "cohorts": "sql/analysis/observed_app_return_cohorts_v1.sql",
    "latency": "sql/analysis/observed_app_return_latency_v1.sql",
    "quality": "sql/analysis/observed_app_return_quality_v1.sql",
}
COHORT_COLUMNS = (
    "cohortType", "cohortValue", "anchorFinalAttempts", "eligibleAnchors",
    "ineligibleAnchors", "observedReturnCount", "observedReturnRate",
    "coldStartReturnCount", "foregroundResumeReturnCount", "rightCensoredCount",
    "rightCensoredRate", "returnedWithinThresholdCount",
    "returnedAfterThresholdCount", "noObservedReturnBeyondThresholdCount",
    "thresholdRightCensoredCount", "thresholdIneligibleCount",
)
TABLE_SPECS = {
    "overall-summary.csv": (COHORT_COLUMNS, ("cohortType", "cohortValue")),
    "outcome-summary.csv": (COHORT_COLUMNS, ("cohortValue",)),
    "stage-summary.csv": (COHORT_COLUMNS, ("cohortValue",)),
    "latency-summary.csv": (
        ("distribution", "observedCount", "p50Seconds", "p75Seconds", "p90Seconds"),
        ("distribution",),
    ),
    "data-quality-summary.csv": (("metric", "count"), ("metric",)),
}
QUALITY_FIELDS = (
    "physicalRowsRead", "logicalOccurrences", "exactDuplicateRowsRemoved",
    "conflictingOccurrenceKeys", "missingPlayerId", "invalidId",
    "invalidTimestamp", "timingQualityExcluded", "attributionExcluded",
    "developmentExcluded", "coldStartProcessReuse", "anchorsTotal",
    "anchorsEligible", "anchorsIneligible", "missingLifecycleBaseline",
    "productionDevelopmentExcluded", "runLifecycleLinkageMissing",
    "runLifecycleLinkageMismatch", "candidateTimestampTies",
    "invalidAnchorPlayerId", "conflictingAnchorIdentity", "invalidAnchorEnd",
)


@dataclass(frozen=True, slots=True)
class ObservedAppReturnAnalysis:
    bundle: ReportBundle
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ObservedAppReturnRequest:
    environment: str
    content_version: int
    analysis_as_of_utc: datetime
    stage_key: str | None = None
    final_outcome: str | None = None
    app_version: str | None = None
    release_id: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    run_ended_at_utc_start: datetime | None = None
    run_ended_at_utc_end: datetime | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    threshold_days: int | None = None
    source_upload_grace_hours: int | None = None

    def __post_init__(self) -> None:
        if self.environment not in {"Test", "Production"}:
            raise ValueError("environment must be Test or Production")
        if isinstance(self.content_version, bool) or not isinstance(self.content_version, int):
            raise ValueError("content_version must be an integer")
        if self.final_outcome not in {None, "Clear", "Dead", "Abandon"}:
            raise ValueError("invalid final_outcome")
        if self.analysis_as_of_utc is None:
            raise ValueError("analysis_as_of_utc is required")
        for name in ("stage_key", "app_version", "release_id", "release_channel", "release_type"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be nonempty when set")
        for name in (
            "analysis_as_of_utc", "run_ended_at_utc_start", "run_ended_at_utc_end",
            "uploaded_at_utc_start", "uploaded_at_utc_end",
        ):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        for start, end in (
            (self.run_ended_at_utc_start, self.run_ended_at_utc_end),
            (self.uploaded_at_utc_start, self.uploaded_at_utc_end),
        ):
            if start is not None and end is not None and start >= end:
                raise ValueError("scope start must precede end")
        if (self.threshold_days is None) != (self.source_upload_grace_hours is None):
            raise ValueError("threshold and source upload grace must be provided together")
        if self.threshold_days is not None and (
            isinstance(self.threshold_days, bool)
            or not isinstance(self.threshold_days, int)
            or self.threshold_days <= 0
        ):
            raise ValueError("threshold_days must be a positive integer")
        if self.source_upload_grace_hours is not None and (
            isinstance(self.source_upload_grace_hours, bool)
            or not isinstance(self.source_upload_grace_hours, int)
            or self.source_upload_grace_hours < 0
        ):
            raise ValueError("source_upload_grace_hours must be a nonnegative integer")

    def scope(self) -> ObservedAppReturnScope:
        return ObservedAppReturnScope(**{
            field: getattr(self, field)
            for field in ObservedAppReturnScope.__dataclass_fields__
        })


def build_queries(
    request: ObservedAppReturnRequest, *, config: AnalyticsConfig,
) -> dict[str, QuerySpec]:
    config.require_environment(request.environment)
    if config.backend_name not in {"test", "production"}:
        raise ValueError("B-8 requires an explicit approved physical backend")
    values: dict[str, Any] = {
        "environment": request.environment,
        "content_version": QueryParameterValue(request.content_version, "INT64"),
        "analysis_as_of_utc": QueryParameterValue(request.analysis_as_of_utc, "TIMESTAMP"),
        "stage_key": QueryParameterValue(request.stage_key, "STRING"),
        "final_outcome": QueryParameterValue(request.final_outcome, "STRING"),
        "app_version": QueryParameterValue(request.app_version, "STRING"),
        "release_id": QueryParameterValue(request.release_id, "STRING"),
        "release_channel": QueryParameterValue(request.release_channel, "STRING"),
        "release_type": QueryParameterValue(request.release_type, "STRING"),
        "run_ended_start_utc": QueryParameterValue(request.run_ended_at_utc_start, "TIMESTAMP"),
        "run_ended_end_utc": QueryParameterValue(request.run_ended_at_utc_end, "TIMESTAMP"),
        "uploaded_start_utc": QueryParameterValue(request.uploaded_at_utc_start, "TIMESTAMP"),
        "uploaded_end_utc": QueryParameterValue(request.uploaded_at_utc_end, "TIMESTAMP"),
        "threshold_days": QueryParameterValue(request.threshold_days, "INT64"),
        "source_upload_grace_hours": QueryParameterValue(
            request.source_upload_grace_hours, "INT64"
        ),
    }
    fragment = load_sql(FRAGMENT, config=config)
    result = {}
    for name, path in SQL_FILES.items():
        sql = load_sql(path, config=config)
        if sql.count(MARKER) != 1:
            raise ValueError(f"Missing B-8 SQL fragment marker in {path}")
        sql = sql.replace(MARKER, fragment)
        names = named_parameter_names(sql)
        if names - values.keys():
            raise ValueError(f"Unbound parameters: {names - values.keys()}")
        result[name] = QuerySpec(sql, {key: values[key] for key in names})
    return result


def build_restricted_query(
    request: ObservedAppReturnRequest, *, config: AnalyticsConfig,
) -> QuerySpec:
    """Build the additive row-level R4 adapter from the unchanged B-8 CTEs."""
    config.require_environment(request.environment)
    if config.backend_name not in {"test", "production"}:
        raise ValueError("B-8 restricted adapter requires an explicit backend")
    values: dict[str, Any] = {
        "environment": request.environment,
        "content_version": QueryParameterValue(request.content_version, "INT64"),
        "analysis_as_of_utc": QueryParameterValue(request.analysis_as_of_utc, "TIMESTAMP"),
        "stage_key": QueryParameterValue(request.stage_key, "STRING"),
        "final_outcome": QueryParameterValue(request.final_outcome, "STRING"),
        "app_version": QueryParameterValue(request.app_version, "STRING"),
        "release_id": QueryParameterValue(request.release_id, "STRING"),
        "release_channel": QueryParameterValue(request.release_channel, "STRING"),
        "release_type": QueryParameterValue(request.release_type, "STRING"),
        "run_ended_start_utc": QueryParameterValue(request.run_ended_at_utc_start, "TIMESTAMP"),
        "run_ended_end_utc": QueryParameterValue(request.run_ended_at_utc_end, "TIMESTAMP"),
        "uploaded_start_utc": QueryParameterValue(request.uploaded_at_utc_start, "TIMESTAMP"),
        "uploaded_end_utc": QueryParameterValue(request.uploaded_at_utc_end, "TIMESTAMP"),
        "threshold_days": QueryParameterValue(request.threshold_days, "INT64"),
        "source_upload_grace_hours": QueryParameterValue(request.source_upload_grace_hours, "INT64"),
        "telemetry_backend": QueryParameterValue(config.backend_name, "STRING"),
    }
    sql = load_sql(RESTRICTED_SQL_FILE, config=config)
    if sql.count(MARKER) != 1:
        raise ValueError("B-8 restricted SQL must contain one fragment marker")
    sql = sql.replace(MARKER, load_sql(FRAGMENT, config=config))
    names = named_parameter_names(sql)
    missing = names - values.keys()
    if missing:
        raise ValueError(f"Unbound parameters: {missing}")
    return QuerySpec(sql, {key: values[key] for key in names})


def _one(frame: pd.DataFrame, name: str) -> Mapping[str, Any]:
    if len(frame) != 1:
        raise ValueError(f"{name} must return exactly one row")
    return frame.iloc[0].to_dict()


def _count(row: Mapping[str, Any], name: str) -> int:
    value = row.get(name)
    if value is None or pd.isna(value) or isinstance(value, bool):
        raise ValueError(f"Missing count: {name}")
    number = int(value)
    if number < 0 or number != value:
        raise ValueError(f"Invalid count: {name}")
    return number


def _optional_number(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def assemble_bundle(
    request: ObservedAppReturnRequest,
    frames: Mapping[str, pd.DataFrame],
    *,
    estimated_bytes: int,
    generated_at_utc: datetime,
) -> ReportBundle:
    cohorts = frames["cohorts"].copy()
    if cohorts.empty:
        cohorts = pd.DataFrame([{
            **{key: 0 for key in COHORT_COLUMNS if key not in {
                "cohortType", "cohortValue", "observedReturnRate", "rightCensoredRate",
                "returnedWithinThresholdCount", "returnedAfterThresholdCount",
                "noObservedReturnBeyondThresholdCount", "thresholdRightCensoredCount",
                "thresholdIneligibleCount",
            }},
            "cohortType": "overall", "cohortValue": "All",
        }])
    overall = _one(cohorts[cohorts["cohortType"] == "overall"], "overall")
    latency = _one(frames["latency"], "latency")
    quality_row = _one(frames["quality"], "quality")
    quality = {key: _count(quality_row, key) for key in QUALITY_FIELDS}
    sample = {
        key: _count(overall, key)
        for key in (
            "anchorFinalAttempts", "eligibleAnchors", "ineligibleAnchors",
            "observedReturnCount", "coldStartReturnCount",
            "foregroundResumeReturnCount", "rightCensoredCount",
        )
    }
    if sample["anchorFinalAttempts"] != sample["eligibleAnchors"] + sample["ineligibleAnchors"]:
        raise ValueError("B-8 anchor invariant failed")
    if sample["eligibleAnchors"] != sample["observedReturnCount"] + sample["rightCensoredCount"]:
        raise ValueError("B-8 observation invariant failed")
    if sample["observedReturnCount"] != (
        sample["coldStartReturnCount"] + sample["foregroundResumeReturnCount"]
    ):
        raise ValueError("B-8 return-kind invariant failed")
    if quality["anchorsTotal"] != sample["anchorFinalAttempts"] or (
        quality["anchorsEligible"] != sample["eligibleAnchors"]
        or quality["anchorsIneligible"] != sample["ineligibleAnchors"]
    ):
        raise ValueError("B-8 quality/cohort counts disagree")
    for cohort_type in ("outcome", "stage"):
        group = cohorts[cohorts["cohortType"] == cohort_type]
        if group["cohortValue"].duplicated().any():
            raise ValueError(f"B-8 duplicate {cohort_type} cohort")
        for key in (
            "anchorFinalAttempts", "eligibleAnchors", "observedReturnCount",
            "coldStartReturnCount", "foregroundResumeReturnCount", "rightCensoredCount",
        ):
            if sum(_count(row, key) for _, row in group.iterrows()) != sample[key]:
                raise ValueError(f"B-8 {cohort_type} cohort total disagrees: {key}")
    if _count(latency, "observedCount") != sample["observedReturnCount"]:
        raise ValueError("B-8 latency count disagrees")
    for key, numerator in (
        ("observedReturnRate", "observedReturnCount"),
        ("rightCensoredRate", "rightCensoredCount"),
    ):
        actual = _optional_number(overall.get(key))
        expected = sample[numerator] / sample["eligibleAnchors"] if sample["eligibleAnchors"] else None
        if (actual is None) != (expected is None) or (
            actual is not None and abs(actual - expected) > 1e-12
        ):
            raise ValueError(f"B-8 rate invariant failed: {key}")
    threshold_names = (
        "returnedWithinThresholdCount", "returnedAfterThresholdCount",
        "noObservedReturnBeyondThresholdCount", "thresholdRightCensoredCount",
        "thresholdIneligibleCount",
    )
    if request.threshold_days is None:
        if any(_optional_number(overall.get(key)) is not None for key in threshold_names):
            raise ValueError("Threshold values present when disabled")
        threshold = {key: None for key in threshold_names}
    else:
        threshold = {key: _count(overall, key) for key in threshold_names}
        if sum(threshold.values()) != sample["anchorFinalAttempts"]:
            raise ValueError("B-8 threshold invariant failed")
        if threshold["thresholdIneligibleCount"] != sample["ineligibleAnchors"]:
            raise ValueError("B-8 threshold ineligible count disagrees")
    percentiles = {
        "timeToObservedAppReturnP50": _optional_number(latency.get("p50Seconds")),
        "timeToObservedAppReturnP75": _optional_number(latency.get("p75Seconds")),
        "timeToObservedAppReturnP90": _optional_number(latency.get("p90Seconds")),
    }
    if sample["observedReturnCount"] == 0 and any(value is not None for value in percentiles.values()):
        raise ValueError("Percentiles without observed returns")
    if sample["observedReturnCount"] > 0:
        values = list(percentiles.values())
        if any(value is None or value < 0 for value in values) or values != sorted(values):
            raise ValueError("Invalid B-8 observed latency percentiles")
    warnings = tuple(
        ReportWarning(code, message) for condition, code, message in (
            (sample["anchorFinalAttempts"] == 0, "NO_APP_RETURN_ANCHORS", "No final-attempt anchors matched the scope."),
            (sample["eligibleAnchors"] == 0, "NO_ELIGIBLE_APP_RETURN_ANCHORS", "No anchors had a valid lifecycle baseline."),
            (quality["conflictingOccurrenceKeys"] > 0, "LIFECYCLE_CONFLICT", "Conflicting occurrence keys were excluded."),
            (quality["runLifecycleLinkageMissing"] > 0, "RUN_LIFECYCLE_LINKAGE_UNKNOWN", "Some anchors lack exact run-start lifecycle linkage."),
        ) if condition
    )
    metrics = {
        "sample": {key: sample[key] for key in ("anchorFinalAttempts", "eligibleAnchors", "ineligibleAnchors")},
        "return": {
            **{key: sample[key] for key in ("observedReturnCount", "coldStartReturnCount", "foregroundResumeReturnCount")},
            "observedReturnRate": {
                "count": sample["observedReturnCount"],
                "denominator": sample["eligibleAnchors"],
                "ratio": _optional_number(overall.get("observedReturnRate")),
            },
        },
        "observation": {
            "rightCensoredCount": sample["rightCensoredCount"],
            "rightCensoredRate": {
                "count": sample["rightCensoredCount"],
                "denominator": sample["eligibleAnchors"],
                "ratio": _optional_number(overall.get("rightCensoredRate")),
            },
        },
        "latency": percentiles,
        "threshold": {
            "enabled": request.threshold_days is not None,
            "thresholdDays": request.threshold_days,
            "sourceUploadGraceHours": request.source_upload_grace_hours,
            **threshold,
        },
        "dataQuality": quality,
    }
    latency_table = pd.DataFrame([{
        "distribution": "observedReturnDelaySeconds",
        "observedCount": sample["observedReturnCount"],
        "p50Seconds": percentiles["timeToObservedAppReturnP50"],
        "p75Seconds": percentiles["timeToObservedAppReturnP75"],
        "p90Seconds": percentiles["timeToObservedAppReturnP90"],
    }])
    tables = {
        "overall-summary.csv": cohorts[cohorts["cohortType"] == "overall"],
        "outcome-summary.csv": cohorts[cohorts["cohortType"] == "outcome"],
        "stage-summary.csv": cohorts[cohorts["cohortType"] == "stage"],
        "latency-summary.csv": latency_table,
        "data-quality-summary.csv": pd.DataFrame([
            {"metric": key, "count": value} for key, value in quality.items()
        ]),
    }
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION, "observedAppReturn", ANALYSIS_VERSION,
        generated_at_utc, request.scope(), sample, quality,
        {
            "returnDefinition": "ObservedAppReturn",
            "anchorObservationUnit": "Canonical final attempt, including final resume or lifecycle terminal",
            "identitySemantics": "Same backend, environment, and durable telemetry player identity",
            "sourceCompletionSemantics": "No global complete-through authority; grace delays threshold maturity only",
            "noObservedReturnMeansChurn": False,
        },
        warnings, estimated_bytes,
    )
    markdown = (
        "# Observed App Return Report\n\n"
        f"- Environment: `{request.environment}`\n"
        f"- Content version: `{request.content_version}`\n"
        "- Return definition: `ObservedAppReturn`\n"
        f"- Final anchors: {sample['anchorFinalAttempts']}\n"
        f"- Eligible anchors: {sample['eligibleAnchors']}\n"
        f"- Observed returns: {sample['observedReturnCount']}\n"
        f"- Right-censored: {sample['rightCensoredCount']}\n\n"
        "A return is a runtime ColdStart or ForegroundResume observation, not a human-intent claim. "
        "No observation does not imply churn or uninstall. Delayed uploads can change later snapshots.\n"
    )
    return ReportBundle(metadata, metrics, markdown, tables)


def estimate_bytes(
    request: ObservedAppReturnRequest, *, config: AnalyticsConfig, client: Any,
) -> tuple[dict[str, QuerySpec], dict[str, int]]:
    queries = build_queries(request, config=config)
    estimates = {
        name: dry_run_query(query, client=client, config=config).total_bytes_processed
        for name, query in queries.items()
    }
    return queries, estimates


def analyze_observed_app_return(
    request: ObservedAppReturnRequest, *, config: AnalyticsConfig, client: Any,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
) -> ObservedAppReturnAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be nonnegative")
    queries, estimates = estimate_bytes(request, config=config, client=client)
    total = sum(estimates.values())
    if total > maximum_total_bytes:
        raise RuntimeError(
            f"B-8 dry-run estimate {total:,} bytes exceeds maximum "
            f"{maximum_total_bytes:,}; no analysis query was executed"
        )
    frames = {
        name: query_dataframe(
            query, client=client, config=config, maximum_bytes_billed=maximum_total_bytes
        )
        for name, query in queries.items()
    }
    bundle = assemble_bundle(
        request, frames, estimated_bytes=total, generated_at_utc=datetime.now(timezone.utc)
    )
    return ObservedAppReturnAnalysis(bundle, total, estimates)


def generate_observed_app_return_report(
    request: ObservedAppReturnRequest, *, config: AnalyticsConfig, client: Any,
    output_root: Path = Path("reports/generated"), overwrite: bool = False,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
) -> Path:
    analysis = analyze_observed_app_return(
        request, config=config, client=client, maximum_total_bytes=maximum_total_bytes
    )
    return write_report_bundle(
        analysis.bundle, output_root=output_root, overwrite=overwrite,
        table_specs=TABLE_SPECS, include_manifest=True,
    )
