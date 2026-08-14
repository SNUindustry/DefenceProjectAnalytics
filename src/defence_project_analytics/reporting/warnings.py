"""Stable data-quality warning generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from defence_project_analytics.reporting.models import DataQuality, ReportWarning, SampleSummary


WARNING_PRIORITY = (
    "NO_ATTEMPTS",
    "LOW_SAMPLE_ATTEMPTS",
    "LOW_SAMPLE_PLAYERS",
    "LOW_SAMPLE_DEATHS",
    "LOW_DETAIL_COVERAGE",
    "INCOMPLETE_DETAIL_EXCLUDED",
    "UNASSESSED_LEGACY_DETAIL",
    "MIXED_CONTENT_DETAIL_EXCLUDED",
    "MISSING_DEATH_ATTRIBUTION",
    "APPROXIMATE_DEATH_STATE",
    "UNRESOLVED_RELEASE_ROWS",
    "UNRECOGNIZED_FINAL_OUTCOME",
    "UNRECOGNIZED_DAMAGE_SOURCE",
    "INVALID_DEATH_TIME",
)
_PRIORITY = {code: index for index, code in enumerate(WARNING_PRIORITY)}


@dataclass(frozen=True, slots=True)
class WarningThresholds:
    min_final_attempts: int = 30
    min_unique_players: int = 10
    min_deaths: int = 20
    min_detail_runs: int = 20

    def __post_init__(self) -> None:
        if min(self.min_final_attempts, self.min_unique_players, self.min_deaths, self.min_detail_runs) < 0:
            raise ValueError("Warning thresholds must be non-negative")


def sort_warnings(warnings: Iterable[ReportWarning]) -> tuple[ReportWarning, ...]:
    return tuple(sorted(warnings, key=lambda item: (_PRIORITY.get(item.code, len(_PRIORITY)), item.code)))


def build_warnings(
    sample: SampleSummary,
    quality: DataQuality,
    *,
    thresholds: WarningThresholds = WarningThresholds(),
    unrecognized_outcomes: int = 0,
    unrecognized_damage_sources: int = 0,
) -> tuple[ReportWarning, ...]:
    warnings: list[ReportWarning] = []

    def add(code: str, message: str) -> None:
        warnings.append(ReportWarning(code, message))

    if sample.final_attempts == 0:
        add("NO_ATTEMPTS", "No final attempts matched the requested scope.")
    elif sample.final_attempts < thresholds.min_final_attempts:
        add("LOW_SAMPLE_ATTEMPTS", f"Final attempts ({sample.final_attempts}) are below {thresholds.min_final_attempts}.")
    if sample.unique_players < thresholds.min_unique_players:
        add("LOW_SAMPLE_PLAYERS", f"Unique players ({sample.unique_players}) are below {thresholds.min_unique_players}.")
    if sample.deaths < thresholds.min_deaths:
        add("LOW_SAMPLE_DEATHS", f"Deaths ({sample.deaths}) are below {thresholds.min_deaths}.")
    if sample.eligible_runs < thresholds.min_detail_runs:
        add("LOW_DETAIL_COVERAGE", f"Detail-eligible runs ({sample.eligible_runs}) are below {thresholds.min_detail_runs}.")
    if quality.excluded_incomplete_detail_rows:
        add("INCOMPLETE_DETAIL_EXCLUDED", f"Excluded {quality.excluded_incomplete_detail_rows} explicitly incomplete detail rows.")
    if quality.unassessed_legacy_detail_rows:
        add("UNASSESSED_LEGACY_DETAIL", f"Excluded {quality.unassessed_legacy_detail_rows} detail rows without upload status.")
    if quality.mixed_content_detail_rows:
        add("MIXED_CONTENT_DETAIL_EXCLUDED", f"Excluded {quality.mixed_content_detail_rows} detail rows outside the requested content/release scope.")
    if quality.missing_death_attribution_rows:
        add("MISSING_DEATH_ATTRIBUTION", f"{quality.missing_death_attribution_rows} deaths have no final source attribution.")
    if quality.approximate_death_state_rows:
        add("APPROXIMATE_DEATH_STATE", f"{quality.approximate_death_state_rows} death-state rows use terminal snapshot approximation.")
    if quality.unresolved_release_rows:
        add("UNRESOLVED_RELEASE_ROWS", f"{quality.unresolved_release_rows} final attempts have unresolved release identity.")
    if unrecognized_outcomes:
        add("UNRECOGNIZED_FINAL_OUTCOME", f"Found {unrecognized_outcomes} final attempts with unrecognized outcomes.")
    if unrecognized_damage_sources:
        add("UNRECOGNIZED_DAMAGE_SOURCE", f"Found {unrecognized_damage_sources} rows with unrecognized damage source codes.")
    if quality.invalid_death_time_rows:
        add("INVALID_DEATH_TIME", f"{quality.invalid_death_time_rows} deaths have missing or negative timing.")
    return sort_warnings(warnings)
