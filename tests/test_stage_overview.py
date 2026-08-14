import pandas as pd
import pytest

from defence_project_analytics.models import QueryParameterValue
from defence_project_analytics.stage_overview import (
    StageOverviewRequest,
    build_stage_overview_query,
    calculate_stage_overview,
    get_stage_overview,
)


def test_clear_rate_excludes_abandons_from_denominator() -> None:
    frame = pd.DataFrame(
        {
            "gameplayOutcome": ["Clear", "Dead", "Abandon", "Abandon"],
            "telemetryPlayerId": ["p1", "p2", "p1", None],
            "attemptElapsedTimeSeconds": [10.0, 20.0, 30.0, None],
            "feedbackExposed": [True, True, False, None],
            "feedbackResponse": ["Positive", "Negative", None, None],
            "telemetryComplete": [True, False, None, None],
        }
    )
    result = calculate_stage_overview(frame)
    assert result["final_attempts"] == 4
    assert result["clears"] == 1
    assert result["deaths"] == 1
    assert result["abandons"] == 2
    assert result["clear_rate"] == pytest.approx(0.5)
    assert result["unique_telemetry_players"] == 2


def test_nullable_legacy_rows_do_not_depress_completeness_rate() -> None:
    frame = pd.DataFrame(
        {
            "telemetryComplete": pd.Series([True, False, None], dtype="boolean"),
            "feedbackExposed": pd.Series([True, None, False], dtype="boolean"),
            "feedbackResponse": ["Positive", None, "UnexpectedLegacyValue"],
            "attemptElapsedTimeSeconds": [10.0, None, 30.0],
        }
    )
    result = calculate_stage_overview(frame)
    assert result["telemetry_complete_assessed_attempts"] == 2
    assert result["telemetry_complete_rate"] == pytest.approx(0.5)
    assert result["feedback_exposure_count"] == 1
    assert result["positive_response_rate"] == pytest.approx(1.0)
    assert result["median_attempt_elapsed_seconds"] == pytest.approx(20.0)


@pytest.mark.parametrize("environment", ["Production", "Test"])
def test_environment_is_a_bound_filter(environment: str) -> None:
    query = build_stage_overview_query(StageOverviewRequest("Stage-1", environment))
    assert "attempts.environment = @environment" in query.sql
    assert query.parameters["environment"] == environment
    assert environment not in query.sql


def test_content_version_is_optional_typed_parameter() -> None:
    omitted = build_stage_overview_query(StageOverviewRequest("Stage-1", "Test"))
    selected = build_stage_overview_query(StageOverviewRequest("Stage-1", "Test", 42))
    assert "@content_version IS NULL" in omitted.sql
    assert omitted.parameters["content_version"] == QueryParameterValue(None, "INT64")
    assert selected.parameters["content_version"] == QueryParameterValue(42, "INT64")


def test_live_aggregate_count_fields_are_serialized_as_integers(monkeypatch) -> None:
    aggregate = pd.DataFrame(
        [{"final_attempts": 13.0, "clears": 1.0, "clear_rate": 0.125}]
    )
    monkeypatch.setattr(
        "defence_project_analytics.stage_overview.query_dataframe",
        lambda *args, **kwargs: aggregate,
    )
    result = get_stage_overview(StageOverviewRequest("stage1", "Test"))
    assert result["final_attempts"] == 13
    assert isinstance(result["final_attempts"], int)
    assert result["clears"] == 1
    assert result["clear_rate"] == pytest.approx(0.125)
