from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from defence_project_analytics.progression_next_run import (
    ProgressionNextRunRequest,
    _assemble_bundle,
    analyze_progression_next_run,
)
from defence_project_analytics.reporting.warnings import ProgressionThresholds


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def progression_frames() -> dict[str, pd.DataFrame]:
    population = {
        "progressionEvents": 8, "uniquePlayers": 1, "progressionKinds": 4,
        "uniqueTargets": 8, "physicalProgressionRows": 9,
        "conflictingProgressionEvents": 1, "missingPlayerIdentityEvents": 0,
        "missingOccurredAtEvents": 0, "missingTargetIdentityEvents": 0,
        "unrecognizedProgressionKindEvents": 0, "transactionLinkedEvents": 6,
        "standaloneEvents": 2, "invalidTransactionLinkEvents": 0,
        "unexpectedStandaloneEvents": 0, "unassessedUnrecognizedLinkageEvents": 0,
        "unboundedProgressionEvents": 0, "unboundedProgressionPlayers": 0,
        "ambiguousTimestampEvents": 0, "boundedEpisodes": 1,
        "singleProgressionEpisodes": 0, "multiProgressionEpisodes": 1,
        "matureEpisodes": 1, "rightCensoredEpisodes": 0,
        "episodesWithPreviousRun": 1, "episodesWithNextRun": 0,
        "sameStagePairedEpisodes": 0, "bothBoundaryEpisodes": 0,
        "previousOnlyEpisodes": 1, "nextOnlyEpisodes": 0,
        "previousContextEligibleEpisodes": 1, "previousRunBeyondWindowEpisodes": 0,
        "previousRunMissingEpisodes": 0, "nextRunWithinWindowEpisodes": 0,
        "noNextRunWithinWindowEpisodes": 1, "laterNextRunOutsideWindowEpisodes": 0,
        "nextRunOutcomePendingEpisodes": 0, "resumeContinuationsSkipped": 0,
        "openAttemptEpisodesExcludedFromPairing": 0,
        "lifecycleTerminalsBetweenProgressionAndNextAttempt": 0,
        "crossContentPerformancePairsExcluded": 0,
        "crossContentPreviousBoundaries": 0, "crossContentNextBoundaries": 0,
        "crossReleasePairs": 0, "invalidTimingRows": 0,
    }
    return {
        "population": pd.DataFrame([population]),
        "activity": pd.DataFrame([{
            "progressionKind": "WeaponRecipe", "targetId": "weapon:a",
            "secondaryId": None, "identityStatus": "identified", "eventCount": 1,
            "boundedEpisodeCount": 1, "unboundedEventCount": 0,
            "singleProgressionEpisodeCount": 0, "multiProgressionEpisodeCount": 1,
            "transactionLinkedCount": 1, "standaloneCount": 0,
            "invalidLinkageCount": 0, "matureEpisodeCount": 1,
            "nextRunCount": 0, "nextRunRate": 0.0, "sameStagePairCount": 0,
        }]),
        "episodes": pd.DataFrame([{
            "progressionKindSet": "EvolutionChoice + WeaponRecipe",
            "episodeType": "multi", "boundaryType": "previousOnly", "episodeCount": 1,
            "eventCount": 8, "sameTargetRepeatedEpisodeCount": 0,
            "matureEpisodeCount": 1, "nextRunCount": 0, "nextRunRateDenominator": 1,
            "nextRunRate": 0.0, "sameStagePairCount": 0,
            "episodeDurationP25": 155.0, "episodeDurationMedian": 155.0,
            "episodeDurationP75": 155.0,
        }]),
        "nextRun": pd.DataFrame([{
            "dimension": "overall", "progressionKind": "All", "boundedEpisodeCount": 1,
            "bothBoundaryEpisodeCount": 0, "previousOnlyEpisodeCount": 1,
            "nextOnlyEpisodeCount": 0, "matureEpisodeCount": 1,
            "rightCensoredEpisodeCount": 0, "nextRunCount": 0,
            "noNextRunWithinWindowCount": 1, "laterNextRunOutsideWindowCount": 0,
            "nextRunRateDenominator": 1, "nextRunRate": 0.0,
            "sameStageCount": 0, "sameStageDenominator": 0, "sameStageRate": None,
            "timeToNextRunP25": None, "timeToNextRunMedian": None,
            "timeToNextRunP75": None, "timeToNextRunP90": None,
            "previousToProgressionP25": 20.0, "previousToProgressionMedian": 20.0,
            "previousToProgressionP75": 20.0, "previousToProgressionP90": 20.0,
        }]),
        "paired": pd.DataFrame(columns=(
            "rowType", "pairScope", "previousOutcome", "nextOutcome", "transitionCount",
            "transitionDenominator", "transitionRatio", "pairedCount",
            "previousElapsedP25", "previousElapsedMedian", "previousElapsedP75",
            "nextElapsedP25", "nextElapsedMedian", "nextElapsedP75", "deltaMean",
            "deltaP25", "deltaMedian", "deltaP75", "previousClears", "previousDeaths",
            "nextClears", "nextDeaths",
        )),
        "coOccurrence": pd.DataFrame([{
            "kindA": "EvolutionChoice", "kindB": "WeaponRecipe",
            "coOccurrenceEpisodeCount": 1, "shareDenominator": 1,
            "shareOfMultiProgressionEpisodes": 1.0,
        }]),
        "stateTransition": pd.DataFrame([{
            "progressionKind": "WeaponRecipe", "targetId": "weapon:a", "secondaryId": None,
            "beforeState": "Locked", "afterState": "Owned", "beforeValue": 0.0,
            "afterValue": 1.0, "eventCount": 1, "boundedEpisodeCount": 1,
            "unboundedEventCount": 0,
        }]),
    }


