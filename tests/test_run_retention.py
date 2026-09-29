from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from defence_project_analytics.reporting.warnings import RunRetentionThresholds
from defence_project_analytics.run_retention import (
    RunRetentionRequest,
    _assemble_bundle,
    analyze_run_retention,
)


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def retention_frames(*, threshold: bool = True) -> dict[str, pd.DataFrame]:
    population = {
        "anchorFinalAttempts": 10, "uniquePlayers": 2, "clears": 3, "deaths": 6,
        "abandons": 1, "unrecognizedOutcomes": 0, "eligibleAnchors": 9,
        "ineligibleAnchors": 1, "nextRunObserved": 6, "noNextRunObservedAsOf": 3,
        "latencyRightCensoredAnchors": 3,
        "thresholdRightCensoredAnchors": 1 if threshold else None,
        "returnedWithinThresholdCount": 4 if threshold else None,
        "returnedAfterThresholdCount": 2 if threshold else None,
        "noNextRunBeyondThresholdCount": 2 if threshold else None,
        "thresholdExceededCount": 4 if threshold else None,
        "thresholdResolvedDenominator": 8 if threshold else None,
        "physicalAnchorRows": 12, "dedupedAnchorRows": 10,
        "missingPlayerIdentityAnchors": 1, "missingAnchorEndRows": 0,
        "conflictingIdentityAnchors": 0, "invalidTimingAnchors": 0,
        "physicalNextCandidateRows": 9, "dedupedNextCandidateRows": 7,
        "resumeContinuationsExcluded": 2, "missingRunStartSnapshotRows": 0,
        "ambiguousNextAttemptOrderGroups": 1, "crossStageNextAttempts": 2,
        "crossContentNextAttempts": 1, "crossReleaseNextAttempts": 1,
        "nextOutcomePendingAttempts": 1,
    }
    overall = {
        "cohortType": "overall", "cohortValue": "All", "anchorFinalAttempts": 10,
        "eligibleAnchors": 9, "nextRunObservedCount": 6,
        "noNextRunObservedAsOfCount": 3, "nextRunObservedRate": 2 / 3,
        "latencyRightCensoredCount": 3, "latencyRightCensoredRate": 1 / 3,
        "returnedWithinThresholdCount": 4 if threshold else None,
        "returnedAfterThresholdCount": 2 if threshold else None,
        "noNextRunBeyondThresholdCount": 2 if threshold else None,
        "thresholdRightCensoredCount": 1 if threshold else None,
        "thresholdExceededCount": 4 if threshold else None,
        "thresholdResolvedDenominator": 8 if threshold else None,
        "thresholdExceededRate": 0.5 if threshold else None,
    }
    return {
        "population": pd.DataFrame([population]),
        "cohorts": pd.DataFrame([
            overall,
            {**overall, "cohortType": "outcome", "cohortValue": "Dead"},
            {**overall, "cohortType": "stage", "cohortValue": "stage1"},
        ]),
        "nextContext": pd.DataFrame([{
            "observedNextCount": 6, "censoredCount": 3,
            "nextRunDelayP50": 100.0, "nextRunDelayP75": 200.0,
            "nextRunDelayP90": 300.0, "nextRunDelayP95": 400.0,
            "censoredObservationAgeP50": 500.0, "censoredObservationAgeP75": 600.0,
            "censoredObservationAgeP90": 700.0, "censoredObservationAgeP95": 800.0,
            "sameStageNextRunCount": 4, "sameContentNextRunCount": 5,
            "crossStageNextAttempts": 2, "crossContentNextAttempts": 1,
            "crossReleaseNextAttempts": 1, "nextOutcomePendingAttempts": 1,
        }]),
    }


def test_request_requires_explicit_threshold_and_grace_pair() -> None:
    request = RunRetentionRequest("Test", 1, analysis_as_of_utc=NOW)
    assert request.to_scope(NOW).stage_key is None
    assert request.to_scope(NOW).long_term_no_next_run_threshold_days is None
    with pytest.raises(ValueError, match="provided together"):
        RunRetentionRequest("Test", 1, long_term_no_next_run_threshold_days=7)
    with pytest.raises(ValueError, match="positive integer"):
        RunRetentionRequest("Test", 1, long_term_no_next_run_threshold_days=0, source_upload_grace_hours=1)
    with pytest.raises(ValueError, match="non-negative integer"):
        RunRetentionRequest("Test", 1, long_term_no_next_run_threshold_days=7, source_upload_grace_hours=-1)


def test_threshold_facts_preserve_late_return_and_resolved_denominator() -> None:
    request = RunRetentionRequest(
        "Test", 1, analysis_as_of_utc=NOW,
        long_term_no_next_run_threshold_days=7, source_upload_grace_hours=24,
    )
    bundle = _assemble_bundle(
        request, NOW, retention_frames(), estimated_bytes=12,
        thresholds=RunRetentionThresholds(), generated_at_utc=NOW,
    )
    threshold = bundle.metrics["thresholdClassification"]
    assert threshold["returnedWithinThresholdCount"] == 4
    assert threshold["returnedAfterThresholdCount"] == 2
    assert threshold["noNextRunBeyondThresholdCount"] == 2
    assert threshold["thresholdRightCensoredCount"] == 1
    assert threshold["thresholdExceededCount"] == 4
    assert threshold["thresholdResolvedDenominator"] == 8
    assert threshold["thresholdExceededRate"] == 0.5


def test_unconfigured_threshold_does_not_synthesize_classification() -> None:
    bundle = _assemble_bundle(
        RunRetentionRequest("Test", 1, analysis_as_of_utc=NOW), NOW,
        retention_frames(threshold=False), estimated_bytes=0,
        thresholds=RunRetentionThresholds(), generated_at_utc=NOW,
    )
    threshold = bundle.metrics["thresholdClassification"]
    assert threshold["enabled"] is False
    assert threshold["thresholdExceededCount"] is None
    assert threshold["thresholdResolvedDenominator"] is None
    assert "no default threshold was applied" in bundle.markdown


def test_cost_gate_stops_before_any_actual_query(monkeypatch) -> None:
    monkeypatch.setattr(
        "defence_project_analytics.run_retention.dry_run_query",
        lambda *args, **kwargs: SimpleNamespace(total_bytes_processed=50),
    )
    actual: list[bool] = []
    monkeypatch.setattr(
        "defence_project_analytics.run_retention.query_dataframe",
        lambda *args, **kwargs: actual.append(True),
    )
    with pytest.raises(RuntimeError, match="no analysis query was executed"):
        analyze_run_retention(
            RunRetentionRequest("Test", 1), maximum_total_bytes=100, clock=lambda: NOW
        )
    assert actual == []
