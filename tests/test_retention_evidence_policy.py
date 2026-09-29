from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from defence_project_analytics import cli
from defence_project_analytics.brief.models import DOMAIN_ORDER
from defence_project_analytics.llm_analysis.models import KNOWN_ANALYSES
from defence_project_analytics.metric_registry import (
    GA_IDENTITY_BRIDGE,
    OBSERVED_APP_RETURN,
    OBSERVED_UNINSTALL,
    RUN_RETENTION,
    known_metric_keys,
    metric_authority,
)
from defence_project_analytics.retention_evidence_policy import (
    POLICY_NAME,
    POLICY_SCHEMA_VERSION,
    POLICY_VERSION,
    AuthorityContext,
    AuthorityDecision,
    AuthorityReason,
    ComparisonPlan,
    EvidenceClass,
    HorizonContract,
    RetentionEventLedger,
    RetentionEvidenceEvent,
    RetentionEvidenceFactKind,
    SEQUENCE_POLICIES,
    SequenceRelation,
    SourceCut,
    SourceFinalizationState,
    SubjectLevel,
    is_mature,
    policy_contract,
    resolve_authority,
    serialize_policy_contract,
    validate_policy_contract,
)


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _cut(
    label: str,
    digest: str | None = None,
    state: SourceFinalizationState | None = SourceFinalizationState.FINAL,
) -> SourceCut:
    return SourceCut(label, digest if digest is not None else "a" * 64, state, T0)


def _plan(
    *,
    horizon: HorizonContract | None = None,
    baseline_cut: SourceCut | None = None,
    candidate_cut: SourceCut | None = None,
    environment_compatible: bool | None = True,
) -> ComparisonPlan:
    return ComparisonPlan(
        horizon=horizon or HorizonContract(7, 24),
        minimum_mature_anchors_per_cohort=10,
        maximum_censoring_rate=0.20,
        baseline_mature_anchors=20,
        candidate_mature_anchors=24,
        baseline_censoring_rate=0.10,
        candidate_censoring_rate=0.15,
        backend_compatible=True,
        environment_compatible=environment_compatible,
        content_release_scope_compatible=True,
        horizon_compatible=True,
        upload_grace_compatible=True,
        source_cut_compatible=True,
        baseline_source_cut=baseline_cut or _cut("baseline"),
        candidate_source_cut=candidate_cut or _cut("candidate", "b" * 64),
    )


def _context(*, plan: ComparisonPlan | None = None) -> AuthorityContext:
    return AuthorityContext(
        derived_fixed_horizon=True,
        anchor_end_utc=T0,
        analysis_as_of_utc=T0 + timedelta(days=9),
        complete_source_coverage=True,
        comparison_plan=plan or _plan(),
    )


@pytest.mark.parametrize(
    "kind,evidence_class",
    [
        (
            RetentionEvidenceFactKind.B7_RETURNED,
            EvidenceClass.DIRECT_BEHAVIOR_OBSERVATION,
        ),
        (
            RetentionEvidenceFactKind.B8_RETURNED,
            EvidenceClass.DIRECT_LIFECYCLE_OBSERVATION,
        ),
    ],
)
def test_returned_mature_fixed_horizon_candidate_allows_comparison(
    kind: RetentionEvidenceFactKind, evidence_class: EvidenceClass,
) -> None:
    assert evidence_class in EvidenceClass
    result = resolve_authority(kind, _context())
    assert result.factual.decision is AuthorityDecision.ALLOWED
    assert result.comparison.decision is AuthorityDecision.ALLOWED
    assert result.comparison.reasons == ()
    assert result.decision.decision is AuthorityDecision.DENIED
    assert result.target.decision is AuthorityDecision.DENIED
    assert result.guardrail.decision is AuthorityDecision.DENIED
    assert result.rollback.decision is AuthorityDecision.DENIED


@pytest.mark.parametrize(
    "kind",
    [
        RetentionEvidenceFactKind.B7_MATURE_NO_OBSERVED_RETURN,
        RetentionEvidenceFactKind.B8_MATURE_NO_OBSERVED_RETURN,
    ],
)
def test_mature_no_observed_return_is_conditional_comparison_fact(
    kind: RetentionEvidenceFactKind,
) -> None:
    result = resolve_authority(kind, _context())
    assert result.factual.decision is AuthorityDecision.ALLOWED
    assert result.comparison.decision is AuthorityDecision.ALLOWED


@pytest.mark.parametrize(
    "kind",
    [
        RetentionEvidenceFactKind.B7_RIGHT_CENSORED,
        RetentionEvidenceFactKind.B8_RIGHT_CENSORED,
    ],
)
def test_right_censored_never_becomes_negative_comparison(
    kind: RetentionEvidenceFactKind,
) -> None:
    result = resolve_authority(kind, _context())
    assert result.factual.decision is AuthorityDecision.ALLOWED
    assert result.comparison.decision is AuthorityDecision.DENIED
    assert result.comparison.reasons == (AuthorityReason.RIGHT_CENSORED,)


