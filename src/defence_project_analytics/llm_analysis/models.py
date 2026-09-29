"""Frozen C-2 request, prompt, response, and normalized analysis models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol


ANALYSIS_VERSION = "1.0.0"
ANALYSIS_POLICY_VERSION = "1.6.0"
PROMPT_TEMPLATE_VERSION = "1.5.0"
RESPONSE_CONTRACT_VERSION = "1.0.0"
DEFAULT_MAX_PROMPT_CHARACTERS = 400_000
DEFAULT_MAX_RESPONSE_CHARACTERS = 100_000
MAX_OBSERVATIONS = 10
MAX_RESPONSE_SECTION_ITEMS = 5
MAX_EXECUTIVE_SUMMARY_IDS = 3
MAX_ROLLBACK_INDICATORS = 10
OUTPUT_ID_DIGITS = 3
OUTPUT_ID_PREFIXES = {
    "observation": "OBS",
    "interpretation": "INT",
    "hypothesis": "HYP",
    "gap": "GAP",
    "change": "CHG",
    "validation": "VAL",
}

FINDING_TYPES = frozenset({
    "ObservedValue",
    "BaselineCandidateDifference",
    "ObservedOnlyInBaseline",
    "ObservedOnlyInCandidate",
    "LimitedOrUnavailable",
})
IMPORTANCE_VALUES = frozenset({"Core", "Supporting"})
COUNTER_EVIDENCE_FOUND_STATUS = "FoundInSuppliedBrief"
COUNTER_EVIDENCE_NOT_IDENTIFIED_STATUS = "NotIdentifiedInSuppliedBrief"
COUNTER_SEARCH_VALUES = frozenset({
    COUNTER_EVIDENCE_FOUND_STATUS,
    COUNTER_EVIDENCE_NOT_IDENTIFIED_STATUS,
})
ACTION_TYPES = frozenset({
    "NoChange",
    "CollectMoreData",
    "Investigate",
    "Experiment",
    "BalanceChange",
    "UXChange",
    "TelemetryChange",
})
TARGET_TYPES = frozenset({"EvidenceEntity", "EvidenceMetric", "Conceptual"})
DIRECTIONS = frozenset({"Increase", "Decrease", "Unchanged", "NotSpecified"})
OBSERVABLE_DIRECTIONS = frozenset({
    "Increase", "Decrease", "NoAssumedDirection", "MonitorOnly"
})
EVIDENCE_STRENGTH_VALUES = frozenset({
    "Insufficient", "Limited", "Moderate", "Strong"
})
ACTIONABILITY_VALUES = frozenset({
    "Hold", "Investigate", "ExperimentCandidate", "HumanReviewCandidate"
})
OVERALL_ASSESSMENTS = frozenset({
    "HumanReviewCandidateAvailable",
    "EvidenceLimited",
    "MoreDataRecommended",
    "NoMaterialCandidateIdentified",
})
KNOWN_ANALYSES = frozenset({
    "stageDifficulty",
    "weaponPerformance",
    "upgradeChoice",
    "progressionNextRun",
    "postRunBehavior",
    "retentionEvidence",
    "contentVersionCompare",
})
MINIMUM_REQUIREMENTS = frozenset({
    "ClearExistingLowSampleWarning",
    "ResolveSourceWarning",
    "NoMaterialCoverageMismatch",
    "ObservedInBothVersions",
    "MeetExistingAnalyticsThreshold",
})
COMPARISON_PLANS = frozenset({
    "NewContentVersionVsCurrentUsingContentVersionCompare",
    "RepeatSingleVersionAnalysis",
    "NotApplicable",
})
ROLLBACK_CONDITIONS = frozenset({
    "UnexpectedDirection",
    "SourceWarningReappears",
    "DesignObjectiveMiss",
})


@dataclass(frozen=True, slots=True)
class AnalysisPromptRequest:
    source_brief_path: Path
    analysis_objective: str | None = None
    output_language: str = "ko"
    max_prompt_characters: int = DEFAULT_MAX_PROMPT_CHARACTERS

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_brief_path", Path(self.source_brief_path))
        if self.output_language not in {"ko", "en"}:
            raise ValueError("output_language must be ko or en")
        if self.max_prompt_characters < 32_000:
            raise ValueError("max_prompt_characters must be at least 32000")
        if self.analysis_objective is not None:
            objective = self.analysis_objective.strip()
            if not objective:
                object.__setattr__(self, "analysis_objective", None)
            elif len(objective) > 4_000:
                raise ValueError("analysis_objective must be at most 4000 characters")
            else:
                object.__setattr__(self, "analysis_objective", objective)


@dataclass(frozen=True, slots=True)
class SourceArtifactDigest:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SourceBriefIdentity:
    bundle_name: str
    semantic_output_digest: str
    scope_hash: str
    mode: str
    environment: str
    content_version: int | None
    baseline_content_version: int | None
    candidate_content_version: int | None
    stage_key: str | None


@dataclass(frozen=True, slots=True)
class LoadedAnalysisBrief:
    path: Path = field(repr=False, compare=False)
    identity: SourceBriefIdentity
    brief: Mapping[str, Any]
    evidence: Mapping[str, Any]
    manifest: Mapping[str, Any]
    evidence_by_id: Mapping[str, Mapping[str, Any]]
    artifacts: tuple[SourceArtifactDigest, ...]
    portable_path: str | None


@dataclass(frozen=True, slots=True)
class AnalysisPromptPackage:
    request: AnalysisPromptRequest
    source: LoadedAnalysisBrief
    request_id: str
    request_digest: str
    prompt: str
    prompt_digest: str
    request_payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutiveSummary:
    qualitative_overview: str
    observation_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]
    evidence_gap_ids: tuple[str, ...]
    change_candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Observation:
    id: str
    finding_type: str
    qualitative_statement: str
    evidence_ids: tuple[str, ...]
    importance: str


@dataclass(frozen=True, slots=True)
class Interpretation:
    id: str
    statement: str
    evidence_ids: tuple[str, ...]
    limitation_evidence_ids: tuple[str, ...]
    limitation_warning_codes: tuple[str, ...]
    evidence_strength: str


@dataclass(frozen=True, slots=True)
class Hypothesis:
    id: str
    statement: str
    supporting_evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]
    counter_evidence_search_status: str
    limitation_warning_codes: tuple[str, ...]
    assumptions: tuple[str, ...]
    alternative_explanations: tuple[str, ...]
    evidence_gap_ids: tuple[str, ...]
    falsification_checks: tuple[str, ...]
    evidence_strength: str
    evidence_strength_scope: str = "suppliedC1Brief"


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    id: str
    question: str
    why_it_matters: str
    related_evidence_ids: tuple[str, ...]
    suggested_analysis: str | None
    requires_new_telemetry: bool


@dataclass(frozen=True, slots=True)
class ChangeTarget:
    target_type: str
    domain: str
    entity_type: str | None
    entity_key: str | None
    metric_family: str | None
    metric: str | None
    description: str | None
    requires_game_design_context: bool


@dataclass(frozen=True, slots=True)
class ProposedChange:
    description: str
    parameter: str | None
    direction: str
    amount_percent: float | None
    heuristic: bool
    magnitude_basis: str | None


@dataclass(frozen=True, slots=True)
class ExpectedObservableDirection:
    domain: str
    metric_family: str
    metric: str
    direction: str


@dataclass(frozen=True, slots=True)
class ChangeCandidate:
    id: str
    domain: str
    target: ChangeTarget
    action_type: str
    proposed_change: ProposedChange
    rationale: str
    supporting_evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]
    counter_evidence_search_status: str
    limitation_warning_codes: tuple[str, ...]
    risks: tuple[str, ...]
    expected_observable_directions: tuple[ExpectedObservableDirection, ...]
    validation_plan_id: str | None
    evidence_strength: str
    actionability: str
    evidence_strength_scope: str = "suppliedC1Brief"


@dataclass(frozen=True, slots=True)
class MetricReference:
    domain: str
    metric_family: str
    metric: str


@dataclass(frozen=True, slots=True)
class RollbackIndicator:
    metric: MetricReference
    condition: str


@dataclass(frozen=True, slots=True)
class ValidationPlan:
    id: str
    change_candidate_id: str
    analyses_to_rerun: tuple[str, ...]
    metrics_to_watch: tuple[MetricReference, ...]
    guardrail_metrics: tuple[MetricReference, ...]
    minimum_evidence_requirements: tuple[str, ...]
    comparison_plan: str
    rollback_indicators: tuple[RollbackIndicator, ...]


@dataclass(frozen=True, slots=True)
class CitationCoverage:
    count: int
    denominator: int
    ratio: float | None


@dataclass(frozen=True, slots=True)
class ValidatedAnalysis:
    analysis_version: str
    source_brief_identity: SourceBriefIdentity
    comparison_direction_acknowledgement: str
    executive_summary: ExecutiveSummary
    observations: tuple[Observation, ...]
    interpretations: tuple[Interpretation, ...]
    hypotheses: tuple[Hypothesis, ...]
    evidence_gaps: tuple[EvidenceGap, ...]
    change_candidates: tuple[ChangeCandidate, ...]
    validation_plans: tuple[ValidationPlan, ...]
    overall_assessment: str
    observation_citation_coverage: CitationCoverage
    hypothesis_support_coverage: CitationCoverage
    change_candidate_evidence_coverage: CitationCoverage
    actionable_candidate_validation_coverage: CitationCoverage
    invalid_evidence_reference_count: int
    normalized_analysis_digest: str


class AnalysisProvider(Protocol):
    provider_name: str
    model_name: str | None

    def generate(self, prompt: AnalysisPromptPackage) -> Mapping[str, object]: ...
