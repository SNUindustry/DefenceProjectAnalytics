from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from defence_project_analytics.brief.loader import load_source_bundle
from defence_project_analytics.analysis_brief import (
    AnalysisBriefRequest,
    generate_analysis_brief,
)
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.bigquery_client import get_client, query_dataframe
from defence_project_analytics.models import QuerySpec
from defence_project_analytics.ga_identity_bridge import (
    RESTRICTED_OBSERVATION_COLUMNS,
    TABLE_SPECS,
    GaIdentityBridgeRequest,
    GaSourceTable,
    _write_restricted_artifact,
    assemble_bundle,
    assert_normal_bundle_privacy,
    build_query,
    materialize_intervals,
    resolve_temporal_profile,
    select_ga_source_tables,
)
from defence_project_analytics.metric_registry import (
    EvidenceUse,
    is_evidence_item_eligible,
    metric_authority,
)
from defence_project_analytics.reporting.writer import write_report_bundle


NOW = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
P1 = "1" * 32
P2 = "2" * 32
B1 = "a" * 32
B2 = "b" * 32


def request(**changes) -> GaIdentityBridgeRequest:
    values = {
        "telemetry_backend": "production",
        "environment": "Production",
        "ga_project": "bald-ops",
        "ga_property_id": "538301722",
        "ga_stream_id": "14908341790",
        "analysis_as_of_utc": NOW,
        "ga_date_start": date(2026, 9, 28),
        "ga_date_end": date(2026, 9, 29),
    }
    values.update(changes)
    return GaIdentityBridgeRequest(**values)


def row(
    minute: int,
    *,
    pseudo: str,
    player: str = P1,
    bridge: str = B1,
    occurrence: int = 1,
    status: str = "Mapped",
    physical: int = 1,
    duplicate: int = 0,
    kind: str = "Daily",
) -> dict[str, object]:
    return {
        "gaProject": "bald-ops",
        "gaPropertyId": "538301722",
        "gaStreamId": "14908341790",
        "telemetryBackend": "production",
        "environment": "Production",
        "observedAtUtc": NOW - timedelta(minutes=100 - minute),
        "telemetryPlayerId": player,
        "retentionBridgeId": bridge,
        "userPseudoId": pseudo,
        "lifecycleOccurrenceId": f"{occurrence:032x}",
        "mappingStatus": status,
        "sourceTableDate": date(2026, 9, 28),
        "sourceTableKind": kind,
        "sourceFinalizationState": "Final" if kind == "Daily" else "Provisional",
        "contentVersion": 129,
        "releaseId": "production-release",
        "isDevelopmentBuild": False,
        "physicalCount": physical,
        "exactDuplicateRowsRemoved": duplicate,
    }


def observations() -> pd.DataFrame:
    values = [
        row(0, pseudo="pseudo-a", occurrence=1),
        row(1, pseudo="pseudo-a", occurrence=2, physical=2, duplicate=1),
        row(2, pseudo="pseudo-a", player=P2, bridge=B2, occurrence=3),
        row(3, pseudo="pseudo-a", player=P2, bridge=B2, occurrence=4),
        row(4, pseudo="pseudo-a", occurrence=5),
        row(0, pseudo="pseudo-b", occurrence=6),
    ]
    for index, status in enumerate((
        "UnmappedMissingUserId", "UnmappedMissingPseudoId",
        "UnmappedMissingOccurrence", "UnmatchedGaOccurrence",
        "CustomConflict", "GaConflict", "TemporalMappingConflict",
        "DurableIdentityConflict", "InvalidEnvironment",
        "InvalidIdentifier", "InvalidContext",
    ), start=10):
        values.append(row(index, pseudo=f"quality-{index}", occurrence=index, status=status))
    return pd.DataFrame(values, columns=RESTRICTED_OBSERVATION_COLUMNS)


def test_source_selection_prefers_daily_and_never_unions_same_date() -> None:
    selected = select_ga_source_tables(
        {
            "events_20260928", "events_intraday_20260928",
            "events_intraday_20260929", "unrelated",
        },
        start=date(2026, 9, 28),
        end=date(2026, 9, 29),
    )
    assert [(item.table_id, item.kind, item.finalization_state) for item in selected] == [
        ("events_20260928", "Daily", "Final"),
        ("events_intraday_20260929", "Intraday", "Provisional"),
    ]


