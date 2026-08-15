from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from defence_project_analytics.reporting.warnings import UpgradeThresholds
from defence_project_analytics.upgrade_choice import (
    UpgradeChoiceRequest,
    _assemble_bundle,
    _segment_selection_count_matches,
    analyze_upgrade_choice,
)


NOW = datetime(2026, 8, 15, 1, 2, 3, tzinfo=timezone.utc)


def upgrade_frames() -> dict[str, pd.DataFrame]:
    return {
        "population": pd.DataFrame([{
            "finalAttempts": 4, "uniquePlayers": 2, "clears": 1, "deaths": 1,
            "abandons": 2, "unrecognizedOutcomes": 0, "detailCandidateSegments": 5,
            "assessedSegments": 4, "transportEligibleSegments": 3,
            "mixedContentSegmentsExcluded": 1, "incompleteSegmentsExcluded": 1,
            "legacyUnassessedSegments": 0, "choiceEligibleSegments": 2,
            "selectionCountMismatchSegments": 1, "noSelectionExposures": 1,
            "multipleSelectionExposures": 0, "legacyUnlinkedSelections": 0,
            "selectionsWithoutExposureMatch": 0, "droppedExposures": 0,
            "omittedCandidates": 0, "detailCandidateAttempts": 4,
            "fullyChoiceCoveredAttempts": 2, "partiallyCoveredAttempts": 1,
            "observedExposures": 3, "truncatedExposures": 0, "malformedExposures": 0,
            "completeExposures": 3, "linkedSelections": 2,
            "missingCandidateIdentityRows": 0, "unrecognizedCategoryRows": 0,
            "candidateCount": 2,
        }]),
        "exposure": pd.DataFrame([{
            "candidateKey": "NewWeapon:new_weapon:a", "categoryCode": 0,
            "category": "NewWeapon", "contentId": "new_weapon:a", "weaponFamilyId": None,
            "grantWeaponId": "a", "cardType": 1, "weaponSourceType": 1,
            "allObservedExposureCount": 3, "completeExposureCount": 3,
            "linkedSelectionCount": 2, "selectionCountIncludingLegacy": 2,
            "pickRateCount": 2, "pickRateDenominator": 3, "pickRate": 2 / 3,
            "exposureElapsedP25": 10.0, "exposureElapsedMedian": 20.0,
            "exposureElapsedP75": 30.0,
        }]),
        "selection": pd.DataFrame([
            {"rowType": "candidate", "candidateKey": "NewWeapon:new_weapon:a",
             "dimension": None, "dimensionValue": None, "exposureCount": 3,
             "selectionCount": 2, "pickRateCount": 2, "pickRateDenominator": 3,
             "pickRate": 2 / 3, "exposedAttempts": 2, "selectedAttempts": 1,
             "repeatedExposureAttempts": 1, "repeatedSelectionAttempts": 0},
            {"rowType": "breakdown", "candidateKey": "NewWeapon:new_weapon:a",
             "dimension": "candidateIndex", "dimensionValue": "0", "exposureCount": 3,
             "selectionCount": 2, "pickRateCount": 2, "pickRateDenominator": 3,
             "pickRate": 2 / 3, "exposedAttempts": None, "selectedAttempts": None,
             "repeatedExposureAttempts": None, "repeatedSelectionAttempts": None},
        ]),
        "context": pd.DataFrame([
            {"rowType": "context", "candidateKey": "NewWeapon:new_weapon:a",
             "dimension": "playerLevel", "dimensionValue": "2",
             "attributionSource": "snapshot", "exposureCount": 3, "selectionCount": 2,
             "pickRateCount": 2, "pickRateDenominator": 3, "pickRate": 2 / 3,
             "approximateContextRows": None, "staleContextRows": None,
             "missingContextRows": None, "snapshotLagP50": None,
             "snapshotLagP75": None, "snapshotLagP90": None},
            {"rowType": "quality", "candidateKey": None, "dimension": None,
             "dimensionValue": None, "attributionSource": None, "exposureCount": None,
             "selectionCount": None, "pickRateCount": None, "pickRateDenominator": None,
             "pickRate": None, "approximateContextRows": 3, "staleContextRows": 0,
             "missingContextRows": 0, "snapshotLagP50": 1.0,
             "snapshotLagP75": 2.0, "snapshotLagP90": 3.0},
        ]),
        "headToHead": pd.DataFrame([{
            "candidateA": "Global:a", "candidateB": "Reward:b", "coExposureCount": 30,
            "aSelectedCount": 20, "bSelectedCount": 5, "otherSelectedCount": 4,
            "unresolvedSelectionCount": 1, "aOverallSelectionShareCount": 20,
            "aOverallSelectionShareDenominator": 30, "aOverallSelectionShare": 2 / 3,
            "bOverallSelectionShareCount": 5, "bOverallSelectionShareDenominator": 30,
            "bOverallSelectionShare": 1 / 6, "aConditionalPreferenceCount": 20,
            "aConditionalPreferenceDenominator": 25, "aConditionalPreference": 0.8,
            "bConditionalPreferenceCount": 5, "bConditionalPreferenceDenominator": 25,
            "bConditionalPreference": 0.2,
        }]),
        "outcome": pd.DataFrame([{
            "candidateKey": "NewWeapon:new_weapon:a", "selectedAttempts": 20,
            "selectedClears": 10, "selectedDeaths": 10, "selectedAbandons": 0,
            "selectedUnrecognizedOutcomes": 0, "selectedClearRate": 0.5,
            "selectedElapsedP25": 30.0, "selectedElapsedMedian": 40.0,
            "selectedElapsedP75": 50.0, "selectedRemainingFromExposureP25": 20.0,
            "selectedRemainingFromExposureMedian": 30.0,
            "selectedRemainingFromExposureP75": 40.0,
            "selectedRemainingOutcomeSecondsP25": 10.0,
            "selectedRemainingOutcomeSecondsMedian": 20.0,
            "selectedRemainingOutcomeSecondsP75": 30.0,
            "exposedNotSelectedAttempts": 20, "alternativeSelectedAttempts": 12,
            "noSelectionOnlyAttempts": 8, "exposedNotSelectedClears": 6,
            "exposedNotSelectedDeaths": 14, "exposedNotSelectedAbandons": 0,
            "exposedNotSelectedUnrecognizedOutcomes": 0,
            "exposedNotSelectedClearRate": 0.3, "exposedNotSelectedElapsedP25": 20.0,
            "exposedNotSelectedElapsedMedian": 30.0, "exposedNotSelectedElapsedP75": 40.0,
            "exposedNotSelectedRemainingFromExposureP25": 10.0,
            "exposedNotSelectedRemainingFromExposureMedian": 20.0,
            "exposedNotSelectedRemainingFromExposureP75": 30.0,
            "clearRateDifferencePp": 20.0, "invalidOutcomeTimeRows": 0,
        }]),
    }


