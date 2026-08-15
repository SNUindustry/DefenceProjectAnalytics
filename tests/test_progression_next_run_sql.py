from datetime import datetime, timezone
import re

from defence_project_analytics.models import QueryParameterValue
from defence_project_analytics.progression_next_run import (
    ProgressionNextRunRequest,
    build_progression_next_run_queries,
)


AS_OF = datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_queries_are_read_only_aggregate_and_share_all_parameters() -> None:
    request = ProgressionNextRunRequest(
        "Test", 4, progression_kind="WeaponRecipe", previous_stage_key="stage1",
        next_stage_key="stage1", app_version="1.2", release_id="r",
        release_channel="test", release_type="candidate", is_development_build=False,
        analysis_as_of_utc=AS_OF, previous_run_max_gap_minutes=5,
        next_run_max_gap_minutes=60,
    )
    queries = build_progression_next_run_queries(request, analysis_as_of_utc=AS_OF)
    assert len(queries) == 7
    for query in queries.values():
        assert "@include" not in query.sql
        assert not re.search(r"\bSELECT\s+(?:[A-Za-z_]+\.)?\*", query.sql, re.IGNORECASE)
        assert query.parameters["content_version"] == QueryParameterValue(4, "INT64")
        assert query.parameters["analysis_as_of_utc"] == QueryParameterValue(AS_OF, "TIMESTAMP")
        assert query.parameters["previous_run_max_gap_minutes"] == QueryParameterValue(5, "INT64")
        assert query.parameters["next_run_max_gap_minutes"] == QueryParameterValue(60, "INT64")
        upper = query.sql.upper()
        for mutation in ("CREATE ", "DELETE ", "DROP ", "UPDATE ", "INSERT ", "MERGE "):
            assert mutation not in upper


def test_structural_boundaries_are_resolved_before_link_windows() -> None:
    sql = build_progression_next_run_queries(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=AS_OF)
    )["population"].sql
    previous_cte = sql.split("previous_candidates AS (", 1)[1].split("next_candidates AS (", 1)[0]
    next_cte = sql.split("next_candidates AS (", 1)[1].split("event_boundaries_base AS (", 1)[0]
    assert "f.segmentEndedAtUtc < p.occurredAtUtc" in previous_cte
    assert "n.segmentStartedAtUtc > p.occurredAtUtc" in next_cte
    assert "max_gap" not in previous_cte.casefold()
    assert "max_gap" not in next_cte.casefold()
    assert "g.segmentIndex = 1" in sql
    assert "s.isFromResume IS NOT TRUE" in sql
    assert "episodeKey IS NULL" in sql
    assert "WHERE isScoped AND episodeKey IS NOT NULL" in sql
    assert "AND NOT crossReleasePair" in sql
    assert "primarySameStageSameContentSingleProgression" in build_progression_next_run_queries(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=AS_OF)
    )["paired"].sql
    assert "secondaryStageChangedSameContent" in build_progression_next_run_queries(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=AS_OF)
    )["paired"].sql
    assert "secondaryCrossReleaseSameContent" in build_progression_next_run_queries(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=AS_OF)
    )["paired"].sql


def test_uploaded_range_is_primary_scope_only_and_as_of_supports_all_sources() -> None:
    sql = build_progression_next_run_queries(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=AS_OF)
    )["population"].sql
    assert sql.count("@uploaded_start_utc") == 2
    assert sql.count("@uploaded_end_utc") == 2
    assert sql.count("uploadedAtUtc < @analysis_as_of_utc") >= 5
    assert "nextRunObservationSemantics" not in sql