def test_test_backend_fails_closed_and_source_dimensions_are_explicit() -> None:
    with pytest.raises(ValueError, match="GA_SOURCE_UNAVAILABLE_FOR_TEST"):
        request(telemetry_backend="test", environment="Test")
    with pytest.raises(ValueError, match="GA_SOURCE_UNAVAILABLE_FOR_TEST"):
        request(telemetry_backend="production", environment="Test")


def test_query_uses_explicit_tables_exact_join_and_status_contract() -> None:
    selected = (
        GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),
        GaSourceTable(
            date(2026, 9, 29), "events_intraday_20260929",
            "Intraday", "Provisional",
        ),
    )
    query = build_query(
        request(), config=AnalyticsConfig.for_backend("production"),
        selected_tables=selected,
    )
    assert "`bald-ops.analytics_538301722.events_20260928`" in query.sql
    assert "`bald-ops.analytics_538301722.events_intraday_20260929`" in query.sql
    assert "events_*" not in query.sql
    assert "custom.lifecycleOccurrenceId = ga.lifecycleOccurrenceId" in query.sql
    assert "nearest" not in query.sql.casefold()
    for status in (
        "UnmappedMissingUserId", "UnmappedMissingPseudoId",
        "UnmappedMissingOccurrence", "UnmatchedGaOccurrence", "CustomConflict",
        "GaConflict", "TemporalMappingConflict", "DurableIdentityConflict",
        "InvalidEnvironment", "InvalidIdentifier", "InvalidContext",
    ):
        assert status in query.sql


def test_temporal_intervals_cover_repeat_switch_restore_and_reinstall() -> None:
    frame = observations()
    intervals = materialize_intervals(frame)
    pseudo_a = intervals[intervals["userPseudoId"] == "pseudo-a"]
    assert list(pseudo_a["observationCount"]) == [2, 2, 1]
    assert list(pseudo_a["telemetryPlayerId"]) == [P1, P2, P1]
    assert pseudo_a.iloc[0]["validUntilUtc"] == pseudo_a.iloc[1]["validFromUtc"]
    assert pseudo_a.iloc[1]["validUntilUtc"] == pseudo_a.iloc[2]["validFromUtc"]
    assert pd.isna(pseudo_a.iloc[2]["validUntilUtc"])
    pseudo_b = intervals[intervals["userPseudoId"] == "pseudo-b"]
    assert len(pseudo_b) == 1
    assert pseudo_b.iloc[0]["telemetryPlayerId"] == P1
    assert pseudo_b.iloc[0]["retentionBridgeId"] == B1

    assert resolve_temporal_profile(
        intervals,
        user_pseudo_id="pseudo-a",
        event_at_utc=pseudo_a.iloc[1]["validFromUtc"].to_pydatetime(),
    ) == "Mapped"
    assert resolve_temporal_profile(
        intervals, user_pseudo_id="absent", event_at_utc=NOW,
    ) == "Unmapped"


def test_bundle_metrics_invariants_statuses_and_privacy() -> None:
    frame = observations()
    intervals = materialize_intervals(frame)
    selected = (GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),)
    bundle = assemble_bundle(
        request(), frame, intervals, selected,
        estimated_bytes=123, generated_at_utc=NOW,
    )
    assert bundle.metrics["mapping"]["mappedCount"] == 6
    assert bundle.metrics["mapping"]["mappedRate"] == {
        "count": 6, "denominator": 17, "ratio": 6 / 17,
    }
    assert bundle.metrics["mapping"]["unmappedCount"] == 11
    assert bundle.metrics["mapping"]["profileSwitchObservationCount"] == 2
    assert bundle.metrics["mapping"]["multiProfilePseudoCount"] == 1
    assert bundle.metrics["dataQuality"]["physicalRows"] == 18
    assert bundle.metrics["dataQuality"]["exactDuplicateRowsRemoved"] == 1
    assert_normal_bundle_privacy(bundle, frame)
    rendered = render_bundle_text(bundle)
    for forbidden in (
        "telemetryPlayerId", "retentionBridgeId", "userPseudoId",
        "lifecycleOccurrenceId", P1, P2, B1, B2, "pseudo-a", "pseudo-b",
    ):
        assert forbidden not in rendered


