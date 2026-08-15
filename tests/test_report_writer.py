from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from defence_project_analytics.reporting.models import (
    AnalysisScope, DataQuality, MetricRatio, ProgressionAnalysisScope, ReportBundle, ReportDefinitions,
    ReportMetadata, SampleSummary,
)
from defence_project_analytics.reporting.renderers import render_csv
from defence_project_analytics.reporting.writer import scope_hash, scope_id, write_report_bundle


def bundle(scope=None, generated_hour=1):
    scope = scope or AnalysisScope("Test", "Stage One", 2, release_id="r1")
    zero = MetricRatio.from_counts(0, 0)
    quality = DataQuality(zero, zero, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    metadata = ReportMetadata(
        "1.0.0", "stageDifficulty", "1.0.0", datetime(2026, 8, 14, generated_hour, tzinfo=timezone.utc),
        scope, SampleSummary(0, 0, 0, 0, 0, 0), quality, ReportDefinitions("p", "c", "d"),
    )
    return ReportBundle(metadata, {"ratio": zero}, "# Report\n", {"empty.csv": pd.DataFrame()})


def test_scope_path_and_hash_are_deterministic() -> None:
    scope = AnalysisScope("Test", "Stage One", 2, release_id="r1")
    assert scope_id(scope) == f"Test__stage-one__cv-2__{scope_hash(scope)}"
    assert len(scope_hash(scope)) == 8
    assert scope_hash(scope) != scope_hash(AnalysisScope("Test", "Stage One", 2, release_id="r2"))


def test_progression_scope_uses_all_stages_without_changing_existing_hash() -> None:
    existing = AnalysisScope("Test", "Stage One", 2, release_id="r1")
    assert scope_hash(existing) == scope_hash(AnalysisScope("Test", "Stage One", 2, release_id="r1"))
    progression = ProgressionAnalysisScope(
        environment="Test", content_version=4,
        analysis_as_of_utc=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    assert scope_id(progression).startswith("Test__all-stages__cv-4__")


def test_collision_refusal_and_safe_overwrite(tmp_path) -> None:
    first = write_report_bundle(bundle(), output_root=tmp_path)
    assert first.parent.name == "stage-difficulty"
    with pytest.raises(FileExistsError):
        write_report_bundle(bundle(), output_root=tmp_path)
    replaced = write_report_bundle(bundle(generated_hour=2), output_root=tmp_path, overwrite=True)
    assert json.loads((replaced / "metadata.json").read_text(encoding="utf-8"))["generatedAtUtc"].endswith("02:00:00Z")
    assert not list(replaced.parent.glob(f".{replaced.name}.*"))


def test_overwrite_rejects_unowned_or_mismatched_target(tmp_path) -> None:
    target = write_report_bundle(bundle(), output_root=tmp_path)
    (target / "metadata.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_report_bundle(bundle(), output_root=tmp_path, overwrite=True)


def test_header_only_csv_and_precision() -> None:
    assert render_csv(pd.DataFrame(), columns=("name", "ratio")) == "name,ratio\n"
    rendered = render_csv(pd.DataFrame([{"name": "x", "ratio": 1.23456789012345}]), columns=("name", "ratio"))
    assert rendered == "name,ratio\nx,1.23456789012\n"
    with pytest.raises(ValueError, match="Infinity"):
        render_csv(pd.DataFrame([{"name": "x", "ratio": float("inf")}]), columns=("name", "ratio"))
