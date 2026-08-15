from datetime import datetime, timezone

from defence_project_analytics.models import QueryParameterValue
from defence_project_analytics.upgrade_choice import UpgradeChoiceRequest, build_upgrade_choice_queries


AS_OF = datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_upgrade_queries_bind_scope_as_of_and_remain_read_only() -> None:
    request = UpgradeChoiceRequest(
        "Production", "stage1", 42, app_version="1.2.3", release_id="release-a",
        is_development_build=False, analysis_as_of_utc=AS_OF,
    )
    queries = build_upgrade_choice_queries(request, analysis_as_of_utc=AS_OF)
    assert set(queries) == {"population", "exposure", "selection", "context", "headToHead", "outcome"}
    for query in queries.values():
        assert "uploadedAtUtc < @analysis_as_of_utc" in query.sql
        assert query.parameters["analysis_as_of_utc"] == QueryParameterValue(AS_OF, "TIMESTAMP")
        assert "SELECT *" not in query.sql.upper()
        for mutation in (" CREATE ", " DROP ", " UPDATE ", " DELETE ", " INSERT ", " MERGE "):
            assert mutation not in f" {query.sql.upper()} "


def test_selection_completeness_is_same_run_segment_local() -> None:
    sql = build_upgrade_choice_queries(
        UpgradeChoiceRequest("Test", "stage1", 2, analysis_as_of_utc=AS_OF),
        analysis_as_of_utc=AS_OF,
    )["population"].sql
    assert "run_end.upgradeSelectionCount = COALESCE(selection_quality.observedSelectionRows, 0)" in sql
    assert "selection_quality.runId = segments.runId" in sql
    assert "PARTITION BY selections.environment, selections.runId, selections.rowIndex" in sql
    assert "PARTITION BY environment, runId" in sql


def test_outcome_sql_has_mutually_exclusive_absent_composition() -> None:
    sql = build_upgrade_choice_queries(
        UpgradeChoiceRequest("Test", "stage1", 2, analysis_as_of_utc=AS_OF),
        analysis_as_of_utc=AS_OF,
    )["outcome"].sql
    assert "NOT events.selected AND events.alternativeSelected AS alternativeSelected" in sql
    assert "NOT events.selected AND NOT events.alternativeSelected AS noSelectionOnly" in sql
    assert "exposedNotSelectedAttempts = alternativeSelectedAttempts + noSelectionOnlyAttempts" in sql
    assert "fully_choice_covered_attempts" in sql