def render_bundle_text(bundle) -> str:
    pieces = [json.dumps(bundle.metrics), json.dumps(to_jsonable(bundle.metadata)), bundle.markdown]
    pieces.extend(frame.to_csv(index=False) for frame in bundle.tables.values())
    return "\n".join(pieces)


def to_jsonable(value):
    from defence_project_analytics.reporting.renderers import to_external
    return to_external(value)


def test_restricted_artifact_manifest_and_normal_c1_bundle(tmp_path: Path) -> None:
    frame = observations()
    intervals = materialize_intervals(frame)
    restricted = _write_restricted_artifact(
        frame, intervals, request(), output_root=tmp_path / "restricted",
        overwrite=False,
    )
    manifest = json.loads((restricted / "manifest.json").read_text(encoding="utf-8"))
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((restricted / filename).read_bytes()).hexdigest() == digest
    assert P1 in (restricted / "mapping-observations.csv").read_text(encoding="utf-8")

    bundle = assemble_bundle(
        request(), frame, intervals,
        (GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),),
        estimated_bytes=0, generated_at_utc=NOW,
    )
    normal = write_report_bundle(
        bundle, output_root=tmp_path / "normal", table_specs=TABLE_SPECS,
        include_manifest=True,
    )
    loaded = load_source_bundle(normal, workspace_root=tmp_path)
    assert loaded.analysis_type == "gaIdentityBridge"
    assert loaded.domain == "gaIdentityBridge"
    brief = generate_analysis_brief(
        AnalysisBriefRequest("single-version", (normal,)),
        output_root=tmp_path / "brief", workspace_root=tmp_path,
    )
    brief_payload = json.loads((brief / "brief.json").read_text(encoding="utf-8"))
    evidence_payload = json.loads((brief / "evidence.json").read_text(encoding="utf-8"))
    assert brief_payload["scope"]["domains"] == ["gaIdentityBridge"]
    assert brief_payload["scope"]["contentVersion"] is None
    assert all(
        item["domain"] == "gaIdentityBridge"
        for item in evidence_payload["evidenceItems"]
    )


def test_metric_authority_is_factual_only() -> None:
    key = ("gaIdentityBridge", "mapping", "mappedRate")
    authority = metric_authority(key)
    assert authority.known_readable
    assert authority.evidence_eligible
    assert not authority.comparison_eligible
    assert not authority.decision_eligible
    assert not authority.target_eligible
    item = {"domain": key[0], "metricFamily": key[1], "metric": key[2]}
    assert is_evidence_item_eligible(item, EvidenceUse.FACTUAL_REFERENCE)
    assert not is_evidence_item_eligible(item, EvidenceUse.DECISION_SUPPORT)
    assert not is_evidence_item_eligible(item, EvidenceUse.TARGET_GUARDRAIL)


def test_same_timestamp_ambiguous_intervals_fail_closed() -> None:
    intervals = pd.DataFrame([
        {
            "userPseudoId": "pseudo",
            "validFromUtc": NOW,
            "validUntilUtc": None,
            "telemetryPlayerId": P1,
            "retentionBridgeId": B1,
        },
        {
            "userPseudoId": "pseudo",
            "validFromUtc": NOW,
            "validUntilUtc": None,
            "telemetryPlayerId": P2,
            "retentionBridgeId": B2,
        },
    ])
    assert resolve_temporal_profile(
        intervals, user_pseudo_id="pseudo", event_at_utc=NOW,
    ) == "Ambiguous"


def _sql_string(value: str | None) -> str:
    if value is None:
        return "CAST(NULL AS STRING)"
    return "'" + value.replace("'", "''") + "'"


