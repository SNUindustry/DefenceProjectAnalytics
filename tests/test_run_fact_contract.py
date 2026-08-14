from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.stage_overview import (
    StageOverviewRequest,
    build_run_fact_query,
)


def test_run_fact_uses_canonical_attempts_and_required_joins() -> None:
    query = build_run_fact_query(
        StageOverviewRequest("Stage-1", "Production"), config=AnalyticsConfig()
    )
    sql = query.sql
    assert "telemetry_attempt_outcomes_v1" in sql
    assert "FROM `bald-ops.game_telemetry.telemetry_run_summary`" not in sql
    assert "feedback.environment = attempts.environment" in sql
    assert "feedback.runId = attempts.runId" in sql
    assert "upload_status.uploadId = attempts.uploadId" in sql
    for column in (
        "environment", "telemetryPlayerId", "attemptId", "runId", "stageKey",
        "gameplayOutcome", "segmentStartedAtUtc", "segmentEndedAtUtc",
        "attemptElapsedTimeSeconds", "appVersion", "contentVersion", "releaseId",
        "releaseChannel", "releaseType", "isDevelopmentBuild", "feedbackExposed",
        "feedbackResponse", "telemetryComplete",
    ):
        assert column in sql

