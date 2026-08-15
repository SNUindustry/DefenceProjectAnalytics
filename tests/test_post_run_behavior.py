from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from defence_project_analytics.post_run_behavior import (
    PostRunBehaviorRequest,
    _assemble_bundle,
    analyze_post_run_behavior,
)
from defence_project_analytics.reporting.warnings import PostRunThresholds


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def post_run_frames() -> dict[str, pd.DataFrame]:
    population = {
        "anchorFinalRuns": 6, "uniquePlayers": 2, "clears": 0, "deaths": 6,
        "abandons": 0, "unrecognizedOutcomes": 0, "linkageEligibleWindows": 6,
        "matureWindows": 6, "rightCensoredWindows": 0, "feedbackExposedWindows": 6,
        "feedbackRespondedWindows": 4, "shopPresentedWindows": 3,
        "shopUserNavigatedWindows": 2, "commerceAttemptWindows": 1,
        "committedSuccessWindows": 2, "progressionWindows": 1,
        "nextRunWithinWindowWindows": 4, "missingPlayerIdentityAnchors": 0,
        "missingAnchorEndRows": 0, "resolvedWindows": 4, "matureNoNextWindows": 2,
        "laterNextRunOutsideWindowWindows": 0, "windowsWithObservedAction": 6,
        "windowsWithUserAction": 5, "windowsWithoutObservedAction": 0,
        "windowsWithoutLobbyActivityObserved": 0, "feedbackLinkMismatchRows": 0,
        "feedbackOutsideWindowRows": 0, "conflictingFeedbackResponseWindows": 0,
        "physicalLobbyRows": 10, "dedupedLobbyRows": 9, "physicalShopRows": 4,
        "dedupedShopRows": 4, "physicalTransactionRows": 4,
        "dedupedTransactionRows": 4, "physicalProgressionRows": 1,
        "dedupedProgressionRows": 1, "physicalIapRows": 0, "dedupedIapRows": 0,
        "crossContentActions": 0, "crossReleaseActions": 0,
        "offerSelectionWithoutExposure": 0, "commerceAttemptWithoutShopSelection": 0,
        "transactionAttemptWithoutResult": 0, "transactionResultWithoutObservedAttempt": 1,
        "committedSuccessWithoutObservedAttemptResults": 1,
        "committedSuccessWithoutObservedAttemptWindows": 1,
        "funFeedbackRewardsExcluded": 4, "progressionTransactionsExcluded": 1,
        "sameTimestampActionGroups": 1, "unrecognizedActionRows": 0,
        "nextOutcomePendingWindows": 0,
    }
    feedback = pd.DataFrame([
        {"anchorOutcome": "Dead", "feedbackCohort": "PositiveResponse", "windowCount": 1,
         "shopPresentedCount": 1, "shopPresentedRate": 1.0, "shopUserNavigatedCount": 1,
         "shopUserNavigatedRate": 1.0, "offerSelectedCount": 0, "offerSelectedRate": 0.0,
         "commerceAttemptCount": 0, "commerceAttemptRate": 0.0,
         "committedSuccessCount": 1, "committedSuccessWindowRate": 1.0,
         "progressionCount": 0, "progressionRate": 0.0, "nextRunCount": 0,
         "nextRunRate": 0.0, "timeToNextRunP25": None,
         "timeToNextRunMedian": None, "timeToNextRunP75": None},
        {"anchorOutcome": "Dead", "feedbackCohort": "NegativeResponse", "windowCount": 3,
         "shopPresentedCount": 1, "shopPresentedRate": 1 / 3,
         "shopUserNavigatedCount": 0, "shopUserNavigatedRate": 0.0,
         "offerSelectedCount": 0, "offerSelectedRate": 0.0,
         "commerceAttemptCount": 0, "commerceAttemptRate": 0.0,
         "committedSuccessCount": 0, "committedSuccessWindowRate": 0.0,
         "progressionCount": 1, "progressionRate": 1 / 3, "nextRunCount": 2,
         "nextRunRate": 2 / 3, "timeToNextRunP25": 20.0,
         "timeToNextRunMedian": 30.0, "timeToNextRunP75": 40.0},
    ])
    navigation = pd.DataFrame([{
        "anchorOutcome": "Dead", "dimension": "Shop", "viewedWindows": 3,
        "viewedDenominator": 6, "viewedRate": 0.5, "userNavigatedWindows": 2,
        "userNavigatedDenominator": 6, "userNavigatedRate": 1 / 3,
        "initialViewedWindows": 0, "programmaticViewedWindows": 0, "eventCount": 3,
    }, {
        "anchorOutcome": "Dead", "dimension": "ShopSection:Main", "viewedWindows": 3,
        "viewedDenominator": 6, "viewedRate": 0.5, "userNavigatedWindows": 0,
        "userNavigatedDenominator": 6, "userNavigatedRate": 0.0,
        "initialViewedWindows": 0, "programmaticViewedWindows": 0, "eventCount": 3,
    }])
    shop = pd.DataFrame([{
        "rowType": "funnel", "anchorOutcome": "Dead", "dimension": "AllCommerce",
        "matureWindows": 6, "shopPresentedWindows": 3, "shopUserNavigatedWindows": 2,
        "offerExposedWindows": 2, "offerSelectedWindows": 1,
        "commerceAttemptObservedWindows": 1, "observedAttemptLinkedSucceededWindows": 1,
        "committedSuccessWindows": 2,
    }, {
        "rowType": "commerce", "anchorOutcome": "Dead", "dimension": "CommerceShop",
        "matureWindows": 6, "observedAttemptCount": 1,
        "linkedSucceededAttemptCount": 1, "observedAttemptSuccessRate": 1.0,
        "committedSuccessWindows": 2, "committedSuccessWindowRate": 1 / 3,
        "committedSuccessWithoutObservedAttemptResults": 1,
        "committedSuccessWithoutObservedAttemptWindows": 1,
    }])
    sequence = pd.DataFrame([
        {"rowType": "firstObserved", "anchorOutcome": "Dead", "fromAction": "FeedbackExposure",
         "toAction": None, "actionCount": 6, "denominator": 6, "ratio": 1.0},
        {"rowType": "firstUser", "anchorOutcome": "Dead", "fromAction": "Shop",
         "toAction": None, "actionCount": 2, "denominator": 6, "ratio": 1 / 3},
        {"rowType": "transition", "anchorOutcome": "Dead", "fromAction": "Shop",
         "toAction": "Battle", "actionCount": 2, "denominator": 2, "ratio": 1.0},
    ])
    return {
        "population": pd.DataFrame([population]), "feedback": feedback,
        "navigation": navigation, "shopCommerce": shop,
        "progression": pd.DataFrame([{
            "anchorOutcome": "Dead", "progressionKind": "WeaponRecipe", "windowCount": 6,
            "progressionWindows": 1, "progressionEventCount": 1, "progressionRate": 1 / 6,
            "timeToProgressionP25": 5.0, "timeToProgressionMedian": 5.0,
            "timeToProgressionP75": 5.0,
        }]),
        "nextRun": pd.DataFrame([{
            "anchorOutcome": "Dead", "matureWindows": 6, "rightCensoredWindows": 0,
            "nextRunWithinWindow": 4, "noNextRunWithinWindow": 2,
            "laterNextRunOutsideWindow": 0, "nextRunRateDenominator": 6,
            "nextRunRate": 2 / 3, "sameStageRetryCount": 4,
            "sameStageRetryDenominator": 4, "sameStageRetryRate": 1.0,
            "sameContentNextRunCount": 4, "sameContentNextRunDenominator": 4,
            "sameContentNextRunRate": 1.0, "nextClears": 0, "nextDeaths": 4,
            "nextAbandons": 0, "nextOutcomePending": 0, "timeToNextRunP25": 20.0,
            "timeToNextRunMedian": 30.0, "timeToNextRunP75": 40.0,
            "timeToNextRunP90": 50.0,
        }]),
        "actionSequence": sequence,
    }


