"""Frozen models for the local-only C-1 evidence compiler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


ANALYSIS_BRIEF_VERSION = "1.0.0"
SELECTION_POLICY_VERSION = "1.0.0"
SINGLE_MODE = "singleVersion"
COMPARISON_MODE = "contentVersionCompare"
DOMAIN_ORDER = (
    "stageDifficulty",
    "weaponPerformance",
    "upgradeChoice",
    "progressionNextRun",
    "postRunBehavior",
)


@dataclass(frozen=True, slots=True)
class EvidenceSelectionPolicy:
    max_brief_characters: int = 48_000
    max_evidence_items: int = 120
    max_evidence_items_per_domain: int = 30
    core_evidence_minimum_per_domain: int = 3

    def __post_init__(self) -> None:
        if self.max_brief_characters < 8_000:
            raise ValueError("max_brief_characters must be at least 8000")
        if self.max_evidence_items <= 0:
            raise ValueError("max_evidence_items must be positive")
        if self.max_evidence_items_per_domain <= 0:
            raise ValueError("max_evidence_items_per_domain must be positive")
        if self.core_evidence_minimum_per_domain < 0:
            raise ValueError("core_evidence_minimum_per_domain must be nonnegative")
        if self.max_evidence_items_per_domain < self.core_evidence_minimum_per_domain:
            raise ValueError(
                "max_evidence_items_per_domain must be at least "
                "core_evidence_minimum_per_domain"
            )


@dataclass(frozen=True, slots=True)
class AnalysisBriefRequest:
    mode: str
    source_report_paths: tuple[Path, ...]
    selection_policy: EvidenceSelectionPolicy = EvidenceSelectionPolicy()

    def __post_init__(self) -> None:
        aliases = {
            "single-version": SINGLE_MODE,
            "singleVersion": SINGLE_MODE,
            "content-version-compare": COMPARISON_MODE,
            "contentVersionCompare": COMPARISON_MODE,
        }
        normalized = aliases.get(self.mode)
        if normalized is None:
            raise ValueError("mode must be singleVersion or contentVersionCompare")
        object.__setattr__(self, "mode", normalized)
        paths = tuple(Path(item) for item in self.source_report_paths)
        if not paths:
            raise ValueError("at least one explicit source_report_path is required")
        object.__setattr__(self, "source_report_paths", paths)
        if normalized == COMPARISON_MODE and len(paths) != 1:
            raise ValueError("contentVersionCompare mode requires exactly one source bundle")


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SourceBundleIdentity:
    analysis_type: str
    domain: str
    bundle_name: str
    bundle_digest: str
    portable_path: str | None


@dataclass(frozen=True, slots=True)
class SourceBundleManifest:
    identity: SourceBundleIdentity
    scope: Mapping[str, Any]
    sample: Mapping[str, Any]
    quality: Mapping[str, Any]
    definitions: Mapping[str, Any]
    warnings: tuple[Mapping[str, Any], ...]
    artifacts: tuple[SourceArtifact, ...]


@dataclass(frozen=True, slots=True)
class SnapshotDescriptor:
    domain: str
    mode: str
    cutoff_utc: datetime | None
    guarantee: str


@dataclass(frozen=True, slots=True)
class LoadedSourceBundle:
    path: Path = field(repr=False, compare=False)
    analysis_type: str
    domain: str
    bundle_name: str
    bundle_digest: str
    portable_path: str | None
    metadata: Mapping[str, Any]
    metrics: Mapping[str, Any]
    tables: Mapping[str, Any] = field(repr=False, compare=False)
    artifacts: Mapping[str, SourceArtifact] = field(default_factory=dict)
    snapshot: SnapshotDescriptor | None = None


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    source_bundle_id: str
    source_bundle_digest: str
    source_artifact: str
    source_artifact_sha256: str
    source_row_key: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EvidenceDefinition:
    metric_family: str
    metric: str
    value_type: str
    unit: str
    observation_unit: str
    core: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceValue:
    value_type: str
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    mode: str
    source_analysis_type: str
    domain: str
    metric_family: str
    metric: str
    entity_type: str | None
    entity_key: str | None
    dimension: str | None
    dimension_value: str | None
    value_type: str
    unit: str
    observation_unit: str
    value: Mapping[str, Any]
    observed: bool
    status: str
    warning_codes: tuple[str, ...]
    sample: Mapping[str, Any]
    provenance: EvidenceProvenance
    priority: int
    core: bool = False
    source_designated: bool = False

    @property
    def canonical_identity(self) -> tuple[str, ...]:
        return tuple(
            "" if item is None else str(item)
            for item in (
                self.mode,
                self.source_analysis_type,
                self.domain,
                self.metric_family,
                self.metric,
                self.entity_type,
                self.entity_key,
                self.dimension,
                self.dimension_value,
            )
        )


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    canonical_identity: tuple[str, ...]
    source_analysis_type: str
    domain: str
    metric_family: str
    metric: str
    entity_type: str | None
    entity_key: str | None
    dimension: str | None
    dimension_value: str | None
    value_type: str
    unit: str
    observation_unit: str
    value: Mapping[str, Any]
    status: str
    warning_codes: tuple[str, ...]
    sample: Mapping[str, Any]
    priority: int
    core: bool
    source_designated: bool
    provenance: EvidenceProvenance


@dataclass(frozen=True, slots=True)
class BriefWarning:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DomainEvidenceSummary:
    domain: str
    status: str
    sample: Mapping[str, Any]
    quality: Mapping[str, Any]
    warning_codes: tuple[str, ...]
    selected_evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BriefDataQuality:
    domains_included: int
    domains_missing: int
    source_warning_count: int
    unavailable_evidence_count: int
    omitted_evidence_count: int


@dataclass(frozen=True, slots=True)
class BriefInterpretationConstraints:
    causal_claims_allowed: bool = False
    missing_means_zero: bool = False
    best_effort_absence_is_definitive: bool = False
    programmatic_navigation_means_user_intent: bool = False
    no_next_run_means_retention_outcome: bool = False


@dataclass(frozen=True, slots=True)
class SnapshotCompatibility:
    source_modes: tuple[str, ...]
    mode_difference: bool
    cutoff_difference_seconds: float | None
    missing_cutoff_domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisBriefScope:
    mode: str
    environment: str
    content_version: int | None
    baseline_content_version: int | None
    candidate_content_version: int | None
    stage_key: str | None
    domains: tuple[str, ...]
    source_bundle_digests: tuple[str, ...]
    max_brief_characters: int
    max_evidence_items: int
    max_evidence_items_per_domain: int
    core_evidence_minimum_per_domain: int
    selection_policy_version: str = SELECTION_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class EvidenceSelectionSummary:
    eligible_evidence_count: int
    selected_evidence_count: int
    omitted_evidence_count: int
    omitted_by_budget: int
    unavailable_in_source: int
    selected_by_domain: Mapping[str, int]
    omitted_by_domain: Mapping[str, int]
    core_selected_by_domain: Mapping[str, int]
    core_available_by_domain: Mapping[str, int]
    truncated: bool
    brief_character_count: int = 0
    brief_byte_count: int = 0


@dataclass(frozen=True, slots=True)
class AnalysisBrief:
    scope: AnalysisBriefScope
    overall_status: str
    sources: tuple[LoadedSourceBundle, ...] = field(repr=False, compare=False)
    warnings: tuple[BriefWarning, ...]
    snapshot_compatibility: SnapshotCompatibility
    evidence: tuple[EvidenceItem, ...]
    selection_summary: EvidenceSelectionSummary
    brief_payload: Mapping[str, Any]
    markdown: str
    semantic_digest: str
