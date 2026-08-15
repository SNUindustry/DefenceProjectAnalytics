import json

from defence_project_analytics.progression_next_run import (
    TABLE_SPECS,
    ProgressionNextRunRequest,
    _assemble_bundle,
)
from defence_project_analytics.reporting.warnings import ProgressionThresholds
from defence_project_analytics.reporting.writer import write_report_bundle
from test_progression_next_run import NOW, progression_frames


def test_report_is_stage_less_aggregate_only_and_deterministic(tmp_path) -> None:
    frames = progression_frames()
    frames["coOccurrence"] = frames["coOccurrence"].iloc[0:0]
    bundle = _assemble_bundle(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=NOW), NOW,
        frames, estimated_bytes=10, thresholds=ProgressionThresholds(),
        generated_at_utc=NOW,
    )
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    assert path.name.startswith("Test__all-stages__cv-4__")
    assert metadata["analysisType"] == "progressionNextRun"
    assert metadata["reportContractVersion"] == "1.0.0"
    assert metadata["definitions"]["progressionNextRunAssociationIsCausal"] is False
    assert metrics["sample"]["boundedEpisodes"] == 1
    assert (path / "tables" / "progression_cooccurrence.csv").read_text(
        encoding="utf-8"
    ).startswith("kindA,kindB,coOccurrenceEpisodeCount")
    combined = "\n".join(item.read_text(encoding="utf-8") for item in path.rglob("*.*"))
    for forbidden in (
        "telemetryPlayerId", "attemptId", "runId", "eventId", "transactionId", "batchId"
    ):
        assert forbidden not in combined
    for forbidden in ("recommendation", "nerf", "buff", "overtuned", "undertuned"):
        assert forbidden not in combined.casefold()


def test_warning_order_and_unbounded_policy_are_stable() -> None:
    frames = progression_frames()
    frames["population"].loc[0, "unboundedProgressionEvents"] = 2
    frames["population"].loc[0, "unboundedProgressionPlayers"] = 1
    bundle = _assemble_bundle(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=NOW), NOW,
        frames, estimated_bytes=0, thresholds=ProgressionThresholds(), generated_at_utc=NOW,
    )
    codes = [item.code for item in bundle.metadata.warnings]
    assert codes.index("LOW_PROGRESSION_SAMPLE") < codes.index(
        "UNBOUNDED_PROGRESSION_EPISODE_EXCLUDED"
    )
    assert "activity and excluded" in next(
        item.message for item in bundle.metadata.warnings
        if item.code == "UNBOUNDED_PROGRESSION_EPISODE_EXCLUDED"
    )