def _ga_fixture_sql() -> str:
    p3, p4, b3 = "3" * 32, "4" * 32, "c" * 32
    rows = [
        # minute, user, pseudo, occurrence, environment, copies
        (11, B1, "pseudo-a", "01" * 16, "Production", 2),
        (12, B1, "pseudo-a", "02" * 16, "Production", 1),
        (13, B2, "pseudo-a", "03" * 16, "Production", 1),
        (14, B1, "pseudo-a", "04" * 16, "Production", 1),
        (11, B1, "pseudo-b", "05" * 16, "Production", 1),
        (20, None, "missing-user", "06" * 16, "Production", 1),
        (21, B1, None, "07" * 16, "Production", 1),
        (22, B1, "missing-occ", None, "Production", 1),
        (23, B1, "unmatched", "09" * 16, "Production", 1),
        (24, "INVALID", "invalid-user", "10" * 16, "Production", 1),
        (25, B1, "wrong-env", "11" * 16, "Test", 1),
        (26, B1, "custom-conflict", "12" * 16, "Production", 1),
        (27, B1, "ga-conflict", "13" * 16, "Production", 1),
        (27, B2, "ga-conflict", "13" * 16, "Production", 1),
        (28, B1, "bad-custom-id", "14" * 16, "Production", 1),
        (29, B1, "bad-context", "15" * 16, "Production", 1),
        (30, B1, "temporal", "16" * 16, "Production", 1),
        (30, B2, "temporal", "17" * 16, "Production", 1),
        (31, b3, "durable-1", "18" * 16, "Production", 1),
        (32, b3, "durable-2", "19" * 16, "Production", 1),
    ]
    structs = []
    for minute, user, pseudo, occurrence, environment, copies in rows:
        structs.append(
            "STRUCT("
            f"{minute} AS minute, {_sql_string(user)} AS userId, "
            f"{_sql_string(pseudo)} AS pseudoId, {_sql_string(occurrence)} AS occurrenceId, "
            f"'{environment}' AS environment, {copies} AS copies)"
        )
    value = (
        "STRUCT(paramValue AS string_value, CAST(NULL AS INT64) AS int_value, "
        "CAST(NULL AS FLOAT64) AS float_value, CAST(NULL AS FLOAT64) AS double_value)"
    )
    params = (
        "ARRAY(SELECT AS STRUCT key, " + value + " AS value FROM UNNEST(["
        "STRUCT('lifecycle_occurrence_id' AS key, x.occurrenceId AS paramValue),"
        "STRUCT('return_kind' AS key, 'ForegroundResume' AS paramValue),"
        "STRUCT('telemetry_environment' AS key, x.environment AS paramValue),"
        "STRUCT('content_version' AS key, '129' AS paramValue),"
        "STRUCT('release_id' AS key, 'r' AS paramValue)]))"
    )
    return (
        "SELECT DATE '2026-09-28' AS sourceTableDate, 'Daily' AS sourceTableKind, "
        "'Final' AS sourceFinalizationState, "
        "UNIX_MICROS(TIMESTAMP_ADD(TIMESTAMP '2026-09-28T11:10:05Z', "
        "INTERVAL x.minute MINUTE)) AS event_timestamp, 'app_foreground' AS event_name, "
        "x.userId AS user_id, x.pseudoId AS user_pseudo_id, "
        "'14908341790' AS stream_id, 'ANDROID' AS platform, "
        "STRUCT('1.0.16' AS version) AS app_info, " + params + " AS event_params "
        "FROM UNNEST([" + ",".join(structs) + "]) AS x "
        "CROSS JOIN UNNEST(GENERATE_ARRAY(1, x.copies)) AS copy"
    )


