"""Read-only analytics helpers for DefenceProject telemetry."""

from defence_project_analytics.bigquery_client import (
    dry_run_query,
    get_client,
    query_dataframe,
    table_exists,
    view_exists,
)
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.stage_overview import (
    StageOverviewRequest,
    calculate_stage_overview,
    generate_stage_overview_report,
    get_stage_overview,
)
from defence_project_analytics.stage_difficulty import (
    StageDifficultyAnalysis,
    StageDifficultyRequest,
    analyze_stage_difficulty,
    generate_stage_difficulty_report,
)
from defence_project_analytics.weapon_performance import (
    WeaponPerformanceAnalysis,
    WeaponPerformanceRequest,
    analyze_weapon_performance,
    generate_weapon_performance_report,
)
from defence_project_analytics.upgrade_choice import (
    UpgradeChoiceAnalysis,
    UpgradeChoiceRequest,
    analyze_upgrade_choice,
    generate_upgrade_choice_report,
)
from defence_project_analytics.progression_next_run import (
    ProgressionNextRunAnalysis,
    ProgressionNextRunRequest,
    analyze_progression_next_run,
    generate_progression_next_run_report,
)
from defence_project_analytics.post_run_behavior import (
    PostRunBehaviorAnalysis,
    PostRunBehaviorRequest,
    analyze_post_run_behavior,
    generate_post_run_behavior_report,
)
from defence_project_analytics.content_version_comparison import (
    ContentVersionCompareRequest,
    analyze_content_version_comparison,
    compare_content_version_snapshots,
    generate_content_version_comparison_report,
)
from defence_project_analytics.reporting.models import ContentVersionComparisonAnalysis

__all__ = [
    "AnalyticsConfig",
    "StageOverviewRequest",
    "StageDifficultyAnalysis",
    "StageDifficultyRequest",
    "analyze_stage_difficulty",
    "calculate_stage_overview",
    "dry_run_query",
    "get_client",
    "get_stage_overview",
    "generate_stage_difficulty_report",
    "generate_stage_overview_report",
    "WeaponPerformanceAnalysis",
    "WeaponPerformanceRequest",
    "analyze_weapon_performance",
    "generate_weapon_performance_report",
    "UpgradeChoiceAnalysis",
    "UpgradeChoiceRequest",
    "analyze_upgrade_choice",
    "generate_upgrade_choice_report",
    "ProgressionNextRunAnalysis",
    "ProgressionNextRunRequest",
    "analyze_progression_next_run",
    "generate_progression_next_run_report",
    "PostRunBehaviorAnalysis",
    "PostRunBehaviorRequest",
    "analyze_post_run_behavior",
    "generate_post_run_behavior_report",
    "ContentVersionComparisonAnalysis",
    "ContentVersionCompareRequest",
    "analyze_content_version_comparison",
    "compare_content_version_snapshots",
    "generate_content_version_comparison_report",
    "query_dataframe",
    "table_exists",
    "view_exists",
]
