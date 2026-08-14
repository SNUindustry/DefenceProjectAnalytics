"""Minimal live read-only connection checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from defence_project_analytics.bigquery_client import (
    query_dataframe,
    table_exists,
    view_exists,
)
from defence_project_analytics.config import AnalyticsConfig


SMOKE_OBJECTS = {
    "telemetry_gameplay_segments_v1": "VIEW",
    "telemetry_attempt_outcomes_v1": "VIEW",
    "telemetry_upload_status_v1": "VIEW",
    "telemetry_run_summary": "TABLE",
    "telemetry_lobby_activity_events": "TABLE",
    "telemetry_transaction_events": "TABLE",
}


@dataclass(frozen=True, slots=True)
class SmokeResult:
    object_name: str
    expected_type: str
    exists: bool
    query_accessible: bool
    diagnostic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_connection_smoke(
    *, client: Any, config: AnalyticsConfig | None = None
) -> list[SmokeResult]:
    settings = config or AnalyticsConfig.from_env()
    results: list[SmokeResult] = []
    for object_name, expected_type in SMOKE_OBJECTS.items():
        exists = (
            view_exists(object_name, client=client, config=settings)
            if expected_type == "VIEW"
            else table_exists(object_name, client=client, config=settings)
        )
        if not exists:
            results.append(
                SmokeResult(object_name, expected_type, False, False, "object not found or wrong type")
            )
            continue
        try:
            query_dataframe(
                f"SELECT 1 AS accessible FROM `{settings.object_ref(object_name)}` LIMIT 1",
                client=client,
                config=settings,
            )
            results.append(SmokeResult(object_name, expected_type, True, True))
        except Exception as exc:  # API errors are preserved as concise diagnostics.
            results.append(
                SmokeResult(object_name, expected_type, True, False, str(exc).splitlines()[0])
            )
    return results