def _custom_fixture_sql() -> str:
    p3, p4 = "3" * 32, "4" * 32
    rows = [
        # occurrence, player, development, return kind, copies
        ("01" * 16, P1, False, "ForegroundResume", 2),
        ("02" * 16, P1, False, "ForegroundResume", 1),
        ("03" * 16, P2, False, "ForegroundResume", 1),
        ("04" * 16, P1, False, "ForegroundResume", 1),
        ("05" * 16, P1, False, "ForegroundResume", 1),
        ("06" * 16, P1, False, "ForegroundResume", 1),
        ("07" * 16, P1, False, "ForegroundResume", 1),
        ("10" * 16, P1, False, "ForegroundResume", 1),
        ("11" * 16, P1, False, "ForegroundResume", 1),
        ("12" * 16, P1, False, "ForegroundResume", 1),
        ("12" * 16, P2, False, "ForegroundResume", 1),
        ("13" * 16, P1, False, "ForegroundResume", 1),
        ("14" * 16, "INVALID", False, "ForegroundResume", 1),
        ("15" * 16, P1, True, "ForegroundResume", 1),
        ("16" * 16, P1, False, "ForegroundResume", 1),
        ("17" * 16, P2, False, "ForegroundResume", 1),
        ("18" * 16, p3, False, "ForegroundResume", 1),
        ("19" * 16, p4, False, "ForegroundResume", 1),
    ]
    structs = [
        "STRUCT("
        f"'{occ}' AS occurrenceId, '{player}' AS playerId, "
        f"{str(dev).upper()} AS development, '{kind}' AS returnKind, {copies} AS copies)"
        for occ, player, dev, kind, copies in rows
    ]
    return (
        "(SELECT 'Production' AS environment, x.occurrenceId AS lifecycleOccurrenceId, "
        "TIMESTAMP '2026-09-28T12:00:00Z' AS uploadedAtUtc, "
        "TIMESTAMP '2026-09-28T11:30:00Z' AS occurredAtUtc, "
        "x.playerId AS telemetryPlayerId, x.returnKind AS returnKind, "
        "129 AS contentVersion, 'r' AS releaseId, x.development AS isDevelopmentBuild, "
        "'CanonicalProfile' AS profileAttributionStatus "
        "FROM UNNEST([" + ",".join(structs) + "]) AS x "
        "CROSS JOIN UNNEST(GENERATE_ARRAY(1, x.copies)) AS copy)"
    )


@pytest.mark.skipif(
    os.getenv("DPA_RUN_BIGQUERY_FIXTURE") != "1",
    reason="explicit read-only BigQuery fixture run",
)
def test_bigquery_deterministic_identity_fixture() -> None:
    config = AnalyticsConfig.for_backend("production")
    query = build_query(
        request(), config=config,
        selected_tables=(
            GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),
        ),
    )
    sql = query.sql.replace(
        "SELECT *, DATE '2026-09-28' AS sourceTableDate, 'Daily' AS sourceTableKind, "
        "'Final' AS sourceFinalizationState FROM "
        "`bald-ops.analytics_538301722.events_20260928`",
        _ga_fixture_sql(),
    )
    sql = sql.replace(
        "`bald-ops.game_telemetry.telemetry_app_lifecycle_events`",
        _custom_fixture_sql(),
    )
    prefix = sql.rsplit("\nSELECT\n  @ga_project", 1)[0]
    aggregate_sql = prefix + "\nSELECT mappingStatus, COUNT(*) AS logicalRows, " \
        "SUM(physicalCount) AS physicalRows, " \
        "SUM(physicalCount - 1) AS duplicateRows " \
        "FROM classified GROUP BY mappingStatus ORDER BY mappingStatus"
    frame = query_dataframe(
        QuerySpec(aggregate_sql, query.parameters), client=get_client(config),
        config=config, maximum_bytes_billed=20_971_520,
    )
    counts = {row.mappingStatus: int(row.logicalRows) for row in frame.itertuples()}
    assert counts == {
        "CustomConflict": 1,
        "DurableIdentityConflict": 2,
        "GaConflict": 1,
        "InvalidContext": 1,
        "InvalidEnvironment": 1,
        "InvalidIdentifier": 2,
        "Mapped": 5,
        "TemporalMappingConflict": 2,
        "UnmappedMissingOccurrence": 1,
        "UnmappedMissingPseudoId": 1,
        "UnmappedMissingUserId": 1,
        "UnmatchedGaOccurrence": 1,
    }
    mapped = frame[frame["mappingStatus"] == "Mapped"].iloc[0]
    assert int(mapped["physicalRows"]) == 6
    assert int(mapped["duplicateRows"]) == 1
