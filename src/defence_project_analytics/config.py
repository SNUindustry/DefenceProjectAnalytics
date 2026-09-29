"""Runtime configuration for the analytics foundation."""

from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_PROJECT_ID = "bald-ops"
TEST_PROJECT_ID = "bald-ops-test"
DEFAULT_DATASET_ID = "game_telemetry"
DEFAULT_LOCATION = "asia-northeast3"

BACKEND_PROFILES = {
    "production": (DEFAULT_PROJECT_ID, DEFAULT_DATASET_ID, DEFAULT_LOCATION, "Production"),
    "test": (TEST_PROJECT_ID, DEFAULT_DATASET_ID, DEFAULT_LOCATION, "Test"),
}


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    """BigQuery identifiers; filters belong to individual query requests."""

    project_id: str = DEFAULT_PROJECT_ID
    dataset_id: str = DEFAULT_DATASET_ID
    location: str = DEFAULT_LOCATION
    backend_environment: str = "Production"
    backend_name: str = "custom"

    def __post_init__(self) -> None:
        if self.backend_environment not in {"Production", "Test"}:
            raise ValueError("backend_environment must be Production or Test")
        if not self.project_id or not self.dataset_id or not self.location:
            raise ValueError("project_id, dataset_id, and location must not be blank")

        profile = BACKEND_PROFILES.get(self.backend_name)
        if profile is not None:
            expected = profile[:3]
            actual = (self.project_id, self.dataset_id, self.location)
            if actual != expected or self.backend_environment != profile[3]:
                raise ValueError(
                    f"{self.backend_name} backend identifiers/environment do not match "
                    "the approved physical backend profile"
                )

    @classmethod
    def for_backend(cls, backend_name: str) -> "AnalyticsConfig":
        normalized = backend_name.strip().casefold()
        try:
            project_id, dataset_id, location, environment = BACKEND_PROFILES[normalized]
        except KeyError as exc:
            raise ValueError("backend must be test or production") from exc
        return cls(project_id, dataset_id, location, environment, normalized)

    @classmethod
    def from_env(cls) -> "AnalyticsConfig":
        backend_name = os.getenv("DPA_BACKEND", "production").strip().casefold()
        profile = cls.for_backend(backend_name)
        has_custom_identifiers = any(
            os.getenv(name) is not None
            for name in ("DPA_GCP_PROJECT", "DPA_BIGQUERY_DATASET", "DPA_BIGQUERY_LOCATION")
        )
        if has_custom_identifiers and os.getenv("DPA_BACKEND_ENVIRONMENT") is None:
            raise ValueError(
                "custom backend identifiers require DPA_BACKEND_ENVIRONMENT"
            )
        project_id = os.getenv("DPA_GCP_PROJECT", profile.project_id)
        dataset_id = os.getenv("DPA_BIGQUERY_DATASET", profile.dataset_id)
        location = os.getenv("DPA_BIGQUERY_LOCATION", profile.location)
        backend_environment = os.getenv("DPA_BACKEND_ENVIRONMENT", profile.backend_environment)
        return cls(
            project_id=project_id,
            dataset_id=dataset_id,
            location=location,
            backend_environment=backend_environment,
            backend_name="custom" if has_custom_identifiers else backend_name,
        )

    def require_environment(self, environment: str) -> None:
        if environment not in {"Production", "Test"}:
            raise ValueError("environment must be Production or Test")
        if environment != self.backend_environment:
            raise ValueError(
                f"logical environment {environment} cannot use physical "
                f"{self.backend_environment} backend {self.project_id}.{self.dataset_id}"
            )

    def object_ref(self, object_name: str) -> str:
        if not object_name or "`" in object_name or "." in object_name:
            raise ValueError("object_name must be one unqualified BigQuery identifier")
        return f"{self.project_id}.{self.dataset_id}.{object_name}"
