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


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgressionAnalysisScope:
    """Stage-less reproducible scope for progression-to-next-run analysis."""

    environment: str
    content_version: int
    progression_kind: str | None = None
    previous_stage_key: str | None = None
    next_stage_key: str | None = None
    app_version: str | None = None
    release_id: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    progression_occurred_at_utc_start: datetime | None = None
    progression_occurred_at_utc_end: datetime | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime
    previous_run_max_gap_minutes: int = 30
    next_run_max_gap_minutes: int = 30


@dataclass(frozen=True, slots=True)
class ProgressionSampleSummary:
    progression_events: int
    unique_players: int
    progression_kinds: int
    unique_targets: int
    bounded_episodes: int
    unbounded_progression_events: int
    single_progression_episodes: int
    multi_progression_episodes: int
    mature_episodes: int
    right_censored_episodes: int
    episodes_with_previous_run: int
    episodes_with_next_run: int
    same_stage_paired_episodes: int


@dataclass(frozen=True, slots=True)
class ProgressionDataQuality:
    physical_progression_rows: int
    deduped_progression_events: int
    conflicting_progression_events: int
    missing_player_identity_events: int
    missing_occurred_at_events: int
    missing_target_identity_events: int
    unrecognized_progression_kind_events: int
    transaction_linked_events: int
    standalone_events: int
    invalid_transaction_link_events: int
    unexpected_standalone_events: int
    unassessed_unrecognized_linkage_events: int
    both_boundary_episodes: int
    previous_only_episodes: int
    next_only_episodes: int
    unbounded_progression_events: int
    unbounded_progression_players: int
    previous_context_eligible_episodes: int
    previous_run_beyond_window_episodes: int
    previous_run_missing_episodes: int
    right_censored_episodes: int
    next_run_within_window_episodes: int
    no_next_run_within_window_episodes: int
    later_next_run_outside_window_episodes: int
    next_run_outcome_pending_episodes: int
    resume_continuations_skipped: int
    open_attempt_episodes_excluded_from_pairing: int
    lifecycle_terminals_between_progression_and_next_attempt: int
    ambiguous_timestamp_events: int
    cross_content_previous_boundaries: int
    cross_content_next_boundaries: int
    cross_content_performance_pairs_excluded: int
    cross_release_pairs: int
    invalid_timing_rows: int


@dataclass(frozen=True, slots=True)
class ProgressionReportDefinitions:
    progression_observation_unit: str
    episode_observation_unit: str
    next_run_engagement_observation_unit: str
    paired_outcome_observation_unit: str
    paired_performance_primary_unit: str
    co_occurrence_observation_unit: str
    episode_boundary_semantics: str
    link_window_semantics: str
    next_run_observation_semantics: str
    no_next_run_within_window: str
    clear_rate: str
    progression_next_run_association_is_causal: bool = False
    paired_run_difference_is_causal: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProgressionActivityMetrics:
    progression_events: int
    progression_kinds: int
    unique_targets: int
    transaction_linked_events: int
    standalone_events: int
    top_progression_activity: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ProgressionEpisodeMetrics:
    bounded_episodes: int
    single_progression_episodes: int
    multi_progression_episodes: int
    top_episode_compositions: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class NextRunEngagementMetrics:
    mature_episodes: int
    right_censored_episodes: int
    next_run_within_window: MetricRatio
    same_stage_retry: MetricRatio
    time_to_next_run: DistributionSummary


@dataclass(frozen=True, slots=True)
class PairedOutcomeMetrics:
    paired_episodes: int
    previous_clear_rate: MetricRatio
    next_clear_rate: MetricRatio
    clear_rate_difference_pp: float | None
    top_outcome_transitions: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PairedElapsedMetrics:
    paired_count: int
    delta_mean_seconds: float | None
    delta_p25_seconds: float | None
    delta_median_seconds: float | None
    delta_p75_seconds: float | None


