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

WEAPON_WARNING_PRIORITY = (
    "NO_ATTEMPTS",
    "LOW_SAMPLE_ATTEMPTS",
    "INCOMPLETE_DETAIL_EXCLUDED",
    "UNASSESSED_LEGACY_DETAIL",
    "MIXED_CONTENT_DETAIL_EXCLUDED",
    "UNRESOLVED_RELEASE_ROWS",
    "HIGH_WEAPON_DETAIL_MISSINGNESS",
    "MISSING_WEAPON_IDENTITY",
    "MIXED_WEAPON_STATE",
    "WEAPON_SAMPLE_TRUNCATED",
    "LOW_WEAPON_SAMPLE",
    "LOW_WEAPON_DPS_COVERAGE",
    "LOW_BOSS_SAMPLE",
    "OUTCOME_ASSOCIATION_BIASED_SAMPLE",
)


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


@dataclass(frozen=True, slots=True)
class WeaponThresholds:
    min_combat_observed_attempts: int = 20
    min_final_owned_attempts: int = 20
    min_valid_dps_samples: int = 20
    min_boss_eligible_segments: int = 10
    min_outcome_cohort_attempts: int = 20
    min_detail_coverage: float = 0.80
    min_dps_coverage: float = 0.50
    min_notable_clear_rate_difference: float = 0.10

    def __post_init__(self) -> None:
        counts = (
            self.min_combat_observed_attempts,
            self.min_final_owned_attempts,
            self.min_valid_dps_samples,
            self.min_boss_eligible_segments,
            self.min_outcome_cohort_attempts,
        )
        if min(counts) < 0:
            raise ValueError("Weapon count thresholds must be non-negative")
        for value in (self.min_detail_coverage, self.min_dps_coverage, self.min_notable_clear_rate_difference):
            if not 0 <= value <= 1:
                raise ValueError("Weapon ratio thresholds must be between zero and one")


def sort_weapon_warnings(warnings: Iterable[ReportWarning]) -> tuple[ReportWarning, ...]:
    priority = {code: index for index, code in enumerate(WEAPON_WARNING_PRIORITY)}
    return tuple(sorted(warnings, key=lambda item: (priority.get(item.code, len(priority)), item.code)))


UPGRADE_WARNING_PRIORITY = (
    "NO_ATTEMPTS",
    "LOW_UPGRADE_EXPOSURE_SAMPLE",
    "INCOMPLETE_DETAIL_EXCLUDED",
    "UNASSESSED_LEGACY_DETAIL",
    "MIXED_CONTENT_DETAIL_EXCLUDED",
    "TRUNCATED_UPGRADE_EXPOSURES",
    "UPGRADE_EXPOSURE_OVERFLOW",
    "MALFORMED_UPGRADE_EXPOSURE",
    "UPGRADE_SELECTION_COUNT_MISMATCH",
    "MISSING_CANDIDATE_IDENTITY",
    "UNRECOGNIZED_UPGRADE_CATEGORY",
    "UNLINKED_UPGRADE_SELECTION",
    "SELECTION_WITHOUT_EXPOSURE_MATCH",
    "MULTIPLE_SELECTIONS_FOR_EXPOSURE",
    "APPROXIMATE_CHOICE_CONTEXT",
    "LOW_CANDIDATE_SAMPLE",
    "LOW_HEAD_TO_HEAD_SAMPLE",
    "OUTCOME_ASSOCIATION_BIASED_SAMPLE",
)


@dataclass(frozen=True, slots=True)
class UpgradeThresholds:
    min_complete_exposures: int = 20
    min_candidate_exposures: int = 20
    min_pair_co_exposures: int = 20
    min_pair_selected: int = 10
    min_outcome_cohort_attempts: int = 20
    high_pick_rate: float = 0.70
    low_pick_rate: float = 0.20
    notable_pair_preference: float = 0.70
    notable_clear_rate_difference: float = 0.10
    max_context_snapshot_lag_seconds: float = 30.0
    min_detail_coverage: float = 0.80

    def __post_init__(self) -> None:
        counts = (
            self.min_complete_exposures,
            self.min_candidate_exposures,
            self.min_pair_co_exposures,
            self.min_pair_selected,
            self.min_outcome_cohort_attempts,
        )
        if min(counts) < 0:
            raise ValueError("Upgrade count thresholds must be non-negative")
        ratios = (
            self.high_pick_rate,
            self.low_pick_rate,
            self.notable_pair_preference,
            self.notable_clear_rate_difference,
            self.min_detail_coverage,
        )
        if any(not 0 <= value <= 1 for value in ratios):
            raise ValueError("Upgrade ratio thresholds must be between zero and one")
        if self.low_pick_rate > self.high_pick_rate:
            raise ValueError("low_pick_rate must not exceed high_pick_rate")
        if self.max_context_snapshot_lag_seconds < 0:
            raise ValueError("max_context_snapshot_lag_seconds must be non-negative")


def sort_upgrade_warnings(warnings: Iterable[ReportWarning]) -> tuple[ReportWarning, ...]:
    priority = {code: index for index, code in enumerate(UPGRADE_WARNING_PRIORITY)}
    return tuple(sorted(warnings, key=lambda item: (priority.get(item.code, len(priority)), item.code)))
