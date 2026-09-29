from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_r3a_lifecycle_source_does_not_change_b7_new_attempt_semantics() -> None:
    sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "sql" / "analysis").glob("*run_retention*.sql"))
    )

    assert "telemetry_app_lifecycle_events" not in sql
    assert "AppReturnObserved" not in sql
    assert "returnDefinition = NewAttempt" in (
        ROOT / "docs" / "r3-a-source-boundary.md"
    ).read_text(encoding="utf-8")


def test_r3a_raw_linkage_identifiers_are_not_report_contract_fields() -> None:
    reporting = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (ROOT / "src" / "defence_project_analytics" / "reporting").glob("*.py")
        )
    )

    for identifier in (
        "retentionBridgeId",
        "appProcessSessionId",
        "lifecycleOccurrenceId",
    ):
        assert identifier not in reporting
