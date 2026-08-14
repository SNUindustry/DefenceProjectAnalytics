from datetime import datetime, timezone
import json

import pytest

from defence_project_analytics.reporting.models import (
    AnalysisScope,
    DataQuality,
    MetricRatio,
    ReportDefinitions,
    ReportMetadata,
    ReportWarning,
    SampleSummary,
)
from defence_project_analytics.reporting.renderers import assert_factual_markdown, render_json
from defence_project_analytics.reporting.warnings import build_warnings


def quality(**changes):
    values = dict(
        telemetry_complete_rate=MetricRatio.from_counts(0, 0),
        detail_coverage_rate=MetricRatio.from_counts(0, 0),
        unresolved_release_rows=0,
        excluded_incomplete_detail_rows=0,
        unassessed_legacy_detail_rows=0,
        mixed_content_detail_rows=0,
        partially_covered_attempts=0,
        missing_death_attribution_rows=0,
        approximate_death_state_rows=0,
        missing_death_state_rows=0,
        invalid_death_time_rows=0,
    )
    values.update(changes)
    return DataQuality(**values)


def test_ratio_zero_denominator_and_camel_case_metadata() -> None:
    metadata = ReportMetadata(
        "1.0.0", "stageDifficulty", "1.0.0", datetime(2026, 8, 14, tzinfo=timezone.utc),
        AnalysisScope("Test", "stage1", 2), SampleSummary(0, 0, 0, 0, 0, 0), quality(),
        ReportDefinitions("final", "Clear / decided", "complete"),
    )
    payload = json.loads(render_json(metadata))
    assert list(payload)[:3] == ["reportContractVersion", "analysisType", "analysisVersion"]
    assert payload["generatedAtUtc"] == "2026-08-14T00:00:00Z"
    assert payload["scope"]["stageKey"] == "stage1"
    assert payload["quality"]["telemetryCompleteRate"] == {"count": 0, "denominator": 0, "ratio": None}


def test_nan_becomes_null_and_infinity_is_rejected() -> None:
    assert json.loads(render_json({"value": float("nan")}))["value"] is None
    with pytest.raises(ValueError, match="Infinity"):
        render_json({"value": float("inf")})


def test_warning_order_is_stable() -> None:
    sample = SampleSummary(2, 1, 0, 0, 0, 0)
    warnings = build_warnings(sample, quality(mixed_content_detail_rows=1, invalid_death_time_rows=2))
    assert [item.code for item in warnings] == [
        "LOW_SAMPLE_ATTEMPTS", "LOW_SAMPLE_PLAYERS", "LOW_SAMPLE_DEATHS", "LOW_DETAIL_COVERAGE",
        "MIXED_CONTENT_DETAIL_EXCLUDED", "INVALID_DEATH_TIME",
    ]


@pytest.mark.parametrize("word", ["overtuned", "nerf", "balancing recommendation"])
def test_markdown_rejects_balancing_language(word: str) -> None:
    with pytest.raises(ValueError):
        assert_factual_markdown(f"This is a {word}.")


def test_warning_model_serializes_in_field_order() -> None:
    assert render_json(ReportWarning("LOW_SAMPLE_DEATHS", "Small sample")).startswith('{\n  "code"')