@pytest.mark.parametrize(
    "kind,expected_reason",
    [
        (
            RetentionEvidenceFactKind.R3D_PROVISIONAL_OBSERVED_UNINSTALL,
            AuthorityReason.PROVISIONAL_SOURCE,
        ),
        (
            RetentionEvidenceFactKind.R3D_FINALIZED_MAPPED_OBSERVED_UNINSTALL,
            AuthorityReason.COMPARISON_NOT_PERMITTED_FOR_FACT,
        ),
        (
            RetentionEvidenceFactKind.R3D_UNMAPPED_OBSERVED_UNINSTALL,
            AuthorityReason.SOURCE_LEVEL_ONLY,
        ),
        (
            RetentionEvidenceFactKind.R3D_AMBIGUOUS_OBSERVED_UNINSTALL,
            AuthorityReason.SOURCE_LEVEL_ONLY,
        ),
    ],
)
def test_uninstall_facts_remain_factual_only(
    kind: RetentionEvidenceFactKind, expected_reason: AuthorityReason,
) -> None:
    result = resolve_authority(kind, _context())
    assert result.factual.decision is AuthorityDecision.ALLOWED
    assert result.comparison.decision is AuthorityDecision.DENIED
    assert expected_reason in result.comparison.reasons


def test_no_app_remove_observed_is_covered_source_fact_only() -> None:
    result = resolve_authority(
        RetentionEvidenceFactKind.R3D_NO_APP_REMOVE_OBSERVED, _context()
    )
    assert result.factual.decision is AuthorityDecision.ALLOWED
    assert result.comparison.decision is AuthorityDecision.DENIED
    assert AuthorityReason.SOURCE_LEVEL_ONLY in result.comparison.reasons


@pytest.mark.parametrize(
    "horizon,reasons",
    [
        (
            HorizonContract(),
            {AuthorityReason.MISSING_HORIZON, AuthorityReason.MISSING_UPLOAD_GRACE},
        ),
        (HorizonContract(None, 24), {AuthorityReason.MISSING_HORIZON}),
        (HorizonContract(7, None), {AuthorityReason.MISSING_UPLOAD_GRACE}),
    ],
)
def test_missing_horizon_or_grace_fails_closed(
    horizon: HorizonContract, reasons: set[AuthorityReason],
) -> None:
    result = resolve_authority(
        RetentionEvidenceFactKind.B7_RETURNED,
        _context(plan=_plan(horizon=horizon)),
    )
    assert result.comparison.decision is AuthorityDecision.DENIED
    assert reasons.issubset(set(result.comparison.reasons))


def test_horizon_only_and_grace_only_are_not_enabled() -> None:
    assert not HorizonContract(7, None).enabled
    assert not HorizonContract(None, 24).enabled
    with pytest.raises(ValueError, match="both horizon and upload grace"):
        is_mature(T0, T0 + timedelta(days=30), HorizonContract(7, None))


def test_missing_source_digest_fails_closed() -> None:
    missing = SourceCut(
        "baseline", None, SourceFinalizationState.FINAL, T0
    )
    result = resolve_authority(
        RetentionEvidenceFactKind.B8_RETURNED,
        _context(plan=_plan(baseline_cut=missing)),
    )
    assert result.comparison.decision is AuthorityDecision.DENIED
    assert AuthorityReason.MISSING_SOURCE_DIGEST in result.comparison.reasons


def test_missing_source_cut_as_of_fails_closed() -> None:
    missing = SourceCut(
        "baseline", "a" * 64, SourceFinalizationState.FINAL, None
    )
    result = resolve_authority(
        RetentionEvidenceFactKind.B8_RETURNED,
        _context(plan=_plan(baseline_cut=missing)),
    )
    assert result.comparison.decision is AuthorityDecision.DENIED
    assert AuthorityReason.MISSING_SOURCE_CUT_AS_OF in result.comparison.reasons


def test_incompatible_environment_fails_closed() -> None:
    result = resolve_authority(
        RetentionEvidenceFactKind.B7_RETURNED,
        _context(plan=_plan(environment_compatible=False)),
    )
    assert result.comparison.decision is AuthorityDecision.DENIED
    assert AuthorityReason.INCOMPATIBLE_ENVIRONMENT in result.comparison.reasons


def test_unknown_environment_compatibility_fails_closed() -> None:
    result = resolve_authority(
        RetentionEvidenceFactKind.B7_RETURNED,
        _context(plan=_plan(environment_compatible=None)),
    )
    assert AuthorityReason.UNKNOWN_ENVIRONMENT_COMPATIBILITY in (
        result.comparison.reasons
    )


def test_same_as_of_with_different_digest_is_a_different_source_cut() -> None:
    left = _cut("same", "a" * 64)
    right = _cut("same", "b" * 64)
    assert left.analysis_as_of_utc == right.analysis_as_of_utc
    assert left.identity != right.identity
    assert left != right