@dataclass(frozen=True, slots=True)
class ProgressionCoOccurrenceMetrics:
    pair_count: int
    top_pairs: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ProgressionNextRunMetrics:
    sample: ProgressionSampleSummary
    progression_activity: ProgressionActivityMetrics
    episodes: ProgressionEpisodeMetrics
    next_run_engagement: NextRunEngagementMetrics
    paired_outcome: PairedOutcomeMetrics
    paired_elapsed: PairedElapsedMetrics
    co_occurrence: ProgressionCoOccurrenceMetrics
    data_quality: ProgressionDataQuality


@dataclass(frozen=True, slots=True, kw_only=True)
class PostRunAnalysisScope:
    """Reproducible optional-stage scope for post-run behavior analysis."""

    environment: str
    content_version: int
    stage_key: str | None = None
    final_outcome: str | None = None
    app_version: str | None = None
    release_id: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    run_ended_at_utc_start: datetime | None = None
    run_ended_at_utc_end: datetime | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime
    post_run_max_gap_minutes: int = 30


@dataclass(frozen=True, slots=True)
class PostRunSampleSummary:
    anchor_final_runs: int
    unique_players: int
    clears: int
    deaths: int
    abandons: int
    unrecognized_outcomes: int
    linkage_eligible_windows: int
    mature_windows: int
    right_censored_windows: int
    shop_presented_windows: int
    shop_user_navigated_windows: int
    commerce_attempt_windows: int
    committed_success_windows: int
    progression_windows: int
    next_run_within_window_windows: int


@dataclass(frozen=True, slots=True)
class PostRunDataQuality:
    missing_player_identity_anchors: int
    missing_anchor_end_rows: int
    resolved_windows: int
    mature_no_next_windows: int
    later_next_run_outside_window_windows: int
    windows_with_observed_action: int
    windows_with_user_action: int
    windows_without_observed_action: int
    windows_without_lobby_activity_observed: int
    physical_lobby_rows: int
    deduped_lobby_rows: int
    physical_shop_rows: int
    deduped_shop_rows: int
    physical_transaction_rows: int
    deduped_transaction_rows: int
    physical_progression_rows: int
    deduped_progression_rows: int
    physical_iap_rows: int
    deduped_iap_rows: int
    cross_content_actions: int
    cross_release_actions: int
    offer_selection_without_exposure: int
    commerce_attempt_without_shop_selection: int
    transaction_attempt_without_result: int
    transaction_result_without_observed_attempt: int
    committed_success_without_observed_attempt_results: int
    committed_success_without_observed_attempt_windows: int
    non_commerce_system_rewards_excluded: int
    progression_transactions_excluded: int
    same_timestamp_action_groups: int
    unrecognized_action_rows: int
    next_outcome_pending_windows: int


@dataclass(frozen=True, slots=True)
class PostRunReportDefinitions:
    post_run_window_observation_unit: str
    shop_presentation_observation_unit: str
    shop_user_navigation_observation_unit: str
    observed_attempt_success_observation_unit: str
    committed_success_presence_observation_unit: str
    transition_observation_unit: str
    next_run_observation_semantics: str
    observed_attempt_success_rate: str
    committed_success_window_rate: str
    post_run_behavior_association_is_causal: bool = False
    programmatic_navigation_means_user_interest: bool = False
    no_next_run_within_window_means_churn: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FeedbackBehaviorMetrics:
    exposed_windows: int
    responded_windows: int
    positive_responses: int
    negative_responses: int
    positive_response_rate: MetricRatio
    top_cohorts: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PostRunWindowMetrics:
    linkage_eligible_windows: int
    mature_windows: int
    resolved_by_next_run_windows: int
    mature_no_next_windows: int
    right_censored_windows: int
    later_next_run_outside_window_windows: int


