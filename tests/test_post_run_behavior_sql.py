from datetime import datetime, timezone
import re

from defence_project_analytics.models import QueryParameterValue
from defence_project_analytics.post_run_behavior import (
    PostRunBehaviorRequest,
    build_post_run_behavior_queries,
)


AS_OF = datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_post_run_queries_are_read_only_aggregate_and_share_snapshot() -> None:
    request = PostRunBehaviorRequest(
        "Test", 4, stage_key="stage1", final_outcome="Dead", app_version="1.2",
        release_id="r", release_channel="test", release_type="candidate",
        is_development_build=False, analysis_as_of_utc=AS_OF,
        post_run_max_gap_minutes=30,
    )
    queries = build_post_run_behavior_queries(request, analysis_as_of_utc=AS_OF)
    assert len(queries) == 7
    for query in queries.values():
        assert "@include" not in query.sql
        assert not re.search(r"\bSELECT\s+(?:[A-Za-z_]+\.)?\*", query.sql, re.IGNORECASE)
        assert query.parameters["analysis_as_of_utc"] == QueryParameterValue(AS_OF, "TIMESTAMP")
        assert query.parameters["post_run_max_gap_minutes"] == QueryParameterValue(30, "INT64")
        upper = query.sql.upper()
        for mutation in ("CREATE ", "DELETE ", "DROP ", "UPDATE ", "INSERT ", "MERGE "):
            assert mutation not in upper


def test_window_and_shop_navigation_semantics_are_explicit() -> None:
    sql = build_post_run_behavior_queries(
        PostRunBehaviorRequest("Test", 4, analysis_as_of_utc=AS_OF)
    )["population"].sql
    assert "g.segmentIndex = 1" in sql
    assert "s.isFromResume IS NOT TRUE" in sql
    assert "n.segmentStartedAtUtc > a.segmentEndedAtUtc" in sql
    assert "l.occurredAtUtc >= w.segmentEndedAtUtc" in sql
    assert "l.occurredAtUtc < w.windowEndedAtUtc" in sql
    assert "eventKind = 'TabViewed' AND tab = 'Shop' AND navigationSource = 'User'" in sql
    assert "ShopSectionViewed" in sql


def test_commerce_attempt_and_durable_result_denominators_are_separate() -> None:
    sql = build_post_run_behavior_queries(
        PostRunBehaviorRequest("Test", 4, analysis_as_of_utc=AS_OF)
    )["shopCommerce"].sql
    assert "observedAttemptSuccessRate" in sql
    assert "committedSuccessWindowRate" in sql
    assert "resultCategory = 'Succeeded' AND NOT hasObservedAttempt" in sql
    assert "FunFeedbackReward" in sql
    assert "ProgressionSpend" in sql
