from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from defence_project_analytics.analysis_brief import (
    AnalysisBriefRequest,
    build_analysis_brief,
)
from defence_project_analytics.brief.loader import load_source_bundle
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.bigquery_client import get_client, query_dataframe
from defence_project_analytics.ga_identity_bridge import GaSourceTable, select_ga_source_tables
from defence_project_analytics.metric_registry import metric_authority
from defence_project_analytics.observed_uninstall import (
    APP_REMOVE_COLUMNS,
    ATTRIBUTED_EVENT_COLUMNS,
    TABLE_SPECS,
    ObservedUninstallRequest,
    _write_restricted_artifact,
    assemble_bundle,
    assert_normal_bundle_privacy,
    attribute_app_remove_events,
    build_app_remove_query,
)
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.reporting.renderers import render_json
from defence_project_analytics.models import QuerySpec


UTC = timezone.utc
T0 = datetime(2026, 9, 28, 10, tzinfo=UTC)
HEX = {
    "p1": "1" * 32,
    "p2": "2" * 32,
    "bridge1": "a" * 32,
    "bridge2": "b" * 32,
}


def request(**overrides: object) -> ObservedUninstallRequest:
    values = {
        "telemetry_backend": "production",
        "environment": "Production",
        "ga_project": "bald-ops",
        "ga_property_id": "538301722",
        "ga_stream_id": "14908341790",
        "analysis_as_of_utc": datetime(2026, 9, 30, tzinfo=UTC),
        "ga_date_start": date(2026, 9, 28),
        "ga_date_end": date(2026, 9, 29),
    }
    values.update(overrides)
    return ObservedUninstallRequest(**values)


def removal(
    when: datetime | None,
    pseudo: str | None,
    *,
    raw_status: str = "Valid",
    physical: int = 1,
    variants: int = 1,
    bundle: int = 1,
) -> dict[str, object]:
    return {
        "gaProject": "bald-ops",
        "gaPropertyId": "538301722",
        "gaStreamId": "14908341790",
        "telemetryBackend": "production",
        "environment": "Production",
        "eventTimestampUtc": when,
        "userPseudoId": pseudo,
        "gaUserId": None,
        "rawEventStatus": raw_status,
        "sourceTableDate": date(2026, 9, 28),
        "sourceTableKind": "Daily",
        "sourceFinalizationState": "Final",
        "eventBundleSequenceId": bundle,
        "batchEventIndex": 0,
        "batchOrderingId": 0,
        "physicalCount": physical,
        "variantCount": variants,
        "exactDuplicateRowsRemoved": physical - variants,
    }


def bridge(
    when: datetime,
    pseudo: str,
    player: str = HEX["p1"],
    bridge_id: str = HEX["bridge1"],
    status: str = "Mapped",
) -> dict[str, object]:
    return {
        "observedAtUtc": when,
        "userPseudoId": pseudo,
        "telemetryPlayerId": player,
        "retentionBridgeId": bridge_id,
        "mappingStatus": status,
    }


def test_request_fails_closed_for_test_ga() -> None:
    with pytest.raises(ValueError, match="GA_SOURCE_UNAVAILABLE_FOR_TEST"):
        request(telemetry_backend="test", environment="Test")


def test_daily_replaces_same_date_intraday_and_intraday_remains_provisional() -> None:
    selected = select_ga_source_tables(
        {
            "events_intraday_20260928", "events_20260928",
            "events_intraday_20260929",
        },
        start=date(2026, 9, 28), end=date(2026, 9, 29),
    )
    assert [(item.table_id, item.finalization_state) for item in selected] == [
        ("events_20260928", "Final"),
        ("events_intraday_20260929", "Provisional"),
    ]


def test_sql_uses_bounded_explicit_source_and_stable_event_key() -> None:
    query = build_app_remove_query(
        request(),
        config=AnalyticsConfig.for_backend("production"),
        selected_tables=(
            GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),
            GaSourceTable(
                date(2026, 9, 29), "events_intraday_20260929",
                "Intraday", "Provisional",
            ),
        ),
    )
    assert "`bald-ops.analytics_538301722.events_20260928`" in query.sql
    assert "`bald-ops.analytics_538301722.events_intraday_20260929`" in query.sql
    assert "events_*" not in query.sql
    assert "event_bundle_sequence_id" in query.sql
    assert "batch_event_index" in query.sql
    assert "batch_ordering_id" in query.sql
    assert "event_name = 'app_remove'" in query.sql
    assert "GaDuplicateConflict" in query.sql


