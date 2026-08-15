from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from defence_project_analytics.reporting.warnings import WeaponThresholds
from defence_project_analytics.weapon_performance import (
    WeaponPerformanceRequest,
    _assemble_bundle,
    analyze_weapon_performance,
)


NOW = datetime(2026, 8, 14, 1, 2, 3, tzinfo=timezone.utc)


def weapon_frames() -> dict[str, pd.DataFrame]:
    return {
        "population": pd.DataFrame([{
            "finalAttempts": 4, "uniquePlayers": 2, "clears": 1, "deaths": 1, "abandons": 2,
            "unrecognizedOutcomes": 0, "unresolvedReleaseRows": 0,
            "detailCandidateSegments": 5, "detailObservedAttempts": 4,
            "mixedContentSegmentsExcluded": 1, "incompleteSegmentsExcluded": 1,
            "legacyUnassessedSegments": 0, "assessedSegments": 4, "completeSegments": 3,
            "detailCandidateAttempts": 4, "detailEligibleAttempts": 2,
            "partiallyCoveredAttempts": 1, "eligibleSegments": 3,
            "weaponSummaryObservedAttempts": 2, "weaponFamilyCount": 1,
            "missingWeaponIdentityRows": 0, "inconsistentDamageShareRows": 0,
        }]),
        "adoption": pd.DataFrame([{
            "weaponFamilyId": "pistol.basic", "eligibleAttempts": 2, "eligibleSegments": 3,
            "startLoadoutAssessedAttempts": 2, "finalStateAssessedAttempts": 2,
            "unmappedAcquisitionRows": 0, "combatObservedSegments": 3,
            "combatObservedAttempts": 2, "segmentInclusionRatio": 1.0,
            "attemptInclusionRatio": 1.0, "startingLoadoutAttempts": 1,
            "startingLoadoutRatio": 0.5, "newWeaponAcquisitionAttempts": 1,
            "acquisitionP25": 10.0, "acquisitionMedian": 20.0, "acquisitionP75": 30.0,
            "upgradeSelectionCount": 2, "evolutionSelectionCount": 0,
            "finalOwnedAttempts": 2, "finalOwnershipRatio": 1.0,
            "finalOwnedClearAttempts": 1, "finalOwnedDeadAttempts": 1,
            "finalOwnedAbandonAttempts": 0, "finalActiveAttempts": 2,
            "finalActiveRatio": 1.0, "finalEvolvedAttempts": 0, "finalEvolvedRatio": 0.0,
            "finalLevelP25": 1.0, "finalLevelMedian": 2.0, "finalLevelP75": 3.0,
        }]),
        "combat": pd.DataFrame([{
            "aggregationLevel": "family", "weaponFamilyId": "pistol.basic", "weaponId": None,
            "eligibleAttempts": 2, "combatObservedAttempts": 2, "combatObservedSegments": 3,
            "combatObservedInstanceSegments": 3, "totalDamage": 300.0,
            "totalBossDamage": 10.0, "totalHits": 30, "damageMean": 150.0,
            "damageP25": 125.0, "damageMedian": 150.0, "damageP75": 175.0,
            "damageP90": 190.0, "bossDamageMean": 5.0, "bossDamageP25": 0.0,
            "bossDamageMedian": 5.0, "bossDamageP75": 10.0, "bossDamageP90": 10.0,
            "hitsMean": 15.0, "hitsP25": 10.0, "hitsMedian": 15.0, "hitsP75": 20.0,
            "hitsP90": 20.0, "damageShareMean": 0.5, "damageShareP25": 0.4,
            "damageShareMedian": 0.5, "damageShareP75": 0.6, "damageShareP90": 0.6,
        }]),
        "dps": pd.DataFrame([{
            "rowType": "metric", "aggregationLevel": "family", "weaponFamilyId": "pistol.basic",
            "weaponId": None, "invalidReason": None, "combatObservedInstanceSegments": 3,
            "instanceSegmentsWithValidDpsSample": 2,
            "dpsCoverageAmongCombatObservedInstanceSegments": 2 / 3,
            "validSampleCount": 4, "invalidSampleCount": 1, "discardedDpsSamples": 0,
            "totalValidDuration": 60.0, "effectiveDpsWeighted": 5.0,
            "effectiveDpsP25": 4.0, "effectiveDpsMedian": 5.0, "effectiveDpsP75": 6.0,
            "mobDpsWeighted": 4.0, "mobDpsP25": 3.0, "mobDpsMedian": 4.0,
            "mobDpsP75": 5.0, "bossDpsWeighted": 1.0, "bossDpsP25": 0.0,
            "bossDpsMedian": 1.0, "bossDpsP75": 2.0, "uptimeObservedCount": 4,
            "uptimeMissingCount": 0, "uptimeP25": 0.4, "uptimeMedian": 0.5,
            "uptimeP75": 0.6, "firstObservedElapsedMedian": 15.0,
        }]),
        "boss": pd.DataFrame([{
            "weaponFamilyId": "pistol.basic", "bossEligibleSegments": 1,
            "familyBossEligibleSegments": 1, "totalDamage": 100.0, "totalBossDamage": 10.0,
            "bossDamageShare": 1.0, "validBossSampleCount": 1, "bossDpsWeighted": 1.0,
            "mobBucketSamples": 0, "mixedBucketSamples": 1, "bossBucketSamples": 0,
        }]),
        "outcome": pd.DataFrame([{
            "associationDefinition": "combatObserved", "weaponFamilyId": "pistol.basic",
            "presentFinalAttempts": 1, "presentClears": 1, "presentDeaths": 0,
            "presentAbandons": 0, "presentUnrecognizedOutcomes": 0, "presentClearRate": 1.0,
            "presentElapsedP25": 30.0, "presentElapsedMedian": 30.0, "presentElapsedP75": 30.0,
            "absentFinalAttempts": 1, "absentClears": 0, "absentDeaths": 1,
            "absentAbandons": 0, "absentUnrecognizedOutcomes": 0, "absentClearRate": 0.0,
            "absentElapsedP25": 10.0, "absentElapsedMedian": 10.0, "absentElapsedP75": 10.0,
            "clearRateDifferencePp": 100.0,
        }]),
        "state": pd.DataFrame([{
            "rowType": "state", "weaponFamilyId": "pistol.basic", "weaponId": "pistol.basic.1",
            "weaponType": "Main", "effectiveLevel": None, "isEvolutionResult": False,
            "isActive": True, "stateRows": 2, "finalOwnedAttempts": 2, "clearAttempts": 1,
            "deadAttempts": 1, "abandonAttempts": 0, "minBaseLevel": 1, "maxBaseLevel": 1,
            "finalStateAssessedAttempts": 2, "finalOwnershipRatio": 1.0,
        }]),
    }


