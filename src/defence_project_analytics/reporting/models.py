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


@dataclass(frozen=True, slots=True, kw_only=True)
class WeaponAnalysisScope(AnalysisScope):
    """B-2-only scope extension; the B-1 scope shape remains unchanged."""

    segment_ended_at_utc_start: datetime | None = None
    segment_ended_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime


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


@dataclass(frozen=True, slots=True)
class WeaponSampleSummary:
    final_attempts: int
    unique_players: int
    clears: int
    deaths: int
    abandons: int
    detail_candidate_attempts: int
    detail_observed_attempts: int
    detail_eligible_attempts: int
    partially_covered_attempts: int
    eligible_segments: int
    weapon_family_count: int


@dataclass(frozen=True, slots=True)
class WeaponDataQuality:
    telemetry_complete_rate: MetricRatio
    detail_coverage_rate: MetricRatio
    weapon_summary_coverage: MetricRatio
    final_weapon_state_coverage: MetricRatio
    dps_coverage_among_combat_observed_instance_segments: MetricRatio
    unresolved_release_rows: int
    incomplete_segments_excluded: int
    legacy_unassessed_segments: int
    mixed_content_segments_excluded: int
    partially_covered_attempts: int
    missing_weapon_identity_rows: int
    inconsistent_damage_share_rows: int
    unmapped_acquisition_rows: int
    discarded_dps_samples: int


@dataclass(frozen=True, slots=True)
class WeaponReportDefinitions:
    population: str
    clear_rate: str
    detail_eligibility: str
    outcome_observation_unit: str
    adoption_observation_unit: str
    combat_observation_unit: str
    dps_observation_unit: str
    final_ownership_observation_unit: str
    boss_observation_unit: str
    dps_coverage: str
    outcome_association_is_causal: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WeaponAdoptionMetrics:
    weapon_family_count: int
    top_attempt_inclusion: tuple[Mapping[str, Any], ...]
    top_final_ownership: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WeaponCombatMetrics:
    total_damage: float
    total_boss_damage: float
    total_hits: int
    top_damage_share: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WeaponDpsMetrics:
    valid_sample_count: int
    invalid_sample_count: int
    coverage: MetricRatio
    top_effective_dps: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WeaponBossMetrics:
    boss_eligible_segments: int
    valid_boss_sample_count: int
    top_boss_damage: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WeaponOutcomeAssociationMetrics:
    rows: int
    top_clear_rate_differences: tuple[Mapping[str, Any], ...]
    observational: bool = True


@dataclass(frozen=True, slots=True)
class WeaponProgressionMetrics:
    final_state_assessed_attempts: int
    top_final_ownership: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WeaponPerformanceMetrics:
    sample: WeaponSampleSummary
    adoption: WeaponAdoptionMetrics
    combat_performance: WeaponCombatMetrics
    dps: WeaponDpsMetrics
    boss_performance: WeaponBossMetrics
    outcome_association: WeaponOutcomeAssociationMetrics
    progression_state: WeaponProgressionMetrics
    data_quality: WeaponDataQuality


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeAnalysisScope(AnalysisScope):
    """B-3-only reproducible scope extension."""

    segment_ended_at_utc_start: datetime | None = None
    segment_ended_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime


@dataclass(frozen=True, slots=True)
class UpgradeSampleSummary:
    final_attempts: int
    unique_players: int
    clears: int
    deaths: int
    abandons: int
    candidate_count: int
    detail_candidate_segments: int
    transport_eligible_segments: int
    choice_eligible_segments: int
    fully_choice_covered_attempts: int
    partially_covered_attempts: int
    complete_exposures: int


@dataclass(frozen=True, slots=True)
class UpgradeDataQuality:
    telemetry_complete_rate: MetricRatio
    detail_coverage_rate: MetricRatio
    choice_coverage_rate: MetricRatio
    attempt_choice_coverage_rate: MetricRatio
    observed_exposures: int
    complete_exposures: int
    truncated_exposures: int
    malformed_exposures: int
    dropped_exposures: int
    omitted_candidates: int
    incomplete_segments_excluded: int
    legacy_unassessed_segments: int
    mixed_content_segments_excluded: int
    partially_covered_attempts: int
    linked_selections: int
    legacy_unlinked_selections: int
    selections_without_exposure_match: int
    no_selection_exposures: int
    multiple_selection_exposures: int
    selection_count_mismatch_segments: int
    missing_candidate_identity_rows: int
    unrecognized_category_rows: int
    approximate_context_rows: int
    stale_context_rows: int
    missing_context_rows: int
    snapshot_lag_p50: float | None
    snapshot_lag_p75: float | None
    snapshot_lag_p90: float | None
    invalid_outcome_time_rows: int


@dataclass(frozen=True, slots=True)
class UpgradeReportDefinitions:
    exposure_observation_unit: str
    candidate_observation_unit: str
    selection_observation_unit: str
    head_to_head_observation_unit: str
    outcome_observation_unit: str
    context_observation_unit: str
    pick_rate: str
    clear_rate: str
    detail_eligibility: str
    selection_count_scope: str
    exposed_not_selected_composition: str
    context_snapshot_max_lag_seconds: float
    selection_outcome_association_is_causal: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UpgradeExposureMetrics:
    observed_exposures: int
    complete_exposures: int
    candidate_count: int
    top_candidate_exposure: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class UpgradeSelectionMetrics:
    linked_selections: int
    no_selection_exposures: int
    top_pick_rates: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class UpgradeChoiceContextMetrics:
    approximate_rows: int
    stale_rows: int
    missing_rows: int
    top_context_rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class UpgradeHeadToHeadMetrics:
    pair_count: int
    top_conditional_preferences: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class UpgradeOutcomeAssociationMetrics:
    candidate_count: int
    exposed_not_selected_attempts: int
    alternative_selected_attempts: int
    no_selection_only_attempts: int
    top_clear_rate_differences: tuple[Mapping[str, Any], ...]
    observational: bool = True


@dataclass(frozen=True, slots=True)
class UpgradeChoiceMetrics:
    sample: UpgradeSampleSummary
    exposure: UpgradeExposureMetrics
    selection: UpgradeSelectionMetrics
    choice_context: UpgradeChoiceContextMetrics
    head_to_head: UpgradeHeadToHeadMetrics
    outcome_association: UpgradeOutcomeAssociationMetrics
    data_quality: UpgradeDataQuality


def utc_now_seconds() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