def test_latest_prior_account_switch_restore_and_timestamp_boundary() -> None:
    observations = pd.DataFrame([
        bridge(T0, "A"),
        bridge(T0 + timedelta(hours=1), "A", HEX["p2"], HEX["bridge2"]),
        bridge(T0 + timedelta(hours=2), "A", HEX["p1"], HEX["bridge1"]),
        bridge(T0 + timedelta(hours=5), "F"),
        bridge(T0 + timedelta(hours=3), "E"),
    ])
    raw = pd.DataFrame([
        removal(T0 + timedelta(minutes=30), "A", bundle=1),
        removal(T0 + timedelta(hours=1, minutes=30), "A", bundle=2),
        removal(T0 + timedelta(hours=3), "A", bundle=3),
        removal(T0 + timedelta(hours=4), "F", bundle=4),
        removal(T0 + timedelta(hours=4), "N", bundle=5),
        removal(T0 + timedelta(hours=3), "E", bundle=6),
    ], columns=APP_REMOVE_COLUMNS)
    result = attribute_app_remove_events(raw, observations)
    assert list(result["attributionStatus"]) == [
        "Mapped", "Mapped", "Mapped", "Unmapped", "Unmapped", "Mapped",
    ]
    assert list(result["telemetryPlayerId"].iloc[:3]) == [
        HEX["p1"], HEX["p2"], HEX["p1"],
    ]
    assert list(result["attributionReason"].iloc[3:5]) == [
        "UnmappedNoPriorMapping", "UnmappedNoPriorMapping",
    ]
    assert result.iloc[5]["mappingAgeSeconds"] == 0.0


def test_latest_conflict_is_ambiguous_without_old_clean_fallback() -> None:
    observations = pd.DataFrame([
        bridge(T0, "A"),
        bridge(
            T0 + timedelta(hours=1), "A", HEX["p1"], HEX["bridge1"],
            "TemporalMappingConflict",
        ),
        bridge(
            T0 + timedelta(hours=1), "A", HEX["p2"], HEX["bridge2"],
            "TemporalMappingConflict",
        ),
    ])
    raw = pd.DataFrame(
        [removal(T0 + timedelta(hours=2), "A")], columns=APP_REMOVE_COLUMNS
    )
    result = attribute_app_remove_events(raw, observations)
    assert result.iloc[0]["attributionStatus"] == "Ambiguous"
    assert result.iloc[0]["attributionReason"] == "TemporalMappingConflict"
    assert pd.isna(result.iloc[0]["telemetryPlayerId"])


def test_missing_pseudo_duplicate_conflict_and_invalid_event_fail_closed() -> None:
    raw = pd.DataFrame([
        removal(T0, None, raw_status="UnmappedMissingPseudoId", bundle=1),
        removal(
            T0, "A", raw_status="GaDuplicateConflict",
            physical=2, variants=2, bundle=2,
        ),
        removal(None, "B", raw_status="InvalidGaEvent", bundle=3),
    ], columns=APP_REMOVE_COLUMNS)
    observations = pd.DataFrame(
        [bridge(T0 - timedelta(seconds=1), "A")]
    )
    result = attribute_app_remove_events(raw, observations)
    assert list(result["attributionStatus"]) == ["Unmapped", "Ambiguous", "Invalid"]
    assert list(result["attributionReason"]) == [
        "UnmappedMissingPseudoId", "GaDuplicateConflict", "InvalidGaEvent",
    ]


def test_reinstall_repeated_uninstall_and_old_mapping_remain_factual() -> None:
    observations = pd.DataFrame([
        bridge(T0 - timedelta(days=60), "A"),
        bridge(T0 + timedelta(days=1), "B"),
    ])
    raw = pd.DataFrame([
        removal(T0, "A", bundle=1),
        removal(T0 + timedelta(days=2), "B", bundle=2),
    ], columns=APP_REMOVE_COLUMNS)
    result = attribute_app_remove_events(raw, observations)
    assert list(result["attributionStatus"]) == ["Mapped", "Mapped"]
    assert result.iloc[0]["mappingAgeSeconds"] == 60 * 24 * 60 * 60
    assert result.iloc[1]["mappingAgeSeconds"] == 24 * 60 * 60
    assert result["telemetryPlayerId"].nunique() == 1


