"""Immutable models shared by generated analytics reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


REPORT_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class AnalysisScope:
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


@dataclass(frozen=True, slots=True)
class MetricRatio:
    count: int
    denominator: int
    ratio: float | None

    @classmethod
    def from_counts(cls, count: int, denominator: int) -> "MetricRatio":
        return cls(count=count, denominator=denominator, ratio=count / denominator if denominator else None)


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    observed_count: int
    missing_count: int
    mean: float | None = None
    p10: float | None = None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    p90: float | None = None


@dataclass(frozen=True, slots=True)
class SampleSummary:
    final_attempts: int
    unique_players: int
    deaths: int
    eligible_runs: int
    distinct_attempts: int
    partially_covered_attempts: int


@dataclass(frozen=True, slots=True)
class DataQuality:
    telemetry_complete_rate: MetricRatio
    detail_coverage_rate: MetricRatio
    unresolved_release_rows: int
    excluded_incomplete_detail_rows: int
    unassessed_legacy_detail_rows: int
    mixed_content_detail_rows: int
    partially_covered_attempts: int
    missing_death_attribution_rows: int
    approximate_death_state_rows: int
    missing_death_state_rows: int
    invalid_death_time_rows: int


@dataclass(frozen=True, slots=True)
class ReportDefinitions:
    population: str
    clear_rate: str
    detail_eligibility: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReportMetadata:
    report_contract_version: str
    analysis_type: str
    analysis_version: str
    generated_at_utc: datetime
    scope: AnalysisScope
    sample: SampleSummary
    quality: DataQuality
    definitions: ReportDefinitions
    warnings: tuple[ReportWarning, ...] = ()
    dry_run_estimated_bytes: int = 0

    def __post_init__(self) -> None:
        if self.report_contract_version != REPORT_CONTRACT_VERSION:
            raise ValueError(f"Unsupported report contract: {self.report_contract_version}")
        value = self.generated_at_utc
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at_utc must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReportBundle:
    metadata: ReportMetadata
    metrics: Mapping[str, Any]
    markdown: str
    tables: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


def utc_now_seconds() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
