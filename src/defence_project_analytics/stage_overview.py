"""Stage Overview query and deterministic in-memory metric calculation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from defence_project_analytics.bigquery_client import query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec
from defence_project_analytics.sql_loader import load_query


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