def test_bundle_metrics_empty_population_and_duplicate_invariants() -> None:
    req = request()
    selected = (
        GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),
    )
    empty = pd.DataFrame(columns=ATTRIBUTED_EVENT_COLUMNS)
    bundle = assemble_bundle(
        req, empty, selected, estimated_bytes=10, generated_at_utc=T0
    )
    assert bundle.metrics["source"]["appRemoveEvents"] == 0
    assert bundle.metrics["attribution"]["mappedRate"]["ratio"] is None
    assert bundle.metrics["mappingAge"]["p50Seconds"] is None
    assert [item.code for item in bundle.metadata.warnings] == [
        "NO_OBSERVED_APP_REMOVE"
    ]

    attributed = attribute_app_remove_events(
        pd.DataFrame([
            removal(T0 + timedelta(seconds=5), "A", physical=2, variants=1),
        ], columns=APP_REMOVE_COLUMNS),
        pd.DataFrame([bridge(T0, "A")]),
    )
    bundle = assemble_bundle(
        req, attributed, selected, estimated_bytes=10, generated_at_utc=T0
    )
    assert bundle.metrics["dataQuality"]["physicalRows"] == 2
    assert bundle.metrics["dataQuality"]["exactDuplicateRowsRemoved"] == 1
    assert bundle.metrics["mappingAge"]["p50Seconds"] == 5.0


def test_restricted_artifact_manifest_normal_privacy_and_c1(tmp_path: Path) -> None:
    req = request()
    selected = (
        GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),
    )
    pseudo = "pseudo-sensitive-123"
    bridge_frame = pd.DataFrame([bridge(T0, pseudo)])
    events = attribute_app_remove_events(
        pd.DataFrame([
            removal(T0 + timedelta(seconds=5), pseudo)
        ], columns=APP_REMOVE_COLUMNS),
        bridge_frame,
    )
    bundle = assemble_bundle(
        req, events, selected, estimated_bytes=10, generated_at_utc=T0
    )
    assert_normal_bundle_privacy(bundle, events, bridge_frame)
    restricted = _write_restricted_artifact(
        events, req, output_root=tmp_path / "restricted", overwrite=False
    )
    normal = write_report_bundle(
        bundle,
        output_root=tmp_path / "generated",
        table_specs=TABLE_SPECS,
        include_manifest=True,
    )
    for root in (restricted, normal):
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest["files"].items():
            assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
    normal_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in normal.rglob("*") if path.is_file()
    )
    for raw in (
        "userPseudoId", "telemetryPlayerId", "retentionBridgeId",
        "gaUserId", HEX["p1"], HEX["bridge1"],
    ):
        assert raw not in normal_text
    loaded = load_source_bundle(normal)
    assert loaded.analysis_type == "observedUninstall"
    brief = build_analysis_brief(AnalysisBriefRequest("single-version", (normal,)))
    assert brief.scope.domains == ("observedUninstall",)
    assert any(
        item.metric == "mappedRate" and item.domain == "observedUninstall"
        for item in brief.evidence
    )
    brief_text = render_json(brief.brief_payload)
    assert HEX["p1"] not in brief_text
    assert HEX["bridge1"] not in brief_text


def test_metric_authority_is_factual_only() -> None:
    key = ("observedUninstall", "attribution", "mappedRate")
    authority = metric_authority(key)
    assert authority.known_readable
    assert authority.evidence_eligible
    assert not authority.comparison_eligible
    assert not authority.decision_eligible
    assert not authority.target_eligible


