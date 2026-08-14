from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from defence_project_analytics.reporting.renderers import render_json
from defence_project_analytics.reporting.warnings import WarningThresholds
from defence_project_analytics.stage_difficulty import (
    StageDifficultyRequest,
    _assemble_bundle,
    analyze_stage_difficulty,
)


def frames():
    return {
        "summary": pd.DataFrame([{
            "final_attempts": 4, "unique_players": 2, "clears": 1, "deaths": 1,
            "abandons": 2, "unrecognized_outcomes": 0, "unresolved_release_rows": 0,
            "eligible_runs": 2, "distinct_attempts": 1, "partially_covered_attempts": 1,
            "mixed_content_detail_rows": 1, "excluded_incomplete_detail_rows": 1,
            "unassessed_legacy_detail_rows": 1, "assessed_detail_rows": 3,
            "complete_detail_rows": 2, "survival_p25": 10.0, "survival_p50": 20.0,
            "survival_p75": 30.0, "survival_p90": 38.0,
        }]),
        "death_timing": pd.DataFrame([{
            "bucket_order": 0, "bucket": "0-30s", "count": 1, "denominator": 1, "ratio": 1.0,
            "death_count": 1, "invalid_death_time_rows": 0, "attempt_p10": 20.0,
            "attempt_p25": 20.0, "attempt_p50": 20.0, "attempt_p75": 20.0,
            "attempt_p90": 20.0, "final_segment_p10": 5.0, "final_segment_p25": 5.0,
            "final_segment_p50": 5.0, "final_segment_p75": 5.0, "final_segment_p90": 5.0,
        }]),
        "death_concentration": pd.DataFrame([{
            "dimension": "phase", "value": "1", "exact_count": 1,
            "approximate_count": 0, "count": 1, "denominator": 1, "ratio": 1.0,
        }]),
        "death_causes": pd.DataFrame([
            {"row_type": "source", "label": "Enemy", "enemy_tier": None, "enemy_classification": None, "count": 1, "denominator": 1, "ratio": 1.0},
            {"row_type": "enemy", "label": "slime", "enemy_tier": 1, "enemy_classification": "Normal", "count": 1, "denominator": 1, "ratio": 1.0},
        ]),
        "incoming_damage": pd.DataFrame([
            {"row_type": "source", "label": "Enemy", "enemy_tier": None, "enemy_classification": None, "total_applied_damage": 20.0, "hit_count": 2, "lethal_hit_events": 1, "affected_runs": 1, "lethal_runs": 1, "damage_ratio": 1.0},
            {"row_type": "enemy", "label": "slime", "enemy_tier": 1, "enemy_classification": "Normal", "total_applied_damage": 20.0, "hit_count": 2, "lethal_hit_events": 1, "affected_runs": 1, "lethal_runs": 1, "damage_ratio": 1.0},
        ]),
        "threat": pd.DataFrame([{"outcome": "Dead", "metric": "nearDeathCount", "eligible_run_count": 1, "observed_count": 1, "missing_count": 0, "mean": 1.0, "p25": 1.0, "median": 1.0, "p75": 1.0}]),
        "death_state": pd.DataFrame([{"metric": "hpRatio", "eligible_deaths": 1, "selected_snapshots": 1, "observed_count": 1, "missing_count": 0, "mean": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0, "true_count": 0}]),
    }


def test_final_attempt_denominator_abandons_and_quality_exclusions() -> None:
    bundle = _assemble_bundle(
        StageDifficultyRequest("Test", "stage1", 2), frames(), estimated_bytes=123,
        thresholds=WarningThresholds(), clock=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    outcome = bundle.metrics["outcome"]
    assert outcome["final_attempts"] == 4
    assert outcome["clear_rate"].count == 1
    assert outcome["clear_rate"].denominator == 2
    assert outcome["clear_rate"].ratio == pytest.approx(0.5)
    assert bundle.metadata.quality.excluded_incomplete_detail_rows == 1
    assert bundle.metadata.quality.unassessed_legacy_detail_rows == 1
    assert bundle.metadata.quality.mixed_content_detail_rows == 1


def test_output_is_aggregate_only_and_finite() -> None:
    bundle = _assemble_bundle(
        StageDifficultyRequest("Test", "stage1", 2), frames(), estimated_bytes=0,
        thresholds=WarningThresholds(0, 0, 0, 0), clock=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    serialized = render_json(bundle.metrics)
    for raw_identifier in ("telemetryPlayerId", "attemptId", "runId", "uploadId"):
        assert raw_identifier not in serialized
        assert raw_identifier not in bundle.markdown
    assert bundle.metrics["incoming_damage"]["lethal_hit_events"] == 1
    assert bundle.metrics["death_causes"]["top_enemies"][0]["count"] == 1


def test_cost_gate_stops_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "defence_project_analytics.stage_difficulty.dry_run_query",
        lambda *args, **kwargs: SimpleNamespace(total_bytes_processed=200),
    )
    executed = []
    monkeypatch.setattr(
        "defence_project_analytics.stage_difficulty.query_dataframe",
        lambda *args, **kwargs: executed.append(True),
    )
    with pytest.raises(RuntimeError, match="no analysis query was executed"):
        analyze_stage_difficulty(StageDifficultyRequest("Test", "stage1", 2), maximum_total_bytes=100)
    assert executed == []


def test_request_validation_and_exact_zero_content_version() -> None:
    assert StageDifficultyRequest("Test", "stage1", 0).content_version == 0
    with pytest.raises(ValueError):
        StageDifficultyRequest("Staging", "stage1", 2)
    with pytest.raises(ValueError):
        StageDifficultyRequest("Test", " ", 2)
