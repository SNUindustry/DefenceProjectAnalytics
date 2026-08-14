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
    "query_dataframe",
    "table_exists",
    "view_exists",
]