def test_request_validation_and_zero_content_version() -> None:
    assert UpgradeChoiceRequest("Test", "stage1", 0).content_version == 0
    with pytest.raises(ValueError):
        UpgradeChoiceRequest("Staging", "stage1", 2)
    with pytest.raises(ValueError):
        UpgradeChoiceRequest("Test", " ", 2)


def test_outcome_composition_and_no_selection_are_explicit() -> None:
    bundle = _assemble_bundle(
        UpgradeChoiceRequest("Test", "stage1", 2, analysis_as_of_utc=NOW), NOW,
        upgrade_frames(), estimated_bytes=123,
        thresholds=UpgradeThresholds(0, 0, 0, 0, 0, 0.7, 0.2, 0.7, 0.1, 30, 0),
        generated_at_utc=NOW,
    )
    association = bundle.metrics["outcomeAssociation"]
    assert association["exposedNotSelectedAttempts"] == 20
    assert association["alternativeSelectedAttempts"] == 12
    assert association["noSelectionOnlyAttempts"] == 8
    assert bundle.metadata.quality.no_selection_exposures == 1
    assert bundle.metrics["exposure"]["topCandidateExposure"][0]["pickRateDenominator"] == 3
    assert bundle.metrics["exposure"]["topCandidateExposure"][0]["pickRateCount"] == 2
    assert "mutually exclusive" in bundle.markdown


def test_resume_selection_counts_are_compared_per_segment_not_attempt() -> None:
    expected = {("Test", "segment-1"): 3, ("Test", "segment-2"): 2}
    observed = {("Test", "segment-1"): 3, ("Test", "segment-2"): 2}
    assert _segment_selection_count_matches(expected, observed) == {
        ("Test", "segment-1"): True,
        ("Test", "segment-2"): True,
    }
    assert not _segment_selection_count_matches(
        expected, {("Test", "segment-1"): 3, ("Test", "segment-2"): 5}
    )[("Test", "segment-2")]


def test_outcome_composition_invariant_is_enforced() -> None:
    frames = upgrade_frames()
    frames["outcome"].loc[0, "noSelectionOnlyAttempts"] = 7
    with pytest.raises(RuntimeError, match="composition invariant"):
        _assemble_bundle(
            UpgradeChoiceRequest("Test", "stage1", 2, analysis_as_of_utc=NOW), NOW,
            frames, estimated_bytes=0, thresholds=UpgradeThresholds(), generated_at_utc=NOW,
        )


def test_cost_gate_stops_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "defence_project_analytics.upgrade_choice.dry_run_query",
        lambda *args, **kwargs: SimpleNamespace(total_bytes_processed=200),
    )
    executed: list[bool] = []
    monkeypatch.setattr(
        "defence_project_analytics.upgrade_choice.query_dataframe",
        lambda *args, **kwargs: executed.append(True),
    )
    with pytest.raises(RuntimeError, match="no analysis query was executed"):
        analyze_upgrade_choice(
            UpgradeChoiceRequest("Test", "stage1", 2), maximum_total_bytes=100,
            clock=lambda: NOW,
        )
    assert executed == []
