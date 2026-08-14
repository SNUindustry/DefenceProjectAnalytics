from datetime import datetime, timezone

from defence_project_analytics.models import QueryParameterValue
from defence_project_analytics.stage_difficulty import StageDifficultyRequest, build_stage_difficulty_queries


def test_all_queries_are_read_only_and_bind_the_full_scope() -> None:
    request = StageDifficultyRequest(
        "Production", "stage1", 0, app_version="1.2", release_id="r", release_channel="beta",
        release_type="candidate", is_development_build=False,
        uploaded_at_utc_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        uploaded_at_utc_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    queries = build_stage_difficulty_queries(request)
    assert len(queries) == 7
    for query in queries.values():
        assert query.sql.lstrip().startswith("--")
        assert "@environment" in query.sql and "@stage_key" in query.sql and "@content_version" in query.sql
        assert "CREATE " not in query.sql.upper() and "DELETE " not in query.sql.upper()
        assert query.parameters["environment"] == "Production"
        assert query.parameters["content_version"] == QueryParameterValue(0, "INT64")
        assert query.parameters["is_development_build"] == QueryParameterValue(False, "BOOL")


def test_resume_and_detail_contract_is_explicit_in_sql() -> None:
    queries = build_stage_difficulty_queries(StageDifficultyRequest("Test", "stage1", 2))
    summary = queries["summary"].sql
    assert "IS NOT DISTINCT FROM" in summary
    assert "PARTITION BY segments.environment, segments.runId" in summary
    assert "telemetryComplete IS TRUE" in summary
    assert "mixed_content_detail_rows" in summary
    assert "telemetry_attempt_outcomes_v1" in summary
    assert "COUNT(*) AS final_attempts" in summary
    assert "lethalHitCount" in queries["incoming_damage"].sql
    assert "finalDeathEnemyDefinitionId" in queries["death_causes"].sql


def test_fixed_bucket_boundaries_and_snapshot_priority() -> None:
    queries = build_stage_difficulty_queries(StageDifficultyRequest("Test", "stage1", 2))
    timing = queries["death_timing"].sql
    for label in ("0-30s", "30-60s", "1-2m", "2-5m", "5-10m", "10m+"):
        assert label in timing
    state = queries["death_state"].sql
    assert "s.reason = 'run_end'" in state
    assert "s.elapsedTime DESC, s.snapshotIndex DESC, s.rowIndex DESC" in state