def test_uninstall_after_comparison_horizon_remains_in_event_ledger() -> None:
    inside = RetentionEvidenceEvent(
        RetentionEvidenceFactKind.B8_RETURNED, T0 + timedelta(days=2)
    )
    after = RetentionEvidenceEvent(
        RetentionEvidenceFactKind.R3D_FINALIZED_MAPPED_OBSERVED_UNINSTALL,
        T0 + timedelta(days=20),
    )
    ledger = RetentionEventLedger((after, inside))
    view = ledger.comparison_window(T0, HorizonContract(7, 24))
    assert ledger.events == (inside, after)
    assert view == (inside,)
    assert after in ledger.events


@pytest.mark.parametrize(
    "relation",
    [
        SequenceRelation.UNINSTALL_THEN_RETURN,
        SequenceRelation.RETURN_THEN_UNINSTALL,
        SequenceRelation.NEW_ATTEMPT_WITHOUT_LIFECYCLE_RETURN,
        SequenceRelation.SAME_TIME_CROSS_SOURCE,
    ],
)
def test_supported_sequences_are_not_telemetry_conflicts(
    relation: SequenceRelation,
) -> None:
    assert SEQUENCE_POLICIES[relation].conflict is False


def test_b7_return_without_b8_return_is_explicitly_not_a_conflict() -> None:
    policy = SEQUENCE_POLICIES[
        SequenceRelation.NEW_ATTEMPT_WITHOUT_LIFECYCLE_RETURN
    ]
    assert not policy.conflict
    assert "continuously foregrounded" in policy.factual_interpretation


def test_unknown_evidence_kind_fails_every_authority_dimension_closed() -> None:
    result = resolve_authority("UnknownEvidenceKind", _context())
    for name in (
        "factual", "comparison", "decision", "target", "guardrail", "rollback",
    ):
        dimension = getattr(result, name)
        assert dimension.decision is AuthorityDecision.DENIED
        assert dimension.reasons == (AuthorityReason.UNSUPPORTED_FACT_KIND,)


def test_maturity_is_exact_and_requires_complete_pair() -> None:
    horizon = HorizonContract(7, 24)
    assert not is_mature(T0, T0 + timedelta(days=7, hours=23, minutes=59), horizon)
    assert is_mature(T0, T0 + timedelta(days=8), horizon)


def test_policy_serialization_is_versioned_and_deterministic() -> None:
    first = serialize_policy_contract()
    second = serialize_policy_contract()
    assert first == second
    payload = json.loads(first)
    assert payload["schemaVersion"] == POLICY_SCHEMA_VERSION
    assert payload["policyName"] == POLICY_NAME
    assert payload["policyVersion"] == POLICY_VERSION
    assert payload["boundaries"]["eventLedgerIsUnboundedByComparisonHorizon"]
    assert payload["boundaries"]["derivedMetricsRegistered"] is False
    assert payload["boundaries"]["c1C2IntegrationEnabled"] is False


def test_contract_validator_passes_and_unknown_values_fail() -> None:
    assert validate_policy_contract().ready
    altered = policy_contract()
    altered["evidenceClasses"] = [*altered["evidenceClasses"], "UnknownClass"]
    report = validate_policy_contract(altered)
    assert not report.ready
    assert "EVIDENCE_CLASSES" in {item.code for item in report.findings}


def test_cli_validator_is_local_and_never_creates_bigquery_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "get_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("R4-A validator must not create a BigQuery client")
        ),
    )
    assert cli.main(["validate-r4a-contracts"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    assert payload["policyName"] == POLICY_NAME


def test_existing_retention_source_registry_authority_is_unchanged() -> None:
    domains = {
        RUN_RETENTION,
        OBSERVED_APP_RETURN,
        GA_IDENTITY_BRIDGE,
        OBSERVED_UNINSTALL,
    }
    keys = {key for key in known_metric_keys() if key[0] in domains}
    assert keys
    for key in keys:
        authority = metric_authority(key)
        assert authority.known_readable
        assert authority.evidence_eligible
        assert not authority.comparison_eligible
        assert not authority.decision_eligible
        assert not authority.target_eligible
    # R4-B may add derived metrics; the R4-A regression is that source authority
    # remains factual-only.
    assert any(key[0] == "retentionEvidence" for key in known_metric_keys())


def test_r4c_registers_retention_evidence_with_c1_and_c2() -> None:
    assert "retentionEvidence" in DOMAIN_ORDER
    assert "retentionEvidence" in KNOWN_ANALYSES


def test_evidence_and_subject_enums_are_categorical_strings() -> None:
    assert [item.value for item in EvidenceClass] == [
        "DirectBehaviorObservation",
        "DirectLifecycleObservation",
        "DirectRemovalObservation",
        "BoundedAbsenceObservation",
        "CensoredObservation",
        "SourceQualityObservation",
    ]
    assert [item.value for item in SubjectLevel] == [
        "Anchor", "Profile", "AppInstance", "Source",
    ]
    with pytest.raises(TypeError):
        _ = EvidenceClass.DIRECT_BEHAVIOR_OBSERVATION < (
            EvidenceClass.DIRECT_LIFECYCLE_OBSERVATION
        )
