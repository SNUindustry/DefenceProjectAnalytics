from datetime import datetime, timezone

import pytest

from defence_project_analytics.comparison import (
    comparison_status,
    distribution_delta,
    ratio_delta,
    scalar_delta,
)
from defence_project_analytics.content_version_comparison import (
    ContentVersionCompareRequest,
    STAGE,
    compare_content_version_snapshots,
)
from defence_project_analytics.reporting.models import (
    ContentVersionAnalysisSnapshot,
    MetricRatio,
    DistributionSummary,
    SourceAnalysisManifest,
    SourceAnalysisSnapshot,
)
from defence_project_analytics.reporting.warnings import ComparisonThresholds


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def stage_snapshot(version: int, *, attempts: int, coverage: float, warning: bool) -> SourceAnalysisSnapshot:
    ratio = {"count": int(attempts * coverage), "denominator": attempts, "ratio": coverage}
    manifest = SourceAnalysisManifest(
        domain=STAGE, side="baseline" if version == 1 else "candidate",
        analysis_type="stageDifficulty", analysis_version="1.0.0",
        report_contract_version="1.0.0", scope={"contentVersion": version},
        generated_at_utc=NOW, snapshot_mode="uploadedAtUtcUpperBound",
        snapshot_cutoff_utc=NOW, snapshot_guarantee="ingestion cutoff",
        sample={"finalAttempts": attempts},
        quality={"telemetryCompleteRate": ratio, "detailCoverageRate": ratio},
        definitions={"clearRate": "Clear / (Clear + Dead)"},
        warnings=({"code": "LOW_SAMPLE_ATTEMPTS", "message": "low"},) if warning else (),
    )
    metrics = {
        "outcome": {
            "finalAttempts": attempts, "uniquePlayers": attempts, "clears": 5,
            "deaths": 5, "abandons": 0,
            "clearRate": {"count": 5, "denominator": 10, "ratio": 0.5},
        },
        "survival": {"p25": 10.0, "median": 20.0, "p75": 30.0, "p90": 40.0},
        "dataQuality": {
            "telemetryCompleteRate": ratio, "detailCoverageRate": ratio,
        },
    }
    return SourceAnalysisSnapshot(STAGE, version, manifest, metrics, {})


def test_delta_math_preserves_direction_and_zero_baseline() -> None:
    scalar = scalar_delta(0, 5)
    assert scalar.absolute_delta == 5
    assert scalar.relative_delta is None
    ratio = ratio_delta(MetricRatio(2, 10, 0.2), MetricRatio(5, 10, 0.5))
    assert ratio.percentage_point_delta == pytest.approx(30.0)
    assert ratio.relative_delta == pytest.approx(1.5)
    distribution = distribution_delta(
        DistributionSummary(10, 0, p25=1, median=2, p75=3),
        DistributionSummary(12, 0, p25=2, median=4, p75=6),
    )
    assert distribution.p25_delta == 1
    assert distribution.median_delta == 2
    assert distribution.p75_delta == 3


def test_coarse_status_does_not_hide_multiple_warning_codes() -> None:
    assert comparison_status((
        "LOW_BASELINE_SAMPLE", "MATERIAL_COVERAGE_DIFFERENCE", "BASELINE_SOURCE_WARNING",
    )) == "Limited"


def test_stage_comparison_keeps_sample_coverage_and_source_warnings() -> None:
    request = ContentVersionCompareRequest("Test", 1, 4, stage_key="stage1", domains=("stage",))
    baseline = ContentVersionAnalysisSnapshot(1, {STAGE: stage_snapshot(1, attempts=10, coverage=0.4, warning=True)})
    candidate = ContentVersionAnalysisSnapshot(4, {STAGE: stage_snapshot(4, attempts=40, coverage=0.9, warning=False)})
    comparison = compare_content_version_snapshots(
        request, baseline, candidate,
        thresholds=ComparisonThresholds(material_sample_imbalance_ratio=4),
    )[STAGE]
    assert comparison.status == "Limited"
    assert "LOW_BASELINE_SAMPLE" in comparison.warning_codes
    assert "MATERIAL_SAMPLE_IMBALANCE" in comparison.warning_codes
    assert "MATERIAL_COVERAGE_DIFFERENCE" in comparison.warning_codes
    assert "BASELINE_SOURCE_WARNING" in comparison.warning_codes
    assert set(comparison.rows[0].warning_codes).issuperset(comparison.warning_codes)


def test_request_defaults_and_stage_dependent_validation() -> None:
    stage_less = ContentVersionCompareRequest("Test", 1, 4)
    assert stage_less.resolved_domains() == ("progressionNextRun", "postRunBehavior")
    with pytest.raises(ValueError, match="stage_key"):
        ContentVersionCompareRequest("Test", 1, 4, domains=("weapon",))
    with pytest.raises(ValueError, match="must differ"):
        ContentVersionCompareRequest("Test", 1, 1)
