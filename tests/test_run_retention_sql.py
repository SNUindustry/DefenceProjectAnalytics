from datetime import datetime, timezone
import re

from defence_project_analytics.models import QueryParameterValue
from defence_project_analytics.run_retention import RunRetentionRequest, build_run_retention_queries


AS_OF = datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_queries_are_read_only_aggregate_and_share_snapshot() -> None:
    request = RunRetentionRequest(
        "Test", 1, stage_key="stage1", analysis_as_of_utc=AS_OF,
        long_term_no_next_run_threshold_days=7, source_upload_grace_hours=24,
    )
    queries = build_run_retention_queries(request, analysis_as_of_utc=AS_OF)
    assert set(queries) == {"population", "cohorts", "nextContext"}
    for query in queries.values():
        assert "@include" not in query.sql
        assert not re.search(r"\bSELECT\s+(?:[A-Za-z_]+\.)?\*", query.sql, re.IGNORECASE)
        assert query.parameters["analysis_as_of_utc"] == QueryParameterValue(AS_OF, "TIMESTAMP")
        assert query.parameters["long_term_threshold_days"] == QueryParameterValue(7, "INT64")
        assert query.parameters["source_upload_grace_hours"] == QueryParameterValue(24, "INT64")
        for mutation in ("CREATE ", "DELETE ", "DROP ", "UPDATE ", "INSERT ", "MERGE "):
            assert mutation not in query.sql.upper()


def test_structural_next_attempt_and_threshold_truth_table_are_explicit() -> None:
    queries = build_run_retention_queries(
        RunRetentionRequest("Test", 1, analysis_as_of_utc=AS_OF),
        analysis_as_of_utc=AS_OF,
    )
    sql = queries["population"].sql
    assert "g.segmentIndex = 1" in sql
    assert "s.isFromResume IS NOT TRUE" in sql
    assert "n.segmentStartedAtUtc > a.segmentEndedAtUtc" in sql
    assert "ORDER BY n.segmentStartedAtUtc, n.attemptId, n.runId" in sql
    assert "ReturnedWithinThreshold" in sql
    assert "ReturnedAfterThreshold" in sql
    assert "LongTermNoNextRun" in sql
    assert "RightCensored" in sql
    assert "COUNTIF(thresholdState IN ('ReturnedAfterThreshold', 'LongTermNoNextRun'))" in sql


def test_anchor_filters_follow_as_of_aware_canonical_dedupe() -> None:
    sql = build_run_retention_queries(
        RunRetentionRequest("Test", 1, analysis_as_of_utc=AS_OF),
        analysis_as_of_utc=AS_OF,
    )["population"].sql
    assert sql.index("uploadedAtUtc < @analysis_as_of_utc") < sql.index("anchor_canonical AS")
    assert "ORDER BY segmentIndex DESC, segmentEndedAtUtc DESC, uploadedAtUtc DESC, runId DESC" in sql
    assert "(@uploaded_start_utc IS NULL OR a.uploadedAtUtc >= @uploaded_start_utc)" in sql
    assert "new_attempt_candidate_physical" in sql