def _app_remove_fixture_sql() -> str:
    empty_params = (
        "ARRAY<STRUCT<key STRING, value STRUCT<string_value STRING, int_value INT64, "
        "float_value FLOAT64, double_value FLOAT64>>>[]"
    )
    rows = (
        "STRUCT(15 AS minute, 'pseudo-valid' AS pseudo, 'u1' AS uid, 1 AS bundle, "
        "0 AS batchIndex, 0 AS ordering, 'ANDROID' AS platform, 2 AS copies),"
        "STRUCT(16 AS minute, 'pseudo-conflict' AS pseudo, 'u1' AS uid, 2 AS bundle, "
        "0 AS batchIndex, 0 AS ordering, 'ANDROID' AS platform, 1 AS copies),"
        "STRUCT(16 AS minute, 'pseudo-conflict' AS pseudo, 'u2' AS uid, 2 AS bundle, "
        "0 AS batchIndex, 0 AS ordering, 'ANDROID' AS platform, 1 AS copies),"
        "STRUCT(17 AS minute, CAST(NULL AS STRING) AS pseudo, CAST(NULL AS STRING) AS uid, "
        "3 AS bundle, 0 AS batchIndex, 0 AS ordering, 'ANDROID' AS platform, 1 AS copies),"
        "STRUCT(18 AS minute, 'pseudo-invalid' AS pseudo, CAST(NULL AS STRING) AS uid, "
        "4 AS bundle, 0 AS batchIndex, 0 AS ordering, 'WEB' AS platform, 1 AS copies)"
    )
    return (
        "SELECT DATE '2026-09-28' AS sourceTableDate, 'Daily' AS sourceTableKind, "
        "'Final' AS sourceFinalizationState, "
        "'20260928' AS event_date, "
        "UNIX_MICROS(TIMESTAMP_ADD(TIMESTAMP '2026-09-28T11:10:05Z', "
        "INTERVAL x.minute MINUTE)) AS event_timestamp, 'app_remove' AS event_name, "
        "CAST(NULL AS INT64) AS event_previous_timestamp, "
        "x.bundle AS event_bundle_sequence_id, "
        "CAST(NULL AS INT64) AS event_server_timestamp_offset, "
        "CAST(NULL AS INT64) AS event_original_occurrence_timestamp, "
        "x.batchIndex AS batch_event_index, x.ordering AS batch_ordering_id, "
        "x.uid AS user_id, x.pseudo AS user_pseudo_id, "
        "'14908341790' AS stream_id, x.platform AS platform, "
        "STRUCT('1.0.16' AS version) AS app_info, " + empty_params + " AS event_params "
        "FROM UNNEST([" + "".join(rows) + "]) AS x "
        "CROSS JOIN UNNEST(GENERATE_ARRAY(1, x.copies)) AS copy"
    )


@pytest.mark.skipif(
    os.getenv("DPA_RUN_BIGQUERY_FIXTURE") != "1",
    reason="explicit read-only BigQuery fixture run",
)
def test_bigquery_deterministic_app_remove_normalization() -> None:
    config = AnalyticsConfig.for_backend("production")
    query = build_app_remove_query(
        request(), config=config,
        selected_tables=(
            GaSourceTable(date(2026, 9, 28), "events_20260928", "Daily", "Final"),
        ),
    )
    sql = query.sql.replace(
        "SELECT *, DATE '2026-09-28' AS sourceTableDate, 'Daily' AS sourceTableKind, "
        "'Final' AS sourceFinalizationState FROM "
        "`bald-ops.analytics_538301722.events_20260928`",
        _app_remove_fixture_sql(),
    )
    aggregate = (
        "SELECT rawEventStatus, COUNT(*) AS logicalRows, "
        "SUM(physicalCount) AS physicalRows, "
        "SUM(exactDuplicateRowsRemoved) AS exactDuplicateRows "
        "FROM (" + sql + ") GROUP BY rawEventStatus ORDER BY rawEventStatus"
    )
    frame = query_dataframe(
        QuerySpec(aggregate, query.parameters),
        client=get_client(config), config=config, maximum_bytes_billed=20_971_520,
    )
    counts = {
        row.rawEventStatus: (
            int(row.logicalRows), int(row.physicalRows), int(row.exactDuplicateRows)
        )
        for row in frame.itertuples()
    }
    assert counts == {
        "GaDuplicateConflict": (1, 2, 0),
        "InvalidGaEvent": (1, 1, 0),
        "UnmappedMissingPseudoId": (1, 1, 0),
        "Valid": (1, 2, 1),
    }
