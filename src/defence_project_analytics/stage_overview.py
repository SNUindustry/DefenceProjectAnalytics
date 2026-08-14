"""Stage Overview query and deterministic in-memory metric calculation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from typing import Any

import pandas as pd

from defence_project_analytics.bigquery_client import query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec
from defence_project_analytics.sql_loader import load_query
from defence_project_analytics.reporting.models import (
    REPORT_CONTRACT_VERSION,
    AnalysisScope,
    DataQuality,
    MetricRatio,
    ReportBundle,
    ReportDefinitions,
    ReportMetadata,
    SampleSummary,
    utc_now_seconds,
)
from defence_project_analytics.reporting.writer import write_report_bundle


RUN_FACT_SQL = "sql/analysis/run_fact_v1.sql"
_COUNT_FIELDS = {
    "final_attempts",
    "unique_telemetry_players",
    "clears",
    "deaths",
    "abandons",
    "feedback_exposure_count",
    "positive_responses",
    "negative_responses",
    "telemetry_complete_assessed_attempts",
}


@dataclass(frozen=True, slots=True)
class StageOverviewRequest:
    stage_key: str
    environment: str
    content_version: int | None = None

    def __post_init__(self) -> None:
        if not self.stage_key.strip():
            raise ValueError("stage_key must not be empty")
        if not self.environment.strip():
            raise ValueError("environment must not be empty")


def build_run_fact_query(
    request: StageOverviewRequest,
    *,
    config: AnalyticsConfig | None = None,
) -> QuerySpec:
    return load_query(
        RUN_FACT_SQL,
        config=config,
        parameters={
            "stage_key": request.stage_key,
            "environment": request.environment,
            "content_version": QueryParameterValue(
                request.content_version, bigquery_type="INT64"
            ),
        },
    )


def build_stage_overview_query(
    request: StageOverviewRequest,
    *,
    config: AnalyticsConfig | None = None,
) -> QuerySpec:
    run_fact = build_run_fact_query(request, config=config)
    run_fact_sql = run_fact.sql.strip().rstrip(";")
    sql = f"""
