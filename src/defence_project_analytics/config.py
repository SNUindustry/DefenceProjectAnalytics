"""Runtime configuration for the analytics foundation."""

from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_PROJECT_ID = "bald-ops"
DEFAULT_DATASET_ID = "game_telemetry"
DEFAULT_LOCATION = "asia-northeast3"


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    """BigQuery identifiers; filters belong to individual query requests."""

    project_id: str = DEFAULT_PROJECT_ID
    dataset_id: str = DEFAULT_DATASET_ID
    location: str = DEFAULT_LOCATION

    @classmethod
    def from_env(cls) -> "AnalyticsConfig":
        return cls(
            project_id=os.getenv("DPA_GCP_PROJECT", DEFAULT_PROJECT_ID),
            dataset_id=os.getenv("DPA_BIGQUERY_DATASET", DEFAULT_DATASET_ID),
            location=os.getenv("DPA_BIGQUERY_LOCATION", DEFAULT_LOCATION),
        )

    def object_ref(self, object_name: str) -> str:
        if not object_name or "`" in object_name or "." in object_name:
            raise ValueError("object_name must be one unqualified BigQuery identifier")
        return f"{self.project_id}.{self.dataset_id}.{object_name}"

