import json

from defence_project_analytics.post_run_behavior import (
    TABLE_SPECS,
    PostRunBehaviorRequest,
    _assemble_bundle,
)
from defence_project_analytics.reporting.warnings import PostRunThresholds
from defence_project_analytics.reporting.writer import write_report_bundle
from test_post_run_behavior import NOW, post_run_frames


def test_post_run_report_is_aggregate_only_and_uses_stable_contract(tmp_path) -> None:
    bundle = _assemble_bundle(
        PostRunBehaviorRequest("Test", 4, analysis_as_of_utc=NOW), NOW,
        post_run_frames(), estimated_bytes=10, thresholds=PostRunThresholds(),
        generated_at_utc=NOW,
    )
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    assert path.name.startswith("Test__all-stages__cv-4__")
    assert metadata["analysisType"] == "postRunBehavior"
    assert metadata["reportContractVersion"] == "1.0.0"
    assert metrics["navigation"]["shopPresentedWindows"] == 3
    assert metrics["navigation"]["shopUserNavigatedWindows"] == 2
    commerce_csv = (path / "tables" / "post_run_commerce.csv").read_text(encoding="utf-8")
    assert "observedAttemptSuccessRate" in commerce_csv
    assert "committedSuccessWindowRate" in commerce_csv
    combined = "\n".join(item.read_text(encoding="utf-8") for item in path.rglob("*.*"))
    for forbidden in (
        "telemetryPlayerId", "attemptId", "runId", "eventId", "operationId",
        "presentationId", "batchId", "uploadId",
    ):
        assert forbidden not in combined
    for forbidden in ("recommendation", "nerf", "buff", "retention", "churn rate"):
        assert forbidden not in combined.casefold()


def test_empty_breakdown_frames_keep_stable_headers(tmp_path) -> None:
    frames = post_run_frames()
    frames["navigation"] = frames["navigation"].iloc[0:0]
    bundle = _assemble_bundle(
        PostRunBehaviorRequest("Test", 4, analysis_as_of_utc=NOW), NOW,
        frames, estimated_bytes=0, thresholds=PostRunThresholds(), generated_at_utc=NOW,
    )
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    header = (path / "tables" / "post_run_navigation.csv").read_text(encoding="utf-8")
    assert header.startswith("anchorOutcome,dimension,viewedWindows")
