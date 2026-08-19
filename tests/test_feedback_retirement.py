from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from defence_project_analytics.brief.registry import (
    HISTORICAL_ONLY_WARNING_CODES,
    is_evidence_metric_eligible,
    required_metric_keys,
    required_tables,
)
from defence_project_analytics.llm_analysis.anthropic_transport import (
    build_evidence_alias_table,
    build_metric_alias_table,
)
from defence_project_analytics.llm_analysis.errors import AnalysisResponseValidationError
from defence_project_analytics.llm_analysis.validator import validate_response
from defence_project_analytics.metric_registry import (
    MetricLifecycle,
    comparison_metric_keys,
    comparison_summary_specs,
    comparison_table_specs,
    decision_metric_keys,
    evidence_metric_keys,
    known_metric_keys,
    metric_lifecycle,
    target_metric_keys,
)
from defence_project_analytics.post_run_behavior import ANALYSIS_VERSION, SQL_FILES, TABLE_SPECS
from llm_analysis_fixtures import make_package, valid_response


FEEDBACK_KEY = ("postRunBehavior", "feedback", "positiveResponseRate")
ACTIVE_KEY = ("postRunBehavior", "navigation", "shopPresentedRate")


def test_feedback_metrics_are_readable_but_not_new_decision_authority() -> None:
    assert metric_lifecycle(FEEDBACK_KEY) is MetricLifecycle.HISTORICAL_ONLY
    assert FEEDBACK_KEY in known_metric_keys()
    assert FEEDBACK_KEY not in comparison_metric_keys()
    assert FEEDBACK_KEY not in evidence_metric_keys()
    assert FEEDBACK_KEY not in decision_metric_keys()
    assert FEEDBACK_KEY not in target_metric_keys()
    assert ACTIVE_KEY in comparison_metric_keys()
    assert ACTIVE_KEY in evidence_metric_keys()
    assert ACTIVE_KEY in decision_metric_keys()
    assert ACTIVE_KEY in target_metric_keys()


def test_current_post_run_contract_has_no_feedback_output_or_query() -> None:
    assert ANALYSIS_VERSION == "1.1.0"
    assert "feedback" not in SQL_FILES
    assert "post_run_feedback_behavior.csv" not in TABLE_SPECS
    assert "feedback" not in required_metric_keys("postRunBehavior", "1.1.0")
    assert "post_run_feedback_behavior.csv" not in required_tables(
        "postRunBehavior", "1.1.0"
    )
    assert "feedback" in required_metric_keys("postRunBehavior", "1.0.0")
    assert "post_run_feedback_behavior.csv" in required_tables(
        "postRunBehavior", "1.0.0"
    )


def test_new_comparison_registry_excludes_feedback_but_keeps_legacy_registry() -> None:
    assert all(spec[0] != "feedback" for spec in comparison_summary_specs("postRunBehavior"))
    assert all(spec.metric_family != "feedback" for spec in comparison_table_specs("postRunBehavior"))
    assert is_evidence_metric_eligible(*ACTIVE_KEY)
    assert not is_evidence_metric_eligible(*FEEDBACK_KEY)
    assert "LOW_FEEDBACK_SAMPLE" in HISTORICAL_ONLY_WARNING_CODES


def test_anthropic_catalog_uses_only_target_eligible_metrics() -> None:
    aliases = build_metric_alias_table()
    assert set(aliases.canonical_metric_keys) == set(target_metric_keys())
    assert FEEDBACK_KEY not in aliases.canonical_metric_keys


def test_current_validator_rejects_historical_feedback_evidence(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    response = valid_response(package)
    evidence_id = response["observations"][0]["evidenceIds"][0]
    evidence = deepcopy(package.source.evidence_by_id[evidence_id])
    evidence.update({
        "domain": "postRunBehavior",
        "metricFamily": "feedback",
        "metric": "positiveResponseRate",
    })
    source = replace(
        package.source,
        evidence_by_id={**package.source.evidence_by_id, evidence_id: evidence},
    )
    package = replace(package, source=source)

    with pytest.raises(AnalysisResponseValidationError) as error:
        validate_response(package, response)

    assert any(
        issue["code"] == "HISTORICAL_ONLY_EVIDENCE"
        for issue in error.value.issues
    )


def test_anthropic_evidence_aliases_omit_historical_feedback(tmp_path: Path) -> None:
    _, package = make_package(tmp_path)
    document = deepcopy(package.source.evidence)
    historical = deepcopy(document["evidenceItems"][0])
    historical["evidenceId"] = "EV-historical-feedback-test"
    historical.update({
        "domain": "postRunBehavior",
        "metricFamily": "feedback",
        "metric": "positiveResponseRate",
    })
    document["evidenceItems"].append(historical)
    source = replace(
        package.source,
        evidence=document,
        evidence_by_id={
            **package.source.evidence_by_id,
            historical["evidenceId"]: historical,
        },
    )
    aliases = build_evidence_alias_table(replace(package, source=source))
    assert historical["evidenceId"] not in aliases.canonical_evidence_ids

