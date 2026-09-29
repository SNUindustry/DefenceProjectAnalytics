import json

from defence_project_analytics.analysis_brief import AnalysisBriefRequest, build_analysis_brief
from defence_project_analytics.metric_registry import (
    RUN_RETENTION,
    decision_metric_keys,
    evidence_metric_keys,
    target_metric_keys,
)
from defence_project_analytics.run_retention import TABLE_SPECS, RunRetentionRequest, _assemble_bundle
from defence_project_analytics.reporting.warnings import RunRetentionThresholds
from defence_project_analytics.reporting.writer import write_report_bundle
from test_run_retention import NOW, retention_frames


def test_report_is_aggregate_only_and_has_stable_tables(tmp_path) -> None:
    bundle = _assemble_bundle(
        RunRetentionRequest("Test", 1, analysis_as_of_utc=NOW), NOW,
        retention_frames(threshold=False), estimated_bytes=10,
        thresholds=RunRetentionThresholds(), generated_at_utc=NOW,
    )
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    assert path.parent.name == "run-retention"
    assert metadata["analysisType"] == "runRetention"
    assert metadata["analysisVersion"] == "1.0.0"
    assert metadata["definitions"]["returnDefinition"] == "NewAttempt"
    assert metrics["latency"]["observedDelaySeconds"]["p95"] == 400.0
    assert metrics["thresholdClassification"]["enabled"] is False
    for filename, (columns, _) in TABLE_SPECS.items():
        assert (path / "tables" / filename).read_text(encoding="utf-8").startswith(",".join(columns))
    combined = "\n".join(item.read_text(encoding="utf-8") for item in path.rglob("*.*"))
    for forbidden in (
        "telemetryPlayerId", "attemptId", "runId", "eventId", "operationId",
        "timeToReturn", "positiveResponseRate", "FeedbackResponse",
    ):
        assert forbidden not in combined


def test_empty_cohorts_keep_stable_headers(tmp_path) -> None:
    frames = retention_frames(threshold=False)
    frames["cohorts"] = frames["cohorts"].iloc[0:0]
    bundle = _assemble_bundle(
        RunRetentionRequest("Test", 1, analysis_as_of_utc=NOW), NOW, frames,
        estimated_bytes=0, thresholds=RunRetentionThresholds(), generated_at_utc=NOW,
    )
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    assert (path / "tables" / "run_retention_by_stage.csv").read_text(
        encoding="utf-8"
    ).startswith("cohortType,cohortValue,anchorFinalAttempts")


def test_c1_accepts_factual_retention_evidence_but_c2_authority_excludes_it(tmp_path) -> None:
    bundle = _assemble_bundle(
        RunRetentionRequest("Test", 1, analysis_as_of_utc=NOW), NOW,
        retention_frames(threshold=False), estimated_bytes=0,
        thresholds=RunRetentionThresholds(), generated_at_utc=NOW,
    )
    source = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    brief = build_analysis_brief(
        AnalysisBriefRequest("single-version", (source,)), workspace_root=tmp_path
    )
    assert any(item.domain == RUN_RETENTION for item in brief.evidence)
    assert any(key[0] == RUN_RETENTION for key in evidence_metric_keys())
    assert all(key[0] != RUN_RETENTION for key in decision_metric_keys())
    assert all(key[0] != RUN_RETENTION for key in target_metric_keys())
