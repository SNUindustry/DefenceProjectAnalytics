import json

from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.reporting.warnings import UpgradeThresholds
from defence_project_analytics.upgrade_choice import TABLE_SPECS, UpgradeChoiceRequest, _assemble_bundle
from test_upgrade_choice import NOW, upgrade_frames


def test_upgrade_report_is_aggregate_only_and_has_stable_empty_tables(tmp_path) -> None:
    frames = upgrade_frames()
    frames["headToHead"] = frames["headToHead"].iloc[0:0]
    bundle = _assemble_bundle(
        UpgradeChoiceRequest("Test", "stage1", 2, analysis_as_of_utc=NOW), NOW,
        frames, estimated_bytes=10, thresholds=UpgradeThresholds(), generated_at_utc=NOW,
    )
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["analysisType"] == "upgradeChoice"
    assert metadata["reportContractVersion"] == "1.0.0"
    assert metrics["outcomeAssociation"]["exposedNotSelectedAttempts"] == 20
    assert metrics["outcomeAssociation"]["alternativeSelectedAttempts"] == 12
    assert metrics["outcomeAssociation"]["noSelectionOnlyAttempts"] == 8
    pair_csv = (path / "tables" / "upgrade_head_to_head.csv").read_text(encoding="utf-8")
    assert pair_csv.startswith("candidateA,candidateB,coExposureCount")
    combined = "\n".join(item.read_text(encoding="utf-8") for item in path.rglob("*.*"))
    for forbidden in ("telemetryPlayerId", "attemptId", "runId", "uploadId", "exposureId", "instanceId"):
        assert forbidden not in combined
    assert "observational, not causal" in combined
    assert "nerf" not in combined.casefold()
    assert "buff" not in combined.casefold()


def test_no_elapsed_context_is_used_as_a_notable_signal() -> None:
    bundle = _assemble_bundle(
        UpgradeChoiceRequest("Test", "stage1", 2, analysis_as_of_utc=NOW), NOW,
        upgrade_frames(), estimated_bytes=0, thresholds=UpgradeThresholds(0, 0, 0, 0, 0),
        generated_at_utc=NOW,
    )
    signals = bundle.markdown.split("## Notable Statistical Signals", 1)[1].split("## Caveats", 1)[0]
    assert "elapsed" not in signals.casefold()