def test_request_validation_and_stage_less_scope() -> None:
    request = ProgressionNextRunRequest("Test", 0, analysis_as_of_utc=NOW)
    assert request.to_scope(NOW).content_version == 0
    with pytest.raises(ValueError):
        ProgressionNextRunRequest("Staging", 4)
    with pytest.raises(ValueError):
        ProgressionNextRunRequest("Test", 4, previous_run_max_gap_minutes=0)
    with pytest.raises(ValueError):
        ProgressionNextRunRequest(
            "Test", 4,
            progression_occurred_at_utc_start=datetime(2026, 8, 1),
        )


def test_bundle_preserves_structural_and_as_of_observation_definitions() -> None:
    bundle = _assemble_bundle(
        ProgressionNextRunRequest("Test", 4, analysis_as_of_utc=NOW), NOW,
        progression_frames(), estimated_bytes=123,
        thresholds=ProgressionThresholds(0, 0, 0, 0, 0), generated_at_utc=NOW,
    )
    definitions = bundle.metadata.definitions
    assert "independently of linkage windows" in definitions.episode_boundary_semantics
    assert definitions.next_run_observation_semantics == "Observed in telemetry uploaded by analysisAsOfUtc."
    assert bundle.metrics["sample"]["progressionEvents"] == 8
    assert bundle.metrics["nextRunEngagement"]["nextRunWithinWindow"] == {
        "count": 0, "denominator": 1, "ratio": 0.0,
    }
    assert "not equivalent to churn" in bundle.markdown


def test_cost_gate_stops_before_any_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "defence_project_analytics.progression_next_run.dry_run_query",
        lambda *args, **kwargs: SimpleNamespace(total_bytes_processed=200),
    )
    executed: list[bool] = []
    monkeypatch.setattr(
        "defence_project_analytics.progression_next_run.query_dataframe",
        lambda *args, **kwargs: executed.append(True),
    )
    with pytest.raises(RuntimeError, match="no analysis query was executed"):
        analyze_progression_next_run(
            ProgressionNextRunRequest("Test", 4), maximum_total_bytes=100,
            clock=lambda: NOW,
        )
    assert executed == []

