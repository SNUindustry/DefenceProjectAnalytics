from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from defence_project_analytics.analysis_execution import PreparedQueryBatch
from defence_project_analytics.content_version_comparison import (
    ContentVersionCompareRequest,
    analyze_content_version_comparison,
    build_content_version_comparison_queries,
)


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_builds_profile_plus_all_selected_source_queries() -> None:
    request = ContentVersionCompareRequest(
        "Test", 1, 4, stage_key="stage1", analysis_as_of_utc=NOW,
    )
    queries, plans = build_content_version_comparison_queries(
        request, analysis_as_of_utc=NOW,
    )
    assert len(queries) == 69
    assert len(plans) == 10
    assert "profile" in queries
    assert all("CREATE " not in query.sql.upper() and "UPDATE " not in query.sql.upper() for query in queries.values())


def test_global_cost_gate_stops_before_any_actual_query(monkeypatch) -> None:
    request = ContentVersionCompareRequest("Test", 1, 4, domains=("progression",), analysis_as_of_utc=NOW)
    monkeypatch.setattr(
        "defence_project_analytics.content_version_comparison.prepare_query_batch",
        lambda *args, **kwargs: PreparedQueryBatch({}, {"all": 101}),
    )
    executed: list[bool] = []
    monkeypatch.setattr(
        "defence_project_analytics.content_version_comparison.execute_prepared_query_batch",
        lambda *args, **kwargs: executed.append(True),
    )
    with pytest.raises(RuntimeError, match="no analysis query was executed"):
        analyze_content_version_comparison(request, maximum_total_bytes=100, clock=lambda: NOW)
    assert executed == []


def test_stage_uses_explicit_ingestion_snapshot_mode() -> None:
    request = ContentVersionCompareRequest("Test", 1, 4, stage_key="stage1", domains=("stage",), analysis_as_of_utc=NOW)
    _, plans = build_content_version_comparison_queries(request, analysis_as_of_utc=NOW)
    assert all(plan.request.uploaded_at_utc_end == NOW for plan in plans)