@dataclass(frozen=True, slots=True)
class NavigationMetrics:
    shop_presented_windows: int
    shop_presented_rate: MetricRatio
    shop_user_navigated_windows: int
    shop_user_navigated_rate: MetricRatio
    top_navigation: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ShopFunnelMetrics:
    offer_exposed_windows: int
    offer_selected_windows: int
    commerce_attempt_observed_windows: int
    observed_attempt_linked_succeeded_windows: int
    shop_presentation_to_exposure: MetricRatio
    exposure_to_selection: MetricRatio
    selection_to_observed_attempt: MetricRatio


@dataclass(frozen=True, slots=True)
class CommerceMetrics:
    observed_commerce_attempts: int
    linked_succeeded_attempts: int
    observed_attempt_success_rate: MetricRatio
    durable_committed_success_windows: int
    committed_success_window_rate: MetricRatio
    committed_success_without_observed_attempt_results: int
    committed_success_without_observed_attempt_windows: int
    top_categories: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PostRunProgressionMetrics:
    progression_windows: int
    progression_events: int
    top_progression: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PostRunNextRunMetrics:
    mature_windows: int
    right_censored_windows: int
    next_run_within_window: MetricRatio
    same_stage_retry: MetricRatio
    same_content_next_run: MetricRatio
    time_to_next_run: DistributionSummary


@dataclass(frozen=True, slots=True)
class ActionSequenceMetrics:
    windows_with_observed_action: int
    windows_with_user_action: int
    top_first_observed_actions: tuple[Mapping[str, Any], ...]
    top_first_user_actions: tuple[Mapping[str, Any], ...]
    top_transitions: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PostRunBehaviorMetrics:
    sample: PostRunSampleSummary
    window: PostRunWindowMetrics
    navigation: NavigationMetrics
    shop: ShopFunnelMetrics
    commerce: CommerceMetrics
    progression: PostRunProgressionMetrics
    next_run: PostRunNextRunMetrics
    action_sequence: ActionSequenceMetrics
    data_quality: PostRunDataQuality


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentVersionComparisonScope:
    """Ordered baseline/candidate scope for B-6 comparison reports."""

    environment: str
    baseline_content_version: int
    candidate_content_version: int
    stage_key: str | None = None
    domains: tuple[str, ...] = ()
    app_version: str | None = None
    release_channel: str | None = None
    release_type: str | None = None
    is_development_build: bool | None = None
    uploaded_at_utc_start: datetime | None = None
    uploaded_at_utc_end: datetime | None = None
    analysis_as_of_utc: datetime
    previous_run_max_gap_minutes: int = 30
    next_run_max_gap_minutes: int = 30
    post_run_max_gap_minutes: int = 30


@dataclass(frozen=True, slots=True)
class ScalarDelta:
    baseline: float | int | None
    candidate: float | int | None
    absolute_delta: float | None
    relative_delta: float | None


@dataclass(frozen=True, slots=True)
class RatioDelta:
    baseline: MetricRatio | None
    candidate: MetricRatio | None
    percentage_point_delta: float | None
    relative_delta: float | None


@dataclass(frozen=True, slots=True)
class DistributionDelta:
    baseline: DistributionSummary | None
    candidate: DistributionSummary | None
    mean_delta: float | None = None
    p10_delta: float | None = None
    p25_delta: float | None = None
    median_delta: float | None = None
    p75_delta: float | None = None
    p90_delta: float | None = None


@dataclass(frozen=True, slots=True)
class AvailabilityDelta:
    baseline_observed: bool
    candidate_observed: bool
    availability: str


