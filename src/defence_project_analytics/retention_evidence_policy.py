"""R4-A Retention Evidence Policy Contract.

This module defines authority and provenance rules only.  It does not load
telemetry, combine B-7/B-8/R3-D populations, or integrate with C-1/C-2.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


POLICY_SCHEMA_VERSION = "1.0.0"
POLICY_NAME = "RetentionEvidencePolicy"
POLICY_VERSION = "1.0.0"


class EvidenceClass(Enum):
    """Categorical evidence meaning; declaration order is not a ranking."""

    DIRECT_BEHAVIOR_OBSERVATION = "DirectBehaviorObservation"
    DIRECT_LIFECYCLE_OBSERVATION = "DirectLifecycleObservation"
    DIRECT_REMOVAL_OBSERVATION = "DirectRemovalObservation"
    BOUNDED_ABSENCE_OBSERVATION = "BoundedAbsenceObservation"
    CENSORED_OBSERVATION = "CensoredObservation"
    SOURCE_QUALITY_OBSERVATION = "SourceQualityObservation"


class SubjectLevel(str, Enum):
    ANCHOR = "Anchor"
    PROFILE = "Profile"
    APP_INSTANCE = "AppInstance"
    SOURCE = "Source"


class AuthorityDecision(str, Enum):
    ALLOWED = "Allowed"
    DENIED = "Denied"


class SourceFinalizationState(str, Enum):
    FINAL = "Final"
    PROVISIONAL = "Provisional"


class RetentionEvidenceFactKind(str, Enum):
    B7_RETURNED = "B7Returned"
    B7_MATURE_NO_OBSERVED_RETURN = "B7MatureNoObservedReturn"
    B7_RIGHT_CENSORED = "B7RightCensored"
    B8_RETURNED = "B8Returned"
    B8_MATURE_NO_OBSERVED_RETURN = "B8MatureNoObservedReturn"
    B8_RIGHT_CENSORED = "B8RightCensored"
    R3D_FINALIZED_MAPPED_OBSERVED_UNINSTALL = (
        "R3DFinalizedMappedObservedUninstall"
    )
    R3D_UNMAPPED_OBSERVED_UNINSTALL = "R3DUnmappedObservedUninstall"
    R3D_AMBIGUOUS_OBSERVED_UNINSTALL = "R3DAmbiguousObservedUninstall"
    R3D_PROVISIONAL_OBSERVED_UNINSTALL = "R3DProvisionalObservedUninstall"
    R3D_NO_APP_REMOVE_OBSERVED = "R3DNoAppRemoveObserved"


class ComparisonCapability(str, Enum):
    DERIVED_MATURE_FIXED_HORIZON_ONLY = "DerivedMatureFixedHorizonOnly"
    DENIED = "Denied"


class SequenceRelation(str, Enum):
    UNINSTALL_THEN_RETURN = "ObservedUninstallThenLaterReturn"
    RETURN_THEN_UNINSTALL = "ReturnThenObservedUninstall"
    NEW_ATTEMPT_WITHOUT_LIFECYCLE_RETURN = "NewAttemptWithoutLifecycleReturn"
    SAME_TIME_CROSS_SOURCE = "SameTimeCrossSourceObservation"


class AuthorityReason(str, Enum):
    COMPARISON_NOT_PERMITTED_FOR_FACT = "ComparisonNotPermittedForFact"
    NOT_DERIVED_FIXED_HORIZON = "NotDerivedFixedHorizon"
    MISSING_COMPARISON_PLAN = "MissingComparisonPlan"
    MISSING_HORIZON = "MissingHorizon"
    MISSING_UPLOAD_GRACE = "MissingUploadGrace"
    MISSING_ANCHOR_END = "MissingAnchorEnd"
    MISSING_ANALYSIS_AS_OF = "MissingAnalysisAsOf"
    INSUFFICIENT_MATURITY = "InsufficientMaturity"
    UNKNOWN_SOURCE_COVERAGE = "UnknownSourceCoverage"
    INCOMPLETE_SOURCE_COVERAGE = "IncompleteSourceCoverage"
    MISSING_MINIMUM_MATURE_ANCHORS = "MissingMinimumMatureAnchors"
    MISSING_MAXIMUM_CENSORING_RATE = "MissingMaximumCensoringRate"
    MISSING_COHORT_MATURITY = "MissingCohortMaturity"
    INSUFFICIENT_MATURE_ANCHORS = "InsufficientMatureAnchors"
    MISSING_CENSORING_RATE = "MissingCensoringRate"
    CENSORING_RATE_EXCEEDED = "CensoringRateExceeded"
    UNKNOWN_BACKEND_COMPATIBILITY = "UnknownBackendCompatibility"
    INCOMPATIBLE_BACKEND = "IncompatibleBackend"
    UNKNOWN_ENVIRONMENT_COMPATIBILITY = "UnknownEnvironmentCompatibility"
    INCOMPATIBLE_ENVIRONMENT = "IncompatibleEnvironment"
    UNKNOWN_SCOPE_COMPATIBILITY = "UnknownScopeCompatibility"
    INCOMPATIBLE_SCOPE = "IncompatibleScope"
    UNKNOWN_HORIZON_COMPATIBILITY = "UnknownHorizonCompatibility"
    INCOMPATIBLE_HORIZON = "IncompatibleHorizon"
    UNKNOWN_UPLOAD_GRACE_COMPATIBILITY = "UnknownUploadGraceCompatibility"
    INCOMPATIBLE_UPLOAD_GRACE = "IncompatibleUploadGrace"
    UNKNOWN_SOURCE_CUT_COMPATIBILITY = "UnknownSourceCutCompatibility"
    INCOMPATIBLE_SOURCE_CUT = "IncompatibleSourceCut"
    MISSING_SOURCE_CUT = "MissingSourceCut"
    MISSING_SOURCE_ARTIFACT_IDENTITY = "MissingSourceArtifactIdentity"
    MISSING_SOURCE_DIGEST = "MissingSourceDigest"
    INVALID_SOURCE_DIGEST = "InvalidSourceDigest"
    MISSING_SOURCE_CUT_AS_OF = "MissingSourceCutAsOf"
    UNKNOWN_SOURCE_FINALIZATION = "UnknownSourceFinalization"
    PROVISIONAL_SOURCE = "ProvisionalSource"
    RIGHT_CENSORED = "RightCensored"
    SOURCE_LEVEL_ONLY = "SourceLevelOnly"
    UNSUPPORTED_FACT_KIND = "UnsupportedFactKind"
    UNSUPPORTED_EVIDENCE_CLASS = "UnsupportedEvidenceClass"
    UNSUPPORTED_SUBJECT_LEVEL = "UnsupportedSubjectLevel"
    DECISION_AUTHORITY_DENIED = "DecisionAuthorityDenied"
    TARGET_AUTHORITY_DENIED = "TargetAuthorityDenied"
    GUARDRAIL_AUTHORITY_DENIED = "GuardrailAuthorityDenied"
    ROLLBACK_AUTHORITY_DENIED = "RollbackAuthorityDenied"


@dataclass(frozen=True, slots=True)
class AuthorityDimensionResolution:
    decision: AuthorityDecision
    reasons: tuple[AuthorityReason, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.decision, AuthorityDecision):
            raise TypeError("decision must be an AuthorityDecision")
        if any(not isinstance(item, AuthorityReason) for item in self.reasons):
            raise TypeError("reasons must contain AuthorityReason values")
        if self.decision is AuthorityDecision.ALLOWED and self.reasons:
            raise ValueError("Allowed authority cannot contain denial reasons")
        if self.decision is AuthorityDecision.DENIED and not self.reasons:
            raise ValueError("Denied authority must contain at least one reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": [item.value for item in self.reasons],
        }


@dataclass(frozen=True, slots=True)
class AuthorityResolution:
    factual: AuthorityDimensionResolution
    comparison: AuthorityDimensionResolution
    decision: AuthorityDimensionResolution
    target: AuthorityDimensionResolution
    guardrail: AuthorityDimensionResolution
    rollback: AuthorityDimensionResolution

    def to_dict(self) -> dict[str, Any]:
        return {
            "factual": self.factual.to_dict(),
            "comparison": self.comparison.to_dict(),
            "decision": self.decision.to_dict(),
            "target": self.target.to_dict(),
            "guardrail": self.guardrail.to_dict(),
            "rollback": self.rollback.to_dict(),
        }


def _allowed() -> AuthorityDimensionResolution:
    return AuthorityDimensionResolution(AuthorityDecision.ALLOWED)


def _denied(*reasons: AuthorityReason) -> AuthorityDimensionResolution:
    return AuthorityDimensionResolution(
        AuthorityDecision.DENIED, _unique_reasons(reasons)
    )


def _unique_reasons(
    reasons: tuple[AuthorityReason, ...] | list[AuthorityReason],
) -> tuple[AuthorityReason, ...]:
    return tuple(dict.fromkeys(reasons))


def _aware_utc(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class HorizonContract:
    horizon_days: int | None = None
    source_upload_grace_hours: int | None = None

    def __post_init__(self) -> None:
        if self.horizon_days is not None and (
            isinstance(self.horizon_days, bool)
            or not isinstance(self.horizon_days, int)
            or self.horizon_days <= 0
        ):
            raise ValueError("horizon_days must be a positive integer or null")
        if self.source_upload_grace_hours is not None and (
            isinstance(self.source_upload_grace_hours, bool)
            or not isinstance(self.source_upload_grace_hours, int)
            or self.source_upload_grace_hours < 0
        ):
            raise ValueError(
                "source_upload_grace_hours must be a nonnegative integer or null"
            )

    @property
    def enabled(self) -> bool:
        return (
            self.horizon_days is not None
            and self.source_upload_grace_hours is not None
        )

    def validation_reasons(self) -> tuple[AuthorityReason, ...]:
        reasons: list[AuthorityReason] = []
        if self.horizon_days is None:
            reasons.append(AuthorityReason.MISSING_HORIZON)
        if self.source_upload_grace_hours is None:
            reasons.append(AuthorityReason.MISSING_UPLOAD_GRACE)
        return tuple(reasons)

    def to_dict(self) -> dict[str, int | None]:
        return {
            "horizonDays": self.horizon_days,
            "sourceUploadGraceHours": self.source_upload_grace_hours,
        }


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class SourceCut:
    artifact_identity: str | None
    sha256_digest: str | None
    finalization_state: SourceFinalizationState | None
    analysis_as_of_utc: datetime | None

    def __post_init__(self) -> None:
        if self.finalization_state is not None and not isinstance(
            self.finalization_state, SourceFinalizationState
        ):
            raise TypeError("finalization_state must be SourceFinalizationState or null")
        object.__setattr__(
            self,
            "analysis_as_of_utc",
            _aware_utc(self.analysis_as_of_utc, "analysis_as_of_utc"),
        )

    def validation_reasons(self) -> tuple[AuthorityReason, ...]:
        reasons: list[AuthorityReason] = []
        if not isinstance(self.artifact_identity, str) or not self.artifact_identity.strip():
            reasons.append(AuthorityReason.MISSING_SOURCE_ARTIFACT_IDENTITY)
        if self.sha256_digest is None or self.sha256_digest == "":
            reasons.append(AuthorityReason.MISSING_SOURCE_DIGEST)
        elif not isinstance(self.sha256_digest, str) or not _SHA256.fullmatch(
            self.sha256_digest
        ):
            reasons.append(AuthorityReason.INVALID_SOURCE_DIGEST)
        if self.finalization_state is None:
            reasons.append(AuthorityReason.UNKNOWN_SOURCE_FINALIZATION)
        elif self.finalization_state is SourceFinalizationState.PROVISIONAL:
            reasons.append(AuthorityReason.PROVISIONAL_SOURCE)
        if self.analysis_as_of_utc is None:
            reasons.append(AuthorityReason.MISSING_SOURCE_CUT_AS_OF)
        return tuple(reasons)

    @property
    def identity(self) -> tuple[str | None, str | None, str | None, datetime | None]:
        return (
            self.artifact_identity,
            self.sha256_digest,
            None if self.finalization_state is None else self.finalization_state.value,
            self.analysis_as_of_utc,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifactIdentity": self.artifact_identity,
            "sha256Digest": self.sha256_digest,
            "finalizationState": (
                None if self.finalization_state is None
                else self.finalization_state.value
            ),
            "analysisAsOfUtc": (
                None if self.analysis_as_of_utc is None
                else self.analysis_as_of_utc.isoformat().replace("+00:00", "Z")
            ),
        }


@dataclass(frozen=True, slots=True)
class ComparisonPlan:
    horizon: HorizonContract
    minimum_mature_anchors_per_cohort: int | None
    maximum_censoring_rate: float | None
    baseline_mature_anchors: int | None
    candidate_mature_anchors: int | None
    baseline_censoring_rate: float | None
    candidate_censoring_rate: float | None
    backend_compatible: bool | None
    environment_compatible: bool | None
    content_release_scope_compatible: bool | None
    horizon_compatible: bool | None
    upload_grace_compatible: bool | None
    source_cut_compatible: bool | None
    baseline_source_cut: SourceCut | None
    candidate_source_cut: SourceCut | None

    def __post_init__(self) -> None:
        if not isinstance(self.horizon, HorizonContract):
            raise TypeError("horizon must be a HorizonContract")
        if self.minimum_mature_anchors_per_cohort is not None and (
            isinstance(self.minimum_mature_anchors_per_cohort, bool)
            or not isinstance(self.minimum_mature_anchors_per_cohort, int)
            or self.minimum_mature_anchors_per_cohort <= 0
        ):
            raise ValueError(
                "minimum_mature_anchors_per_cohort must be positive or null"
            )
        for name in ("baseline_mature_anchors", "candidate_mature_anchors"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be nonnegative or null")
        for name in (
            "maximum_censoring_rate",
            "baseline_censoring_rate",
            "candidate_censoring_rate",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= float(value) <= 1
            ):
                raise ValueError(f"{name} must be a finite ratio or null")
        for name in (
            "backend_compatible",
            "environment_compatible",
            "content_release_scope_compatible",
            "horizon_compatible",
            "upload_grace_compatible",
            "source_cut_compatible",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{name} must be boolean or null")
        for name in ("baseline_source_cut", "candidate_source_cut"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, SourceCut):
                raise TypeError(f"{name} must be SourceCut or null")


@dataclass(frozen=True, slots=True)
class AuthorityContext:
    derived_fixed_horizon: bool = False
    anchor_end_utc: datetime | None = None
    analysis_as_of_utc: datetime | None = None
    complete_source_coverage: bool | None = None
    comparison_plan: ComparisonPlan | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.derived_fixed_horizon, bool):
            raise TypeError("derived_fixed_horizon must be boolean")
        if self.complete_source_coverage is not None and not isinstance(
            self.complete_source_coverage, bool
        ):
            raise TypeError("complete_source_coverage must be boolean or null")
        if self.comparison_plan is not None and not isinstance(
            self.comparison_plan, ComparisonPlan
        ):
            raise TypeError("comparison_plan must be ComparisonPlan or null")
        object.__setattr__(
            self,
            "anchor_end_utc",
            _aware_utc(self.anchor_end_utc, "anchor_end_utc"),
        )
        object.__setattr__(
            self,
            "analysis_as_of_utc",
            _aware_utc(self.analysis_as_of_utc, "analysis_as_of_utc"),
        )


@dataclass(frozen=True, slots=True)
class FactPolicy:
    evidence_class: EvidenceClass
    subject_level: SubjectLevel
    comparison_capability: ComparisonCapability
    required_finalization: SourceFinalizationState | None = None

    def to_dict(self, fact_kind: RetentionEvidenceFactKind) -> dict[str, Any]:
        return {
            "factKind": fact_kind.value,
            "evidenceClass": self.evidence_class.value,
            "subjectLevel": self.subject_level.value,
            "factual": AuthorityDecision.ALLOWED.value,
            "comparisonCapability": self.comparison_capability.value,
            "decision": AuthorityDecision.DENIED.value,
            "target": AuthorityDecision.DENIED.value,
            "guardrail": AuthorityDecision.DENIED.value,
            "rollback": AuthorityDecision.DENIED.value,
            "requiredFinalization": (
                None if self.required_finalization is None
                else self.required_finalization.value
            ),
        }


_DERIVED = ComparisonCapability.DERIVED_MATURE_FIXED_HORIZON_ONLY
_NEVER = ComparisonCapability.DENIED

FACT_POLICIES: Mapping[RetentionEvidenceFactKind, FactPolicy] = MappingProxyType({
    RetentionEvidenceFactKind.B7_RETURNED: FactPolicy(
        EvidenceClass.DIRECT_BEHAVIOR_OBSERVATION, SubjectLevel.ANCHOR, _DERIVED
    ),
    RetentionEvidenceFactKind.B7_MATURE_NO_OBSERVED_RETURN: FactPolicy(
        EvidenceClass.BOUNDED_ABSENCE_OBSERVATION, SubjectLevel.ANCHOR, _DERIVED
    ),
    RetentionEvidenceFactKind.B7_RIGHT_CENSORED: FactPolicy(
        EvidenceClass.CENSORED_OBSERVATION, SubjectLevel.ANCHOR, _NEVER
    ),
    RetentionEvidenceFactKind.B8_RETURNED: FactPolicy(
        EvidenceClass.DIRECT_LIFECYCLE_OBSERVATION, SubjectLevel.ANCHOR, _DERIVED
    ),
    RetentionEvidenceFactKind.B8_MATURE_NO_OBSERVED_RETURN: FactPolicy(
        EvidenceClass.BOUNDED_ABSENCE_OBSERVATION, SubjectLevel.ANCHOR, _DERIVED
    ),
    RetentionEvidenceFactKind.B8_RIGHT_CENSORED: FactPolicy(
        EvidenceClass.CENSORED_OBSERVATION, SubjectLevel.ANCHOR, _NEVER
    ),
    RetentionEvidenceFactKind.R3D_FINALIZED_MAPPED_OBSERVED_UNINSTALL: FactPolicy(
        EvidenceClass.DIRECT_REMOVAL_OBSERVATION,
        SubjectLevel.PROFILE,
        _NEVER,
        SourceFinalizationState.FINAL,
    ),
    RetentionEvidenceFactKind.R3D_UNMAPPED_OBSERVED_UNINSTALL: FactPolicy(
        EvidenceClass.SOURCE_QUALITY_OBSERVATION, SubjectLevel.SOURCE, _NEVER
    ),
    RetentionEvidenceFactKind.R3D_AMBIGUOUS_OBSERVED_UNINSTALL: FactPolicy(
        EvidenceClass.SOURCE_QUALITY_OBSERVATION, SubjectLevel.SOURCE, _NEVER
    ),
    RetentionEvidenceFactKind.R3D_PROVISIONAL_OBSERVED_UNINSTALL: FactPolicy(
        EvidenceClass.DIRECT_REMOVAL_OBSERVATION,
        SubjectLevel.APP_INSTANCE,
        _NEVER,
        SourceFinalizationState.PROVISIONAL,
    ),
    RetentionEvidenceFactKind.R3D_NO_APP_REMOVE_OBSERVED: FactPolicy(
        EvidenceClass.BOUNDED_ABSENCE_OBSERVATION, SubjectLevel.SOURCE, _NEVER
    ),
})


@dataclass(frozen=True, slots=True)
class SequencePolicy:
    conflict: bool
    factual_interpretation: str

    def to_dict(self, relation: SequenceRelation) -> dict[str, Any]:
        return {
            "relation": relation.value,
            "conflict": self.conflict,
            "factualInterpretation": self.factual_interpretation,
        }


SEQUENCE_POLICIES: Mapping[SequenceRelation, SequencePolicy] = MappingProxyType({
    SequenceRelation.UNINSTALL_THEN_RETURN: SequencePolicy(
        False, "The removal observation and later return are both preserved."
    ),
    SequenceRelation.RETURN_THEN_UNINSTALL: SequencePolicy(
        False, "The return and later removal observation are both preserved."
    ),
    SequenceRelation.NEW_ATTEMPT_WITHOUT_LIFECYCLE_RETURN: SequencePolicy(
        False,
        "A new attempt can occur while the application remains continuously foregrounded.",
    ),
    SequenceRelation.SAME_TIME_CROSS_SOURCE: SequencePolicy(
        False, "Equal occurrence timestamps do not receive an ingestion-order tie break."
    ),
})


def _compatibility_reason(
    value: bool | None,
    unknown: AuthorityReason,
    incompatible: AuthorityReason,
) -> AuthorityReason | None:
    if value is None:
        return unknown
    if value is False:
        return incompatible
    return None


def _comparison_resolution(
    fact_kind: RetentionEvidenceFactKind,
    policy: FactPolicy,
    context: AuthorityContext,
) -> AuthorityDimensionResolution:
    if policy.comparison_capability is ComparisonCapability.DENIED:
        if fact_kind in {
            RetentionEvidenceFactKind.B7_RIGHT_CENSORED,
            RetentionEvidenceFactKind.B8_RIGHT_CENSORED,
        }:
            return _denied(AuthorityReason.RIGHT_CENSORED)
        if policy.required_finalization is SourceFinalizationState.PROVISIONAL:
            return _denied(AuthorityReason.PROVISIONAL_SOURCE)
        if policy.subject_level is SubjectLevel.SOURCE:
            return _denied(
                AuthorityReason.SOURCE_LEVEL_ONLY,
                AuthorityReason.COMPARISON_NOT_PERMITTED_FOR_FACT,
            )
        return _denied(AuthorityReason.COMPARISON_NOT_PERMITTED_FOR_FACT)

    reasons: list[AuthorityReason] = []
    if not context.derived_fixed_horizon:
        reasons.append(AuthorityReason.NOT_DERIVED_FIXED_HORIZON)
    plan = context.comparison_plan
    if plan is None:
        reasons.append(AuthorityReason.MISSING_COMPARISON_PLAN)
        return _denied(*reasons)

    reasons.extend(plan.horizon.validation_reasons())
    if context.anchor_end_utc is None:
        reasons.append(AuthorityReason.MISSING_ANCHOR_END)
    if context.analysis_as_of_utc is None:
        reasons.append(AuthorityReason.MISSING_ANALYSIS_AS_OF)
    if context.complete_source_coverage is None:
        reasons.append(AuthorityReason.UNKNOWN_SOURCE_COVERAGE)
    elif not context.complete_source_coverage:
        reasons.append(AuthorityReason.INCOMPLETE_SOURCE_COVERAGE)

    for value, unknown, incompatible in (
        (
            plan.backend_compatible,
            AuthorityReason.UNKNOWN_BACKEND_COMPATIBILITY,
            AuthorityReason.INCOMPATIBLE_BACKEND,
        ),
        (
            plan.environment_compatible,
            AuthorityReason.UNKNOWN_ENVIRONMENT_COMPATIBILITY,
            AuthorityReason.INCOMPATIBLE_ENVIRONMENT,
        ),
        (
            plan.content_release_scope_compatible,
            AuthorityReason.UNKNOWN_SCOPE_COMPATIBILITY,
            AuthorityReason.INCOMPATIBLE_SCOPE,
        ),
        (
            plan.horizon_compatible,
            AuthorityReason.UNKNOWN_HORIZON_COMPATIBILITY,
            AuthorityReason.INCOMPATIBLE_HORIZON,
        ),
        (
            plan.upload_grace_compatible,
            AuthorityReason.UNKNOWN_UPLOAD_GRACE_COMPATIBILITY,
            AuthorityReason.INCOMPATIBLE_UPLOAD_GRACE,
        ),
        (
            plan.source_cut_compatible,
            AuthorityReason.UNKNOWN_SOURCE_CUT_COMPATIBILITY,
            AuthorityReason.INCOMPATIBLE_SOURCE_CUT,
        ),
    ):
        reason = _compatibility_reason(value, unknown, incompatible)
        if reason is not None:
            reasons.append(reason)

    for source_cut in (plan.baseline_source_cut, plan.candidate_source_cut):
        if source_cut is None:
            reasons.append(AuthorityReason.MISSING_SOURCE_CUT)
        else:
            reasons.extend(source_cut.validation_reasons())

    minimum = plan.minimum_mature_anchors_per_cohort
    if minimum is None:
        reasons.append(AuthorityReason.MISSING_MINIMUM_MATURE_ANCHORS)
    if (
        plan.baseline_mature_anchors is None
        or plan.candidate_mature_anchors is None
    ):
        reasons.append(AuthorityReason.MISSING_COHORT_MATURITY)
    elif minimum is not None and min(
        plan.baseline_mature_anchors, plan.candidate_mature_anchors
    ) < minimum:
        reasons.append(AuthorityReason.INSUFFICIENT_MATURE_ANCHORS)

    maximum = plan.maximum_censoring_rate
    if maximum is None:
        reasons.append(AuthorityReason.MISSING_MAXIMUM_CENSORING_RATE)
    if (
        plan.baseline_censoring_rate is None
        or plan.candidate_censoring_rate is None
    ):
        reasons.append(AuthorityReason.MISSING_CENSORING_RATE)
    elif maximum is not None and max(
        plan.baseline_censoring_rate, plan.candidate_censoring_rate
    ) > maximum:
        reasons.append(AuthorityReason.CENSORING_RATE_EXCEEDED)

    if (
        plan.horizon.enabled
        and context.anchor_end_utc is not None
        and context.analysis_as_of_utc is not None
        and not is_mature(
            context.anchor_end_utc, context.analysis_as_of_utc, plan.horizon
        )
    ):
        reasons.append(AuthorityReason.INSUFFICIENT_MATURITY)

    if reasons:
        return _denied(*reasons)
    return _allowed()


def resolve_authority(
    fact_kind: RetentionEvidenceFactKind | object,
    context: AuthorityContext = AuthorityContext(),
) -> AuthorityResolution:
    """Resolve every authority dimension; unknown inputs fail closed."""

    if not isinstance(context, AuthorityContext):
        raise TypeError("context must be AuthorityContext")
    if not isinstance(fact_kind, RetentionEvidenceFactKind):
        unsupported = _denied(AuthorityReason.UNSUPPORTED_FACT_KIND)
        return AuthorityResolution(
            unsupported, unsupported, unsupported, unsupported, unsupported, unsupported
        )
    policy = FACT_POLICIES[fact_kind]
    return AuthorityResolution(
        factual=_allowed(),
        comparison=_comparison_resolution(fact_kind, policy, context),
        decision=_denied(AuthorityReason.DECISION_AUTHORITY_DENIED),
        target=_denied(AuthorityReason.TARGET_AUTHORITY_DENIED),
        guardrail=_denied(AuthorityReason.GUARDRAIL_AUTHORITY_DENIED),
        rollback=_denied(AuthorityReason.ROLLBACK_AUTHORITY_DENIED),
    )


def is_mature(
    anchor_end_utc: datetime,
    analysis_as_of_utc: datetime,
    horizon: HorizonContract,
) -> bool:
    """Return whether the full horizon plus upload grace has elapsed."""

    anchor = _aware_utc(anchor_end_utc, "anchor_end_utc")
    as_of = _aware_utc(analysis_as_of_utc, "analysis_as_of_utc")
    if not horizon.enabled:
        raise ValueError("maturity requires both horizon and upload grace")
    assert anchor is not None and as_of is not None
    return anchor + timedelta(
        days=horizon.horizon_days or 0,
        hours=horizon.source_upload_grace_hours or 0,
    ) <= as_of


@dataclass(frozen=True, slots=True)
class RetentionEvidenceEvent:
    fact_kind: RetentionEvidenceFactKind
    occurred_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fact_kind, RetentionEvidenceFactKind):
            raise TypeError("fact_kind must be RetentionEvidenceFactKind")
        value = _aware_utc(self.occurred_at_utc, "occurred_at_utc")
        assert value is not None
        object.__setattr__(self, "occurred_at_utc", value)


@dataclass(frozen=True, slots=True)
class RetentionEventLedger:
    """Immutable factual history; horizon views never mutate or truncate it."""

    events: tuple[RetentionEvidenceEvent, ...]

    def __post_init__(self) -> None:
        values = tuple(self.events)
        if any(not isinstance(item, RetentionEvidenceEvent) for item in values):
            raise TypeError("events must contain RetentionEvidenceEvent values")
        object.__setattr__(
            self,
            "events",
            tuple(sorted(values, key=lambda item: (
                item.occurred_at_utc, item.fact_kind.value
            ))),
        )

    def comparison_window(
        self, anchor_end_utc: datetime, horizon: HorizonContract
    ) -> tuple[RetentionEvidenceEvent, ...]:
        anchor = _aware_utc(anchor_end_utc, "anchor_end_utc")
        if not horizon.enabled:
            raise ValueError("comparison window requires horizon and upload grace")
        assert anchor is not None and horizon.horizon_days is not None
        cutoff = anchor + timedelta(days=horizon.horizon_days)
        return tuple(
            item for item in self.events
            if anchor < item.occurred_at_utc <= cutoff
        )


def policy_contract() -> dict[str, Any]:
    return {
        "schemaVersion": POLICY_SCHEMA_VERSION,
        "policyName": POLICY_NAME,
        "policyVersion": POLICY_VERSION,
        "evidenceClasses": [item.value for item in EvidenceClass],
        "subjectLevels": [item.value for item in SubjectLevel],
        "authorityDecisions": [item.value for item in AuthorityDecision],
        "sourceFinalizationStates": [item.value for item in SourceFinalizationState],
        "authorityReasonCodes": [item.value for item in AuthorityReason],
        "facts": [
            FACT_POLICIES[kind].to_dict(kind)
            for kind in RetentionEvidenceFactKind
        ],
        "sequenceSemantics": [
            SEQUENCE_POLICIES[relation].to_dict(relation)
            for relation in SequenceRelation
        ],
        "boundaries": {
            "eventLedgerIsUnboundedByComparisonHorizon": True,
            "comparisonRequiresResolvedRuntimeAuthority": True,
            "sourceMetricAuthorityIsUnchanged": True,
            "derivedMetricsRegistered": False,
            "c1C2IntegrationEnabled": False,
            "compositeScoreAllowed": False,
        },
    }


def serialize_policy_contract() -> str:
    return json.dumps(
        policy_contract(), ensure_ascii=False, indent=2, sort_keys=True,
        allow_nan=False,
    ) + "\n"


@dataclass(frozen=True, slots=True)
class PolicyValidationFinding:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class PolicyValidationReport:
    parsed_contracts: int
    checked_invariants: int
    findings: tuple[PolicyValidationFinding, ...]

    @property
    def ready(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "policyName": POLICY_NAME,
            "policyVersion": POLICY_VERSION,
            "parsedContracts": self.parsed_contracts,
            "checkedInvariants": self.checked_invariants,
            "ready": self.ready,
            "findings": [item.to_dict() for item in self.findings],
        }


_FORBIDDEN_STATE_IDENTIFIERS = frozenset({
    "Churned", "Lost", "Retained", "Abandoned",
})
_FORBIDDEN_COMPOSITE_IDENTIFIERS = frozenset({
    "retentionHealthScore", "retentionScore", "combinedRetentionRate",
})


def _flatten_identifiers(value: Any) -> tuple[str, ...]:
    result: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            result.append(str(key))
            result.extend(_flatten_identifiers(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_flatten_identifiers(item))
    elif isinstance(value, str):
        result.append(value)
    return tuple(result)


def validate_policy_contract(
    contract: Mapping[str, Any] | None = None,
) -> PolicyValidationReport:
    """Validate the serialized surface and executable fail-closed invariants."""

    raw = dict(policy_contract() if contract is None else contract)
    findings: list[PolicyValidationFinding] = []
    checked = 0

    def check(condition: bool, code: str, message: str) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            findings.append(PolicyValidationFinding(code, message))

    check(raw.get("schemaVersion") == POLICY_SCHEMA_VERSION, "SCHEMA_VERSION", "schema version mismatch")
    check(raw.get("policyName") == POLICY_NAME, "POLICY_NAME", "policy name mismatch")
    check(raw.get("policyVersion") == POLICY_VERSION, "POLICY_VERSION", "policy version mismatch")
    check(raw.get("evidenceClasses") == [item.value for item in EvidenceClass], "EVIDENCE_CLASSES", "evidence class contract mismatch")
    check(raw.get("subjectLevels") == [item.value for item in SubjectLevel], "SUBJECT_LEVELS", "subject level contract mismatch")
    check(raw.get("authorityDecisions") == [item.value for item in AuthorityDecision], "AUTHORITY_DECISIONS", "authority decision contract mismatch")
    check(raw.get("sourceFinalizationStates") == [item.value for item in SourceFinalizationState], "SOURCE_FINALIZATION", "source finalization contract mismatch")
    check(raw.get("authorityReasonCodes") == [item.value for item in AuthorityReason], "AUTHORITY_REASONS", "authority reason contract mismatch")
    facts = raw.get("facts")
    check(isinstance(facts, list), "FACT_MATRIX", "fact matrix must be an array")
    if isinstance(facts, list):
        check(len(facts) == len(RetentionEvidenceFactKind), "FACT_MATRIX_SIZE", "fact matrix size mismatch")
        check(
            [item.get("factKind") for item in facts if isinstance(item, Mapping)]
            == [item.value for item in RetentionEvidenceFactKind],
            "FACT_MATRIX_KINDS",
            "fact matrix kind/order mismatch",
        )
        for item in facts:
            if not isinstance(item, Mapping):
                findings.append(PolicyValidationFinding("FACT_MATRIX_ROW", "fact row must be an object"))
                continue
            check(item.get("factual") == "Allowed", "FACTUAL_DEFAULT", "all supported facts must be factual")
            for dimension in ("decision", "target", "guardrail", "rollback"):
                check(item.get(dimension) == "Denied", "AUTHORITY_CLOSED", f"{dimension} must be denied")

    identifiers = _flatten_identifiers(raw)
    lowered_identifiers = tuple(item.casefold() for item in identifiers)
    for forbidden in sorted(_FORBIDDEN_STATE_IDENTIFIERS | _FORBIDDEN_COMPOSITE_IDENTIFIERS):
        check(
            not any(forbidden.casefold() in item for item in lowered_identifiers),
            "FORBIDDEN_IDENTIFIER",
            f"forbidden identifier present: {forbidden}",
        )

    default_context = AuthorityContext()
    for kind in RetentionEvidenceFactKind:
        resolution = resolve_authority(kind, default_context)
        check(resolution.factual.decision is AuthorityDecision.ALLOWED, "FACTUAL_RESOLUTION", f"{kind.value} factual authority failed")
        for name in ("comparison", "decision", "target", "guardrail", "rollback"):
            check(getattr(resolution, name).decision is AuthorityDecision.DENIED, "FAIL_CLOSED", f"{kind.value}.{name} must default denied")

    for kind in (
        RetentionEvidenceFactKind.B7_RIGHT_CENSORED,
        RetentionEvidenceFactKind.B8_RIGHT_CENSORED,
    ):
        check(AuthorityReason.RIGHT_CENSORED in resolve_authority(kind).comparison.reasons, "CENSORING_POLICY", "right-censored comparison did not fail closed")
    check(AuthorityReason.PROVISIONAL_SOURCE in resolve_authority(RetentionEvidenceFactKind.R3D_PROVISIONAL_OBSERVED_UNINSTALL).comparison.reasons, "PROVISIONAL_POLICY", "provisional comparison did not fail closed")
    unknown = resolve_authority("UnknownFact")
    check(all(getattr(unknown, name).decision is AuthorityDecision.DENIED for name in ("factual", "comparison", "decision", "target", "guardrail", "rollback")), "UNKNOWN_FAIL_CLOSED", "unknown fact did not fail closed")

    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    final_left = SourceCut(
        "baseline", "a" * 64, SourceFinalizationState.FINAL, instant
    )
    final_right = SourceCut(
        "candidate", "b" * 64, SourceFinalizationState.FINAL, instant
    )

    def comparison_plan(horizon: HorizonContract) -> ComparisonPlan:
        return ComparisonPlan(
            horizon=horizon,
            minimum_mature_anchors_per_cohort=1,
            maximum_censoring_rate=0.5,
            baseline_mature_anchors=1,
            candidate_mature_anchors=1,
            baseline_censoring_rate=0.0,
            candidate_censoring_rate=0.0,
            backend_compatible=True,
            environment_compatible=True,
            content_release_scope_compatible=True,
            horizon_compatible=True,
            upload_grace_compatible=True,
            source_cut_compatible=True,
            baseline_source_cut=final_left,
            candidate_source_cut=final_right,
        )

    valid_context = AuthorityContext(
        derived_fixed_horizon=True,
        anchor_end_utc=instant,
        analysis_as_of_utc=instant + timedelta(days=8),
        complete_source_coverage=True,
        comparison_plan=comparison_plan(HorizonContract(7, 24)),
    )
    check(
        resolve_authority(
            RetentionEvidenceFactKind.B7_RETURNED, valid_context
        ).comparison.decision is AuthorityDecision.ALLOWED,
        "MATURE_COMPARISON",
        "valid mature fixed-horizon comparison was not allowed",
    )
    missing_grace = resolve_authority(
        RetentionEvidenceFactKind.B7_RETURNED,
        AuthorityContext(
            derived_fixed_horizon=True,
            anchor_end_utc=instant,
            analysis_as_of_utc=instant + timedelta(days=8),
            complete_source_coverage=True,
            comparison_plan=comparison_plan(HorizonContract(7, None)),
        ),
    )
    check(
        AuthorityReason.MISSING_UPLOAD_GRACE in missing_grace.comparison.reasons,
        "HORIZON_PAIR",
        "partial horizon pair did not fail closed",
    )
    missing_digest = SourceCut(
        "baseline", None, SourceFinalizationState.FINAL, instant
    )
    plan = comparison_plan(HorizonContract(7, 24))
    plan = replace(plan, baseline_source_cut=missing_digest)
    missing_cut_context = AuthorityContext(
        derived_fixed_horizon=True,
        anchor_end_utc=instant,
        analysis_as_of_utc=instant + timedelta(days=8),
        complete_source_coverage=True,
        comparison_plan=plan,
    )
    check(
        AuthorityReason.MISSING_SOURCE_DIGEST in resolve_authority(
            RetentionEvidenceFactKind.B8_RETURNED, missing_cut_context
        ).comparison.reasons,
        "SOURCE_CUT_REQUIREMENT",
        "missing source digest did not fail closed",
    )
    ledger_event = RetentionEvidenceEvent(
        RetentionEvidenceFactKind.R3D_FINALIZED_MAPPED_OBSERVED_UNINSTALL,
        instant + timedelta(days=20),
    )
    ledger = RetentionEventLedger((ledger_event,))
    check(
        ledger_event in ledger.events
        and ledger_event not in ledger.comparison_window(
            instant, HorizonContract(7, 24)
        ),
        "LEDGER_HORIZON_BOUNDARY",
        "comparison horizon mutated or included out-of-window factual history",
    )

    return PolicyValidationReport(1, checked, tuple(findings))


__all__ = [
    "POLICY_SCHEMA_VERSION", "POLICY_NAME", "POLICY_VERSION",
    "EvidenceClass", "SubjectLevel", "AuthorityDecision",
    "SourceFinalizationState", "RetentionEvidenceFactKind",
    "ComparisonCapability", "SequenceRelation", "AuthorityReason",
    "AuthorityDimensionResolution", "AuthorityResolution", "HorizonContract",
    "SourceCut", "ComparisonPlan", "AuthorityContext", "FactPolicy",
    "FACT_POLICIES", "SequencePolicy", "SEQUENCE_POLICIES",
    "RetentionEvidenceEvent", "RetentionEventLedger", "resolve_authority",
    "is_mature", "policy_contract", "serialize_policy_contract",
    "PolicyValidationFinding", "PolicyValidationReport", "validate_policy_contract",
]
