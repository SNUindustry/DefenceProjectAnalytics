from datetime import datetime, timezone

import pandas as pd
from types import SimpleNamespace

from defence_project_analytics.content_version_comparison import (
    TABLE_SPECS,
    ContentVersionCompareRequest,
    generate_content_version_comparison_report,
)
from defence_project_analytics.reporting.models import (
    ComparisonDataQuality,
    ContentVersionComparisonDefinitions,
    ContentVersionComparisonReportMetadata,
    ContentVersionComparisonSample,
    ContentVersionComparisonScope,
    ReportBundle,
)
from defence_project_analytics.reporting.writer import scope_id, write_report_bundle


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_comparison_scope_preserves_input_direction() -> None:
    scope = ContentVersionComparisonScope(
        environment="Test", baseline_content_version=4, candidate_content_version=1,
        stage_key="stage1", analysis_as_of_utc=NOW,
    )
    assert scope_id(scope).startswith("Test__stage1__cv-4-vs-cv-1__")


def test_final_comparison_bundle_has_stable_empty_tables(tmp_path) -> None:
    scope = ContentVersionComparisonScope(
        environment="Test", baseline_content_version=1, candidate_content_version=4,
        stage_key="stage1", analysis_as_of_utc=NOW,
    )
    quality = ComparisonDataQuality(1, 0, 1, 0, 0, 1, 1)
    metadata = ContentVersionComparisonReportMetadata(
        "1.0.0", "contentVersionCompare", "1.0.0", NOW, scope,
        ContentVersionComparisonSample({"stageDifficulty": 1}, {"stageDifficulty": 2}),
        quality, ContentVersionComparisonDefinitions(), (), (), 10,
    )
    tables = {name: pd.DataFrame() for name in TABLE_SPECS}
    bundle = ReportBundle(metadata, {"comparisonDataQuality": quality}, "# ContentVersion Comparison\n", tables)
    path = write_report_bundle(bundle, output_root=tmp_path, table_specs=TABLE_SPECS)
    assert path.parent.name == "content-version-compare"
    assert (path / "tables" / "comparison_stage.csv").read_text().startswith("domain,metricFamily")


def test_generate_performs_exactly_one_final_artifact_write(monkeypatch, tmp_path) -> None:
    scope = ContentVersionComparisonScope(
        environment="Test", baseline_content_version=1, candidate_content_version=4,
        stage_key="stage1", analysis_as_of_utc=NOW,
    )
    quality = ComparisonDataQuality(1, 0, 1, 0, 0, 0, 0)
    metadata = ContentVersionComparisonReportMetadata(
        "1.0.0", "contentVersionCompare", "1.0.0", NOW, scope,
        ContentVersionComparisonSample({}, {}), quality,
        ContentVersionComparisonDefinitions(), (), (), 0,
    )
    bundle = ReportBundle(metadata, {}, "# ContentVersion Comparison\n", {})
    monkeypatch.setattr(
        "defence_project_analytics.content_version_comparison.analyze_content_version_comparison",
        lambda *args, **kwargs: SimpleNamespace(bundle=bundle),
    )
    writes: list[ReportBundle] = []
    monkeypatch.setattr(
        "defence_project_analytics.content_version_comparison.write_report_bundle",
        lambda item, **kwargs: writes.append(item) or tmp_path / "final",
    )
    path = generate_content_version_comparison_report(
        ContentVersionCompareRequest("Test", 1, 4, stage_key="stage1"),
        output_root=tmp_path,
    )
    assert path == tmp_path / "final"
    assert writes == [bundle]