def test_request_validation_and_optional_stage_scope() -> None:
    request = PostRunBehaviorRequest("Test", 0, analysis_as_of_utc=NOW)
    assert request.to_scope(NOW).stage_key is None
    with pytest.raises(ValueError):
        PostRunBehaviorRequest("Staging", 4)
    with pytest.raises(ValueError):
        PostRunBehaviorRequest("Test", 4, post_run_max_gap_minutes=0)
    with pytest.raises(ValueError):
        PostRunBehaviorRequest("Test", 4, final_outcome="None")


def test_shop_presentation_and_user_navigation_are_independent() -> None:
    bundle = _assemble_bundle(
        PostRunBehaviorRequest("Test", 4, analysis_as_of_utc=NOW), NOW,
        post_run_frames(), estimated_bytes=10, thresholds=PostRunThresholds(),
        generated_at_utc=NOW,
    )
    navigation = bundle.metrics["navigation"]
    assert navigation["shopPresentedRate"] == {"count": 3, "denominator": 6, "ratio": 0.5}
    assert navigation["shopUserNavigatedRate"]["count"] == 2
    section = next(row for row in navigation["topNavigation"] if row["dimension"] == "ShopSection:Main")
    assert section["userNavigatedWindows"] == 0
    assert "section presentation alone" in bundle.metadata.definitions.notes[0]


def test_attempt_funnel_and_durable_success_presence_are_separate() -> None:
    bundle = _assemble_bundle(
        PostRunBehaviorRequest("Test", 4, analysis_as_of_utc=NOW), NOW,
        post_run_frames(), estimated_bytes=10, thresholds=PostRunThresholds(),
        generated_at_utc=NOW,
    )
    commerce = bundle.metrics["commerce"]
    assert commerce["observedAttemptSuccessRate"] == {"count": 1, "denominator": 1, "ratio": 1.0}
    assert commerce["committedSuccessWindowRate"] == {"count": 2, "denominator": 6, "ratio": 1 / 3}
    assert commerce["committedSuccessWithoutObservedAttemptWindows"] == 1
    assert "Attempt availability does not change the durable Result fact" in bundle.markdown
    assert "orphan" not in bundle.markdown.casefold()
    assert "funnel failure" not in bundle.markdown.casefold()


def test_cost_gate_stops_before_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "defence_project_analytics.post_run_behavior.dry_run_query",
        lambda *args, **kwargs: SimpleNamespace(total_bytes_processed=200),
    )
    executed: list[bool] = []
    monkeypatch.setattr(
        "defence_project_analytics.post_run_behavior.query_dataframe",
        lambda *args, **kwargs: executed.append(True),
    )
    with pytest.raises(RuntimeError, match="no analysis query was executed"):
        analyze_post_run_behavior(
            PostRunBehaviorRequest("Test", 4), maximum_total_bytes=100, clock=lambda: NOW,
        )
    assert executed == []