def test_request_validation_and_zero_content_version() -> None:
    assert WeaponPerformanceRequest("Test", "stage1", 0).content_version == 0
    with pytest.raises(ValueError):
        WeaponPerformanceRequest("Staging", "stage1", 2)
    with pytest.raises(ValueError):
        WeaponPerformanceRequest("Test", " ", 2)


def test_bundle_has_strict_coverage_and_unambiguous_names() -> None:
    bundle = _assemble_bundle(
        WeaponPerformanceRequest("Test", "stage1", 2, analysis_as_of_utc=NOW), NOW,
        weapon_frames(), estimated_bytes=123, thresholds=WeaponThresholds(0, 0, 0, 0, 0, 0, 0, 0),
        generated_at_utc=NOW,
    )
    assert bundle.metadata.sample.detail_eligible_attempts == 2
    assert bundle.metadata.quality.dps_coverage_among_combat_observed_instance_segments.ratio == pytest.approx(2 / 3)
    rendered = str(bundle.metrics)
    assert "combatObservedRuns" not in rendered
    assert "combatObservedSegments" in rendered
    assert "dpsCoverageAmongCombatObservedInstanceSegments" in rendered
    assert "especially sensitive to acquisition timing and survivorship" in bundle.markdown


def test_cost_gate_stops_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "defence_project_analytics.weapon_performance.dry_run_query",
        lambda *args, **kwargs: SimpleNamespace(total_bytes_processed=200),
    )
    executed: list[bool] = []
    monkeypatch.setattr(
        "defence_project_analytics.weapon_performance.query_dataframe",
        lambda *args, **kwargs: executed.append(True),
    )
    with pytest.raises(RuntimeError, match="no analysis query was executed"):
        analyze_weapon_performance(
            WeaponPerformanceRequest("Test", "stage1", 2), maximum_total_bytes=100,
            clock=lambda: NOW,
        )
    assert executed == []

