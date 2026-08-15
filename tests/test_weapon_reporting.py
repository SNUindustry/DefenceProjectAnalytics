import json

from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.reporting.warnings import WeaponThresholds
from defence_project_analytics.weapon_performance import TABLE_SPECS, WeaponPerformanceRequest, _assemble_bundle
from test_weapon_performance import NOW, weapon_frames


def test_weapon_report_is_aggregate_only_and_has_stable_empty_tables(tmp_path) -> None:
    frames = weapon_frames()
    frames["boss"] = frames["boss"].iloc[0:0]
    bundle = _assemble_bundle(
        WeaponPerformanceRequest("Test", "stage1", 2, analysis_as_of_utc=NOW), NOW,
        frames, estimated_bytes=10, thresholds=WeaponThresholds(), generated_at_utc=NOW,
    )
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["analysisType"] == "weaponPerformance"
    assert metadata["reportContractVersion"] == "1.0.0"
    assert metrics["dps"]["coverage"] == {"count": 2, "denominator": 3, "ratio": 2 / 3}
    boss_csv = (path / "tables" / "weapon_boss_performance.csv").read_text(encoding="utf-8")
    assert boss_csv.startswith("weaponFamilyId,bossEligibleSegments")
    combined = "\n".join(item.read_text(encoding="utf-8") for item in path.rglob("*.*"))
    for forbidden in ("telemetryPlayerId", "attemptId", "runId", "uploadId", "instanceId"):
        assert forbidden not in combined
    assert "combatObservedRuns" not in combined
    assert "observational, not causal" in combined


def test_elapsed_difference_is_not_a_notable_signal() -> None:
    bundle = _assemble_bundle(
        WeaponPerformanceRequest("Test", "stage1", 2, analysis_as_of_utc=NOW), NOW,
        weapon_frames(), estimated_bytes=0,
        thresholds=WeaponThresholds(0, 0, 0, 0, 0, 0, 0, 0), generated_at_utc=NOW,
    )
    signals = bundle.markdown.split("## Notable Statistical Signals", 1)[1].split("## Caveats", 1)[0]
    assert "elapsed" not in signals.casefold()