@dataclass(frozen=True, slots=True)
class ComparisonMetricDefinition:
    metric: str
    value_type: str
    unit: str
    observation_unit: str
    definition: str


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    domain: str
    metric_family: str
    metric: str
    entity_type: str | None
    entity_key: str | None
    dimension: str | None
    dimension_value: str | None
    value_type: str
    unit: str
    observation_unit: str
    baseline_observed: bool
    candidate_observed: bool
    baseline_count: float | int | None = None
    baseline_denominator: float | int | None = None
    baseline_value: float | int | None = None
    candidate_count: float | int | None = None
    candidate_denominator: float | int | None = None
    candidate_value: float | int | None = None
    absolute_delta: float | None = None
    percentage_point_delta: float | None = None
    relative_delta: float | None = None
    direction: str = "Unavailable"
    status: str = "Unavailable"
    warning_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceAnalysisManifest:
    domain: str
    side: str
    analysis_type: str
    analysis_version: str
    report_contract_version: str
    scope: Mapping[str, Any]
    generated_at_utc: datetime
    snapshot_mode: str
    snapshot_cutoff_utc: datetime
    snapshot_guarantee: str
    sample: Mapping[str, Any]
    quality: Mapping[str, Any]
    definitions: Mapping[str, Any]
    warnings: tuple[Mapping[str, Any], ...]
    cohort_profile: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceAnalysisSnapshot:
    domain: str
    content_version: int
    manifest: SourceAnalysisManifest
    metrics: Mapping[str, Any]
    tables: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ContentVersionAnalysisSnapshot:
    content_version: int
    sources: Mapping[str, SourceAnalysisSnapshot]


@dataclass(frozen=True, slots=True)
class DomainComparison:
    domain: str
    status: str
    warning_codes: tuple[str, ...]
    baseline_sample: int
    candidate_sample: int
    rows: tuple[ComparisonRow, ...]
    top_changes: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ComparisonDataQuality:
    domains_requested: int
    domains_comparable: int
    domains_limited: int
    domains_unavailable: int
    domains_incompatible: int
    baseline_source_warning_count: int
    candidate_source_warning_count: int


@dataclass(frozen=True, slots=True)
class ContentVersionComparisonSample:
    baseline: Mapping[str, int]
    candidate: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ContentVersionComparisonDefinitions:
    comparison_direction: str = "candidateMinusBaseline"
    content_version_comparison_is_causal: bool = False
    count_changes_are_performance_changes: bool = False
    missing_means_zero: bool = False
    stage_snapshot_semantics: str = (
        "Stage Difficulty comparison snapshots use an uploadedAtUtc ingestion cutoff "
        "and do not provide a BigQuery historical system-time snapshot."
    )
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComparisonWarning:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContentVersionComparisonReportMetadata:
    report_contract_version: str
    analysis_type: str
    analysis_version: str
    generated_at_utc: datetime
    scope: ContentVersionComparisonScope
    sample: ContentVersionComparisonSample
    quality: ComparisonDataQuality
    definitions: ContentVersionComparisonDefinitions
    source_analyses: tuple[SourceAnalysisManifest, ...]
    warnings: tuple[ComparisonWarning, ...] = ()
    dry_run_estimated_bytes: int = 0

    def __post_init__(self) -> None:
        if self.report_contract_version != REPORT_CONTRACT_VERSION:
            raise ValueError(f"Unsupported report contract: {self.report_contract_version}")
        if self.analysis_type != "contentVersionCompare":
            raise ValueError("comparison metadata must use contentVersionCompare")
        if self.generated_at_utc.tzinfo is None or self.generated_at_utc.utcoffset() is None:
            raise ValueError("generated_at_utc must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ContentVersionComparisonMetrics:
    sample: ContentVersionComparisonSample
    stage_difficulty: DomainComparison | None
    weapon_performance: DomainComparison | None
    upgrade_choice: DomainComparison | None
    progression_next_run: DomainComparison | None
    post_run_behavior: DomainComparison | None
    comparison_data_quality: ComparisonDataQuality


@dataclass(frozen=True, slots=True)
class ContentVersionComparisonAnalysis:
    bundle: ReportBundle
    baseline_snapshot: ContentVersionAnalysisSnapshot
    candidate_snapshot: ContentVersionAnalysisSnapshot
    domain_comparisons: Mapping[str, DomainComparison]
    dry_run_estimated_bytes: int
    query_estimated_bytes: Mapping[str, int]


def utc_now_seconds() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