WITH run_fact AS (
{run_fact_sql}
),
counts AS (
  SELECT
    COUNT(*) AS final_attempts,
    COUNT(DISTINCT NULLIF(TRIM(telemetryPlayerId), '')) AS unique_telemetry_players,
    COUNTIF(gameplayOutcome = 'Clear') AS clears,
    COUNTIF(gameplayOutcome = 'Dead') AS deaths,
    COUNTIF(gameplayOutcome = 'Abandon') AS abandons,
    SAFE_DIVIDE(
      COUNTIF(gameplayOutcome = 'Clear'),
      COUNTIF(gameplayOutcome IN ('Clear', 'Dead'))
    ) AS clear_rate,
    COUNTIF(feedbackExposed IS TRUE) AS feedback_exposure_count,
    COUNTIF(feedbackResponse = 'Positive') AS positive_responses,
    COUNTIF(feedbackResponse = 'Negative') AS negative_responses,
    SAFE_DIVIDE(
      COUNTIF(feedbackResponse = 'Positive'),
      COUNTIF(feedbackResponse IN ('Positive', 'Negative'))
    ) AS positive_response_rate,
    COUNTIF(telemetryComplete IS NOT NULL) AS telemetry_complete_assessed_attempts,
    SAFE_DIVIDE(
      COUNTIF(telemetryComplete IS TRUE),
      COUNTIF(telemetryComplete IS NOT NULL)
    ) AS telemetry_complete_rate
  FROM run_fact
),
duration_rows AS (
  SELECT
    PERCENTILE_CONT(attemptElapsedTimeSeconds, 0.25) OVER() AS p25_attempt_elapsed_seconds,
    PERCENTILE_CONT(attemptElapsedTimeSeconds, 0.50) OVER() AS median_attempt_elapsed_seconds,
    PERCENTILE_CONT(attemptElapsedTimeSeconds, 0.75) OVER() AS p75_attempt_elapsed_seconds
  FROM run_fact
  WHERE attemptElapsedTimeSeconds IS NOT NULL
),
durations AS (
  SELECT
    ANY_VALUE(p25_attempt_elapsed_seconds) AS p25_attempt_elapsed_seconds,
    ANY_VALUE(median_attempt_elapsed_seconds) AS median_attempt_elapsed_seconds,
    ANY_VALUE(p75_attempt_elapsed_seconds) AS p75_attempt_elapsed_seconds
  FROM duration_rows
)
SELECT counts.*, durations.*
FROM counts
CROSS JOIN durations
""".strip()
    return QuerySpec(sql=sql, parameters=run_fact.parameters)


def _safe_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def calculate_stage_overview(frame: pd.DataFrame) -> dict[str, Any]:
    """Calculate Stage Overview metrics from an already bounded run-fact frame."""

    outcomes = frame.get("gameplayOutcome", pd.Series(index=frame.index, dtype="object"))
    players = frame.get("telemetryPlayerId", pd.Series(index=frame.index, dtype="object"))
    durations = pd.to_numeric(
        frame.get("attemptElapsedTimeSeconds", pd.Series(index=frame.index, dtype="float64")),
        errors="coerce",
    ).dropna()
    feedback_exposed = frame.get(
        "feedbackExposed", pd.Series(False, index=frame.index, dtype="boolean")
    ).fillna(False)
    feedback_response = frame.get(
        "feedbackResponse", pd.Series(index=frame.index, dtype="object")
    )
    completeness = frame.get(
        "telemetryComplete", pd.Series(index=frame.index, dtype="boolean")
    )

    clears = int(outcomes.eq("Clear").sum())
    deaths = int(outcomes.eq("Dead").sum())
    abandons = int(outcomes.eq("Abandon").sum())
    positive = int(feedback_response.eq("Positive").sum())
    negative = int(feedback_response.eq("Negative").sum())
    assessed = int(completeness.notna().sum())
    complete = int(completeness.eq(True).sum())  # noqa: E712 - nullable boolean comparison
    normalized_players = players.astype("string").str.strip().replace("", pd.NA)

    return {
        "final_attempts": int(len(frame)),
        "unique_telemetry_players": int(normalized_players.nunique(dropna=True)),
        "clears": clears,
        "deaths": deaths,
        "abandons": abandons,
        "clear_rate": _safe_rate(clears, clears + deaths),
        "median_attempt_elapsed_seconds": (
            float(durations.quantile(0.50)) if not durations.empty else None
        ),
        "p25_attempt_elapsed_seconds": (
            float(durations.quantile(0.25)) if not durations.empty else None
        ),
        "p75_attempt_elapsed_seconds": (
            float(durations.quantile(0.75)) if not durations.empty else None
        ),
        "feedback_exposure_count": int(feedback_exposed.eq(True).sum()),  # noqa: E712
        "positive_responses": positive,
        "negative_responses": negative,
        "positive_response_rate": _safe_rate(positive, positive + negative),
        "telemetry_complete_assessed_attempts": assessed,
        "telemetry_complete_rate": _safe_rate(complete, assessed),
    }


def get_stage_overview(
    request: StageOverviewRequest,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_bytes_billed: int | None = None,
) -> dict[str, Any]:
    """Fetch a single aggregate row; raw telemetry is never dumped locally."""

    query = build_stage_overview_query(request, config=config)
    frame = query_dataframe(
        query,
        client=client,
        config=config,
        maximum_bytes_billed=maximum_bytes_billed,
    )
    if len(frame) != 1:
        raise RuntimeError(f"Stage Overview expected one aggregate row, got {len(frame)}")
    row = frame.iloc[0]
    result: dict[str, Any] = {}
    for key, value in row.items():
        if pd.isna(value):
            result[key] = None
        elif key in _COUNT_FIELDS:
            result[key] = int(value)
        elif hasattr(value, "item"):
            result[key] = value.item()
        else:
            result[key] = value
    return result


def generate_stage_overview_report(
    request: StageOverviewRequest,
    metrics: dict[str, Any],
    *,
    output_root: Path,
    overwrite: bool = False,
    clock: Callable[[], datetime] = utc_now_seconds,
) -> Path:
    """Adapt an existing Stage Overview result to the common report bundle."""

    if request.content_version is None:
        raise ValueError("content_version is required when generating a Stage Overview report")
    final_attempts = int(metrics.get("final_attempts") or 0)
    unique_players = int(metrics.get("unique_telemetry_players") or 0)
    deaths = int(metrics.get("deaths") or 0)
    assessed = int(metrics.get("telemetry_complete_assessed_attempts") or 0)
    complete_rate = metrics.get("telemetry_complete_rate")
    complete = round(float(complete_rate) * assessed) if complete_rate is not None else 0
    completeness = MetricRatio.from_counts(complete, assessed)
    quality = DataQuality(
        telemetry_complete_rate=completeness,
        detail_coverage_rate=completeness,
        unresolved_release_rows=0,
        excluded_incomplete_detail_rows=max(assessed - complete, 0),
        unassessed_legacy_detail_rows=max(final_attempts - assessed, 0),
        mixed_content_detail_rows=0,
        partially_covered_attempts=0,
        missing_death_attribution_rows=0,
        approximate_death_state_rows=0,
        missing_death_state_rows=0,
        invalid_death_time_rows=0,
    )
    scope = AnalysisScope(request.environment, request.stage_key, request.content_version)
    sample = SampleSummary(final_attempts, unique_players, deaths, complete, complete, 0)
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION,
        "stageOverview",
        "1.0.0",
        clock(),
        scope,
        sample,
        quality,
        ReportDefinitions(
            population="One row per final attempt from telemetry_attempt_outcomes_v1.",
            clear_rate="Clear / (Clear + Dead); Abandon is excluded.",
            detail_eligibility="Completeness is assessed from upload status when available.",
        ),
    )
    clear_rate = metrics.get("clear_rate")
    clear_display = "n/a" if clear_rate is None else f"{float(clear_rate):.1%}"
    markdown = f"""# Stage Overview Report

## Scope

- Environment: `{request.environment}`
- Stage: `{request.stage_key}`
- Content version: `{request.content_version}`

## Outcome

- Final attempts: {final_attempts}
- Clears: {int(metrics.get('clears') or 0)}
- Deaths: {deaths}
- Abandons: {int(metrics.get('abandons') or 0)}
- Clear rate (Clear / (Clear + Dead)): {clear_display}

## Caveats

- Nullable legacy upload status is excluded from the telemetry completeness denominator.
"""
    bundle = ReportBundle(metadata, metrics, markdown)
    return write_report_bundle(bundle, output_root=output_root, overwrite=overwrite)
