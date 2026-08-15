from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from defence_project_analytics.analysis_brief import (
    AnalysisBriefRequest,
    build_analysis_brief,
)
from defence_project_analytics.brief.writer import write_analysis_brief


def test_comparison_bundle_preserves_source_values_and_is_aggregate_only(tmp_path: Path) -> None:
    source = Path(
        "reports/generated/content-version-compare/"
        "Test__stage1__cv-1-vs-cv-4__89b98645"
    )
    if not source.exists():
        pytest.skip("local acceptance comparison artifact is not present")
    before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    brief = build_analysis_brief(AnalysisBriefRequest(
        mode="contentVersionCompare", source_report_paths=(source,),
    ))
    path = write_analysis_brief(brief, output_root=tmp_path)
    assert {item.name for item in path.iterdir()} == {
        "brief.md", "brief.json", "evidence.json", "manifest.json"
    }
    payload = json.loads((path / "brief.json").read_text(encoding="utf-8"))
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["mode"] == "contentVersionCompare"
    assert payload["overallStatus"] == "Limited"
    assert manifest["bigQueryEstimatedBytes"] == 0
    assert manifest["cloudAccessPerformed"] is False
    assert manifest["sourceAnalyzersExecuted"] is False
    assert not any(Path(item["portablePath"] or "").is_absolute() for item in manifest["sourceBundles"])
    combined = "\n".join(
        item.read_text(encoding="utf-8") for item in path.iterdir()
    )
    for forbidden in (
        '"telemetryPlayerId":', '"runId":', '"attemptId":', '"eventId":',
        '"operationId":', '"uploadId":',
    ):
        assert forbidden not in combined
    after = {
        item.relative_to(source).as_posix(): item.read_bytes()
        for item in source.rglob("*") if item.is_file()
    }
    assert before == after

    catalog = json.loads((path / "evidence.json").read_text(encoding="utf-8"))
    item = next(
        row for row in catalog["evidenceItems"]
        if row["provenance"]["sourceArtifact"] == "tables/comparison_stage.csv"
    )
    key = item["provenance"]["sourceRowKey"]
    frame = pd.read_csv(source / "tables/comparison_stage.csv")
    matched = frame[
        (frame["metric"] == key["metric"])
        & (frame["metricFamily"] == key["metricFamily"])
        & (frame["entityKey"].fillna("") == (key["entityKey"] or ""))
        & (frame["dimensionValue"].fillna("") == (key["dimensionValue"] or ""))
    ].iloc[0]
    expected = None if pd.isna(matched["baselineValue"]) else matched["baselineValue"]
    assert item["value"]["baselineValue"] == expected


def test_collision_refusal_and_safe_overwrite(tmp_path: Path) -> None:
    source = Path(
        "reports/generated/content-version-compare/"
        "Test__stage1__cv-1-vs-cv-4__89b98645"
    )
    if not source.exists():
        pytest.skip("local acceptance comparison artifact is not present")
    brief = build_analysis_brief(AnalysisBriefRequest(
        mode="contentVersionCompare", source_report_paths=(source,),
    ))
    first = write_analysis_brief(brief, output_root=tmp_path)
    with pytest.raises(FileExistsError):
        write_analysis_brief(brief, output_root=tmp_path)
    replaced = write_analysis_brief(brief, output_root=tmp_path, overwrite=True)
    assert replaced == first
