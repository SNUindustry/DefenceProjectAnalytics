from datetime import datetime, timezone

from defence_project_analytics.models import QueryParameterValue
from defence_project_analytics.weapon_performance import (
    WeaponPerformanceRequest,
    build_weapon_performance_queries,
)


def test_all_weapon_queries_are_composed_read_only_and_share_as_of() -> None:
    as_of = datetime(2026, 8, 14, tzinfo=timezone.utc)
    request = WeaponPerformanceRequest(
        "Production", "stage1", 0, app_version="1.2", release_id="r",
        release_channel="beta", release_type="candidate", is_development_build=False,
        analysis_as_of_utc=as_of,
    )
    queries = build_weapon_performance_queries(request, analysis_as_of_utc=as_of)
    assert len(queries) == 7
    for query in queries.values():
        assert "@include" not in query.sql
        assert "uploadedAtUtc < @analysis_as_of_utc" in query.sql
        assert query.parameters["content_version"] == QueryParameterValue(0, "INT64")
        assert query.parameters["analysis_as_of_utc"] == QueryParameterValue(as_of, "TIMESTAMP")
        upper = query.sql.upper()
        for mutation in ("CREATE ", "DELETE ", "DROP ", "UPDATE ", "INSERT ", "MERGE "):
            assert mutation not in upper


def test_resume_completeness_and_metric_contract_are_explicit() -> None:
    queries = build_weapon_performance_queries(
        WeaponPerformanceRequest("Test", "stage1", 2, analysis_as_of_utc=datetime(2026, 8, 14, tzinfo=timezone.utc))
    )
    population = queries["population"].sql
    assert "IS NOT DISTINCT FROM" in population
    assert "eligibleSegments = candidateSegments" in population
    assert "telemetryComplete IS TRUE" in population
    assert "mixedContentSegmentsExcluded" in population
    combat = queries["combat"].sql
    assert "attempt_denominators" in combat
    assert "AVG(damageShare)" in combat
    assert "combatObservedSegments" in combat
    assert "combatObservedRuns" not in combat
    dps = queries["dps"].sql
    assert "sampleValid IS TRUE AND duration > 0" in dps
    assert "dpsCoverageAmongCombatObservedInstanceSegments" in dps
    assert "BossStarted" in queries["boss"].sql
    assert "Clear', 'Dead" in queries["outcome"].sql

