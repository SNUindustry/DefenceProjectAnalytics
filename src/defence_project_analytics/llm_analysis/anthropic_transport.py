"""Compact Anthropic input and strict-tool response transport adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from defence_project_analytics.llm_analysis.models import (
    ANALYSIS_VERSION,
    COUNTER_EVIDENCE_FOUND_STATUS,
    COUNTER_EVIDENCE_NOT_IDENTIFIED_STATUS,
    MAX_EXECUTIVE_SUMMARY_IDS,
    MAX_OBSERVATIONS,
    MAX_RESPONSE_SECTION_ITEMS,
    OUTPUT_ID_DIGITS,
    OUTPUT_ID_PREFIXES,
    AnalysisPromptPackage,
)
from defence_project_analytics.llm_analysis.provider_errors import (
    AnthropicDuplicateToolUseError,
    AnthropicEvidenceAliasError,
    AnthropicMetricAliasError,
    AnthropicOutputRefError,
    AnthropicValidationPlanRefError,
    AnthropicWarningAliasError,
    AnthropicFlatReconstructionError,
    AnthropicInvalidToolInputError,
    AnthropicMalformedResponseError,
    AnthropicRequestValidationError,
    AnthropicRequiredToolMissingError,
    AnthropicUnknownToolUseError,
)
from defence_project_analytics.llm_analysis.provider_schema import (
    ANTHROPIC_PROVIDER_STAGE_SPECS,
    ANTHROPIC_STAGE_SPECS,
    ANTHROPIC_STRICT_TOOL_SECTIONS,
)
from defence_project_analytics.llm_analysis.validator import (
    ValidatedStageA,
    ValidatedStageB,
)
from defence_project_analytics.llm_analysis.warning_authority import (
    collect_allowed_warning_codes,
    model_visible_brief,
)
from defence_project_analytics.metric_registry import (
    EvidenceUse,
    is_evidence_item_eligible,
    monitor_metric_keys,
    target_metric_keys,
)
from defence_project_analytics.reporting.renderers import to_external


ANTHROPIC_INPUT_PROJECTION_VERSION = "1.2.0"
ANTHROPIC_FLAT_RESPONSE_VERSION = "1.0.0"
ANTHROPIC_SERIALIZED_ENVELOPE_VERSION = "1.0.0"
ANTHROPIC_STRICT_TOOL_TRANSPORT_VERSION = "1.0.0"
ANTHROPIC_THREE_STAGE_STRICT_TOOL_TRANSPORT_VERSION = "1.10.0"
ANTHROPIC_STAGE_CONTEXT_VERSION = "1.5.0"
ANTHROPIC_STAGE_B_CONTEXT_VERSION = "1.7.0"
ANTHROPIC_STAGE_C_CONTEXT_VERSION = "1.8.0"
_ANTHROPIC_STAGE_CONTEXT_VERSIONS = {
    "A": ANTHROPIC_STAGE_CONTEXT_VERSION,
    "B": ANTHROPIC_STAGE_B_CONTEXT_VERSION,
    "C": ANTHROPIC_STAGE_C_CONTEXT_VERSION,
}
ANTHROPIC_EVIDENCE_ALIAS_VERSION = "1.1.0"
ANTHROPIC_METRIC_ALIAS_VERSION = "1.0.0"
ANTHROPIC_WARNING_ALIAS_VERSION = "1.0.0"
ANTHROPIC_VALIDATION_PLAN_REF_VERSION = "1.0.0"
ANTHROPIC_OUTPUT_REF_VERSION = "1.0.0"
ANTHROPIC_STAGE_B_IDENTITY_VERSION = "1.0.0"
_DATA_MARKER = "# UNTRUSTED_ANALYTICS_DATA"
_DATA_END = "# END_UNTRUSTED_ANALYTICS_DATA"
_METRIC_SEPARATOR = "::"
_NONE_SENTINEL = "None"
_NULL_METRIC_REFERENCE = 0
_SERIALIZED_SECTION_ROOTS = {
    "observationsJson": ("observations", list),
    "interpretationsJson": ("interpretations", list),
    "hypothesesJson": ("hypotheses", list),
    "evidenceGapsJson": ("evidenceGaps", list),
    "changeCandidatesJson": ("changeCandidates", list),
    "validationPlansJson": ("validationPlans", list),
    "executiveSummaryJson": ("executiveSummary", dict),
}


_RECONSTRUCTION_INVARIANTS = frozenset({
    "UNKNOWN_EVIDENCE_ALIAS",
    "UNKNOWN_METRIC_ALIAS",
    "UNKNOWN_WARNING_ALIAS",
    "UNKNOWN_VALIDATION_PLAN_REF",
    "UNKNOWN_OUTPUT_REF",
    "OUTPUT_REF_SET_MISMATCH",
    "VALIDATION_PLAN_REF_SET_MISMATCH",
    "INVALID_SENTINEL",
    "INVALID_AMOUNT_SENTINEL_STATE",
    "PARALLEL_LENGTH_MISMATCH",
    "INVALID_TARGET_RECONSTRUCTION",
    "INVALID_PROPOSED_CHANGE_RECONSTRUCTION",
    "INVALID_EXPECTED_DIRECTION_RECONSTRUCTION",
    "INVALID_METRIC_RECONSTRUCTION",
    "INVALID_SECTION_WRAPPER",
    "INVALID_SECTION_ITEM_SHAPE",
    "MISSING_REQUIRED_RECONSTRUCTION_FIELD",
    "UNEXPECTED_RECONSTRUCTION_FIELD",
    "CANONICAL_SECTION_CONSTRUCTION_FAILED",
})


class _ReconstructionInvariantError(RuntimeError):
    """Internal, payload-free description of a deterministic wire failure."""

    def __init__(
        self,
        invariant: str,
        *,
        section: str,
        component: str,
        item_index: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if invariant not in _RECONSTRUCTION_INVARIANTS:
            raise ValueError(f"unknown reconstruction invariant: {invariant}")
        super().__init__("Anthropic reconstruction invariant failed.")
        safe: dict[str, Any] = {
            "section": section,
            "reconstructionComponent": component,
            "reconstructionInvariant": invariant,
        }
        if item_index is not None:
            safe["itemIndex"] = item_index
        if details:
            safe.update(details)
        self.safe_details = safe


def _raise_reconstruction_failure(
    invariant: str,
    *,
    section: str,
    component: str,
    item_index: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise _ReconstructionInvariantError(
        invariant,
        section=section,
        component=component,
        item_index=item_index,
        details=details,
    )


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compact_payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_compact_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceAliasTable:
    """Request-local bijection between canonical Evidence IDs and integer refs."""

    canonical_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.canonical_evidence_ids:
            raise AnthropicEvidenceAliasError(
                "Anthropic Evidence alias mapping cannot be empty."
            )
        if any(
            not isinstance(value, str) or not value
            for value in self.canonical_evidence_ids
        ):
            raise AnthropicEvidenceAliasError(
                "Anthropic Evidence alias mapping contains an invalid canonical ID."
            )
        if len(set(self.canonical_evidence_ids)) != len(self.canonical_evidence_ids):
            raise AnthropicEvidenceAliasError(
                "Anthropic Evidence alias mapping contains duplicate canonical IDs."
            )

    @property
    def provider_refs(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.canonical_evidence_ids) + 1))

    @property
    def digest(self) -> str:
        mapping = [
            {"evidenceRef": reference, "evidenceId": evidence_id}
            for reference, evidence_id in zip(
                self.provider_refs, self.canonical_evidence_ids, strict=True
            )
        ]
        return hashlib.sha256(_compact_json(mapping).encode("utf-8")).hexdigest()

    def provider_ref(self, canonical_evidence_id: Any) -> int:
        if not isinstance(canonical_evidence_id, str):
            raise AnthropicEvidenceAliasError(
                "Anthropic canonical Evidence reference must be a string."
            )
        try:
            return self.canonical_evidence_ids.index(canonical_evidence_id) + 1
        except ValueError as error:
            raise AnthropicEvidenceAliasError(
                "Anthropic canonical Evidence reference is not in the alias mapping."
            ) from error

    def canonical_id(self, provider_ref: Any) -> str:
        if not isinstance(provider_ref, int) or isinstance(provider_ref, bool):
            raise AnthropicEvidenceAliasError(
                "Anthropic provider Evidence reference must be an integer."
            )
        if provider_ref < 1 or provider_ref > len(self.canonical_evidence_ids):
            raise AnthropicEvidenceAliasError(
                "Anthropic provider Evidence reference is outside the alias mapping."
            )
        return self.canonical_evidence_ids[provider_ref - 1]


@dataclass(frozen=True)
class MetricAliasTable:
    """Request-local bijection between registry metric keys and integer refs."""

    canonical_metric_keys: tuple[tuple[str, str, str], ...]

    def __post_init__(self) -> None:
        if not self.canonical_metric_keys:
            raise AnthropicMetricAliasError(
                "Anthropic metric alias mapping cannot be empty."
            )
        if any(
            not isinstance(key, tuple)
            or len(key) != 3
            or any(not isinstance(value, str) or not value for value in key)
            for key in self.canonical_metric_keys
        ):
            raise AnthropicMetricAliasError(
                "Anthropic metric alias mapping contains an invalid canonical key."
            )
        if len(set(self.canonical_metric_keys)) != len(self.canonical_metric_keys):
            raise AnthropicMetricAliasError(
                "Anthropic metric alias mapping contains duplicate canonical keys."
            )
        if set(self.canonical_metric_keys) != set(_provider_metric_keys()):
            raise AnthropicMetricAliasError(
                "Anthropic metric aliases do not match the exact metric registry."
            )

    @property
    def provider_refs(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.canonical_metric_keys) + 1))

    @property
    def digest(self) -> str:
        mapping = [
            {
                "metricRef": reference,
                "domain": key[0],
                "metricFamily": key[1],
                "metric": key[2],
            }
            for reference, key in zip(
                self.provider_refs, self.canonical_metric_keys, strict=True
            )
        ]
        return hashlib.sha256(_compact_json(mapping).encode("utf-8")).hexdigest()

    def provider_ref(self, canonical_key: Any) -> int:
        if (
            not isinstance(canonical_key, tuple)
            or len(canonical_key) != 3
            or any(not isinstance(value, str) for value in canonical_key)
        ):
            raise AnthropicMetricAliasError(
                "Anthropic canonical metric reference must be an exact key tuple."
            )
        try:
            return self.canonical_metric_keys.index(canonical_key) + 1
        except ValueError as error:
            raise AnthropicMetricAliasError(
                "Anthropic canonical metric reference is not in the alias mapping."
            ) from error

    def canonical_key(self, provider_ref: Any) -> tuple[str, str, str]:
        if not isinstance(provider_ref, int) or isinstance(provider_ref, bool):
            raise AnthropicMetricAliasError(
                "Anthropic provider metric reference must be an integer."
            )
        if provider_ref < 1 or provider_ref > len(self.canonical_metric_keys):
            raise AnthropicMetricAliasError(
                "Anthropic provider metric reference is outside the alias mapping."
            )
        return self.canonical_metric_keys[provider_ref - 1]


@dataclass(frozen=True)
class WarningAliasTable:
    """Request-local bijection between canonical warning codes and integer refs."""

    canonical_warning_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(not isinstance(code, str) or not code for code in self.canonical_warning_codes):
            raise AnthropicWarningAliasError(
                "Anthropic warning alias mapping contains an invalid canonical code."
            )
        if len(set(self.canonical_warning_codes)) != len(self.canonical_warning_codes):
            raise AnthropicWarningAliasError(
                "Anthropic warning alias mapping contains duplicate canonical codes."
            )
        if self.canonical_warning_codes != tuple(sorted(self.canonical_warning_codes)):
            raise AnthropicWarningAliasError(
                "Anthropic warning alias mapping is not deterministically ordered."
            )

    @property
    def provider_refs(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.canonical_warning_codes) + 1))

    @property
    def digest(self) -> str:
        mapping = [
            {"warningRef": reference, "warningCode": code}
            for reference, code in zip(
                self.provider_refs, self.canonical_warning_codes, strict=True
            )
        ]
        return hashlib.sha256(_compact_json(mapping).encode("utf-8")).hexdigest()

    def provider_ref(self, canonical_warning_code: Any) -> int:
        if not isinstance(canonical_warning_code, str):
            raise AnthropicWarningAliasError(
                "Anthropic canonical warning reference must be a string."
            )
        try:
            return self.canonical_warning_codes.index(canonical_warning_code) + 1
        except ValueError as error:
            raise AnthropicWarningAliasError(
                "Anthropic canonical warning reference is not in the alias mapping."
            ) from error

    def canonical_code(self, provider_ref: Any) -> str:
        if not isinstance(provider_ref, int) or isinstance(provider_ref, bool):
            raise AnthropicWarningAliasError(
                "Anthropic provider warning reference must be an integer."
            )
        if provider_ref < 1 or provider_ref > len(self.canonical_warning_codes):
            raise AnthropicWarningAliasError(
                "Anthropic provider warning reference is outside the alias mapping."
            )
        return self.canonical_warning_codes[provider_ref - 1]


@dataclass(frozen=True)
class OutputRefTable:
    """Stage-local exact refs for already validated canonical output objects."""

    kind: str
    canonical_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        prefix = OUTPUT_ID_PREFIXES.get(self.kind)
        if prefix is None:
            raise AnthropicOutputRefError(
                "Anthropic output ref mapping has an unknown canonical kind."
            )
        pattern = re.compile(
            rf"^{re.escape(prefix)}-\d{{{OUTPUT_ID_DIGITS}}}$"
        )
        if any(
            not isinstance(value, str) or pattern.fullmatch(value) is None
            for value in self.canonical_ids
        ):
            raise AnthropicOutputRefError(
                "Anthropic output ref mapping contains an invalid canonical ID."
            )
        if len(set(self.canonical_ids)) != len(self.canonical_ids):
            raise AnthropicOutputRefError(
                "Anthropic output ref mapping contains duplicate canonical IDs."
            )

    @property
    def provider_refs(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.canonical_ids) + 1))

    @property
    def digest(self) -> str:
        mapping = [
            {"outputRef": ref, "canonicalId": canonical_id}
            for ref, canonical_id in zip(
                self.provider_refs, self.canonical_ids, strict=True
            )
        ]
        return hashlib.sha256(_compact_json(mapping).encode("utf-8")).hexdigest()

    def provider_ref(self, canonical_id: Any) -> int:
        if not isinstance(canonical_id, str):
            raise AnthropicOutputRefError(
                "Anthropic canonical output reference must be a string."
            )
        try:
            return self.canonical_ids.index(canonical_id) + 1
        except ValueError as error:
            raise AnthropicOutputRefError(
                "Anthropic canonical output reference is not in the mapping."
            ) from error

    def canonical_id(self, provider_ref: Any) -> str:
        if not isinstance(provider_ref, int) or isinstance(provider_ref, bool):
            raise AnthropicOutputRefError(
                "Anthropic provider output reference must be an integer."
            )
        if provider_ref < 1 or provider_ref > len(self.canonical_ids):
            raise AnthropicOutputRefError(
                "Anthropic provider output reference is outside the mapping."
            )
        return self.canonical_ids[provider_ref - 1]


@dataclass(frozen=True)
class StageOutputRefTables:
    observations: OutputRefTable
    evidence_gaps: OutputRefTable
    hypotheses: OutputRefTable | None = None
    change_candidates: OutputRefTable | None = None


def build_stage_output_ref_tables(
    stage_a: ValidatedStageA,
    stage_b: ValidatedStageB | None = None,
) -> StageOutputRefTables:
    """Build deterministic refs from validated prior-stage array order."""

    tables = StageOutputRefTables(
        observations=OutputRefTable(
            "observation", tuple(item.id for item in stage_a.observations)
        ),
        evidence_gaps=OutputRefTable(
            "gap", tuple(item.id for item in stage_a.evidence_gaps)
        ),
        hypotheses=(
            OutputRefTable(
                "hypothesis", tuple(item.id for item in stage_b.hypotheses)
            )
            if stage_b is not None
            else None
        ),
        change_candidates=(
            OutputRefTable(
                "change", tuple(item.id for item in stage_b.change_candidates)
            )
            if stage_b is not None
            else None
        ),
    )
    for table in (
        tables.observations,
        tables.evidence_gaps,
        tables.hypotheses,
        tables.change_candidates,
    ):
        if table is not None and tuple(
            table.canonical_id(ref) for ref in table.provider_refs
        ) != table.canonical_ids:
            raise AnthropicOutputRefError(
                "Anthropic output ref inverse validation failed."
            )
    return tables


@dataclass(frozen=True)
class ValidationPlanRefTable:
    """Stage-C-local bijection from validated plan/candidate pairs to integers."""

    canonical_pairs: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        plan_pattern = re.compile(
            rf"^{re.escape(OUTPUT_ID_PREFIXES['validation'])}-\d{{{OUTPUT_ID_DIGITS}}}$"
        )
        change_pattern = re.compile(
            rf"^{re.escape(OUTPUT_ID_PREFIXES['change'])}-\d{{{OUTPUT_ID_DIGITS}}}$"
        )
        if any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not isinstance(pair[1], str)
            or plan_pattern.fullmatch(pair[0]) is None
            or change_pattern.fullmatch(pair[1]) is None
            for pair in self.canonical_pairs
        ):
            raise AnthropicValidationPlanRefError(
                "Anthropic ValidationPlan ref mapping contains an invalid canonical pair."
            )
        plan_ids = tuple(pair[0] for pair in self.canonical_pairs)
        candidate_ids = tuple(pair[1] for pair in self.canonical_pairs)
        if len(set(plan_ids)) != len(plan_ids) or len(set(candidate_ids)) != len(candidate_ids):
            raise AnthropicValidationPlanRefError(
                "Anthropic ValidationPlan ref mapping contains duplicate canonical IDs."
            )

    @property
    def provider_refs(self) -> tuple[int, ...]:
        return tuple(range(1, len(self.canonical_pairs) + 1))

    @property
    def digest(self) -> str:
        mapping = [
            {
                "validationPlanRef": reference,
                "validationPlanId": pair[0],
                "changeCandidateId": pair[1],
            }
            for reference, pair in zip(
                self.provider_refs, self.canonical_pairs, strict=True
            )
        ]
        return hashlib.sha256(_compact_json(mapping).encode("utf-8")).hexdigest()

    def provider_ref(self, validation_plan_id: Any, change_candidate_id: Any) -> int:
        pair = (validation_plan_id, change_candidate_id)
        if not all(isinstance(value, str) for value in pair):
            raise AnthropicValidationPlanRefError(
                "Anthropic canonical ValidationPlan reference must be an ID pair."
            )
        try:
            return self.canonical_pairs.index(pair) + 1
        except ValueError as error:
            raise AnthropicValidationPlanRefError(
                "Anthropic canonical ValidationPlan pair is not in the ref mapping."
            ) from error

    def canonical_pair(self, provider_ref: Any) -> tuple[str, str]:
        if not isinstance(provider_ref, int) or isinstance(provider_ref, bool):
            raise AnthropicValidationPlanRefError(
                "Anthropic provider ValidationPlan reference must be an integer."
            )
        if provider_ref < 1 or provider_ref > len(self.canonical_pairs):
            raise AnthropicValidationPlanRefError(
                "Anthropic provider ValidationPlan reference is outside the mapping."
            )
        return self.canonical_pairs[provider_ref - 1]


def _provider_metric_keys() -> frozenset[tuple[str, str, str]]:
    """Canonical provider keys; R4 additions are MonitorOnly candidates."""

    return target_metric_keys() | monitor_metric_keys()


def build_metric_alias_table() -> MetricAliasTable:
    """Build deterministic aliases from the actual exact metric registry."""

    table = MetricAliasTable(tuple(sorted(_provider_metric_keys())))
    if tuple(table.canonical_key(ref) for ref in table.provider_refs) != table.canonical_metric_keys:
        raise AnthropicMetricAliasError(
            "Anthropic metric alias inverse validation failed."
        )
    return table


def build_warning_alias_table(package: AnalysisPromptPackage) -> WarningAliasTable:
    """Build aliases from the existing canonical model-visible warning authority."""

    table = WarningAliasTable(collect_allowed_warning_codes(package))
    if tuple(table.canonical_code(ref) for ref in table.provider_refs) != table.canonical_warning_codes:
        raise AnthropicWarningAliasError(
            "Anthropic warning alias inverse validation failed."
        )
    return table


def _factual_evidence_items(
    package: AnalysisPromptPackage,
) -> list[Mapping[str, Any]]:
    document = to_external(package.source.evidence)
    items = document.get("evidenceItems")
    if not isinstance(items, list):
        raise AnthropicEvidenceAliasError(
            "Canonical evidence document is missing evidenceItems."
        )
    return [
        item
        for item in items
        if isinstance(item, Mapping)
        and is_evidence_item_eligible(item, EvidenceUse.FACTUAL_REFERENCE)
    ]


def build_evidence_alias_table(package: AnalysisPromptPackage) -> EvidenceAliasTable:
    """Build the deterministic alias table from validated C-1 evidence ordering."""

    items = _factual_evidence_items(package)
    canonical_ids: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise AnthropicEvidenceAliasError(
                "Canonical evidence contains an invalid item."
            )
        evidence_id = item.get("evidenceId")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise AnthropicEvidenceAliasError(
                "Canonical evidence contains an invalid Evidence ID."
            )
        canonical_ids.append(evidence_id)
    table = EvidenceAliasTable(tuple(canonical_ids))
    eligible_ids = {
        evidence_id
        for evidence_id, item in package.source.evidence_by_id.items()
        if is_evidence_item_eligible(item, EvidenceUse.FACTUAL_REFERENCE)
    }
    if set(table.canonical_evidence_ids) != eligible_ids:
        raise AnthropicEvidenceAliasError(
            "Canonical Evidence aliases do not match the loaded Evidence catalog."
        )
    if tuple(table.canonical_id(ref) for ref in table.provider_refs) != table.canonical_evidence_ids:
        raise AnthropicEvidenceAliasError(
            "Anthropic Evidence alias inverse validation failed."
        )
    return table


def decision_evidence_refs(
    package: AnalysisPromptPackage, table: EvidenceAliasTable
) -> tuple[int, ...]:
    """Subset of the factual namespace authorized for Stage B support."""

    return tuple(
        reference for reference in table.provider_refs
        if is_evidence_item_eligible(
            package.source.evidence_by_id[table.canonical_id(reference)],
            EvidenceUse.DECISION_SUPPORT,
        )
    )


def build_validation_plan_ref_table(
    stage_b: ValidatedStageB,
) -> ValidationPlanRefTable:
    """Build refs from every validated Stage-B candidate that declares a plan."""

    pairs = tuple(
        (candidate.validation_plan_id, candidate.id)
        for candidate in stage_b.change_candidates
        if candidate.validation_plan_id is not None
    )
    table = ValidationPlanRefTable(pairs)
    if tuple(table.canonical_pair(ref) for ref in table.provider_refs) != pairs:
        raise AnthropicValidationPlanRefError(
            "Anthropic ValidationPlan ref inverse validation failed."
        )
    return table


def _host_output_id(kind: str, index: int) -> str:
    return (
        f"{OUTPUT_ID_PREFIXES[kind]}-"
        f"{index + 1:0{OUTPUT_ID_DIGITS}d}"
    )


def _sequential_output_ref_table(kind: str, count: int) -> OutputRefTable:
    return OutputRefTable(
        kind, tuple(_host_output_id(kind, index) for index in range(count))
    )


def _maximum_provider_ref(values: Any) -> int:
    if not isinstance(values, list):
        return 0
    refs = [
        value for value in values
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    return max(refs, default=0)


def _infer_fixture_output_refs(
    flat: Mapping[str, Any], stage: str
) -> StageOutputRefTables:
    """Reference-test fallback; production always supplies validated ref tables."""

    if stage == "B":
        rows = flat.get("hypotheses")
        maximum = max(
            (
                _maximum_provider_ref(row.get("evidenceGapRefs"))
                for row in rows if isinstance(row, Mapping)
            ),
            default=0,
        ) if isinstance(rows, list) else 0
        return StageOutputRefTables(
            observations=_sequential_output_ref_table("observation", 0),
            evidence_gaps=_sequential_output_ref_table("gap", maximum),
        )
    if stage == "C":
        return StageOutputRefTables(
            observations=_sequential_output_ref_table(
                "observation", _maximum_provider_ref(flat.get("executiveObservationRefs"))
            ),
            evidence_gaps=_sequential_output_ref_table(
                "gap", _maximum_provider_ref(flat.get("executiveEvidenceGapRefs"))
            ),
            hypotheses=_sequential_output_ref_table(
                "hypothesis", _maximum_provider_ref(flat.get("executiveHypothesisRefs"))
            ),
            change_candidates=_sequential_output_ref_table(
                "change", _maximum_provider_ref(flat.get("executiveChangeCandidateRefs"))
            ),
        )
    raise ValueError(f"cannot infer output refs for stage {stage}")


def project_canonical_stage_b_authority(
    sections: Mapping[str, Any],
    output_refs: StageOutputRefTables,
) -> Mapping[str, Any]:
    """Remove Stage-B bookkeeping authority from the provider representation."""

    projected = deepcopy(dict(sections))
    hypotheses = projected.get("hypotheses")
    changes = projected.get("changeCandidates")
    if not isinstance(hypotheses, list) or not isinstance(changes, list):
        raise AnthropicOutputRefError(
            "Canonical Stage B sections have an invalid output-ref shape."
        )
    for index, row in enumerate(hypotheses):
        if not isinstance(row, dict) or row.pop("id", None) != _host_output_id(
            "hypothesis", index
        ):
            raise AnthropicOutputRefError(
                "Canonical Stage B hypothesis IDs are not host-normalized."
            )
        gap_ids = row.pop("evidenceGapIds", None)
        if not isinstance(gap_ids, list):
            raise AnthropicOutputRefError(
                "Canonical Stage B hypothesis gap references are invalid."
            )
        row["evidenceGapRefs"] = [
            output_refs.evidence_gaps.provider_ref(value) for value in gap_ids
        ]
    plan_index = 0
    for index, row in enumerate(changes):
        if not isinstance(row, dict) or row.pop("id", None) != _host_output_id(
            "change", index
        ):
            raise AnthropicOutputRefError(
                "Canonical Stage B change IDs are not host-normalized."
            )
        plan_id = row.pop("validationPlanId", None)
        if not isinstance(plan_id, str):
            raise AnthropicOutputRefError(
                "Canonical Stage B validation plan sentinel is invalid."
            )
        include_plan = bool(plan_id)
        if include_plan:
            expected = _host_output_id("validation", plan_index)
            if plan_id != expected:
                raise AnthropicOutputRefError(
                    "Canonical Stage B validation plan IDs are not host-normalized."
                )
            plan_index += 1
        row["includeValidationPlan"] = include_plan
    restored = restore_provider_stage_b_authority(projected, output_refs)
    if restored != sections:
        raise AnthropicOutputRefError(
            "Stage B host authority projection failed exact round-trip validation."
        )
    return projected


def restore_provider_stage_b_authority(
    sections: Mapping[str, Any],
    output_refs: StageOutputRefTables,
    *,
    reconstruction_diagnostics: bool = False,
) -> Mapping[str, Any]:
    """Assign canonical HYP/CHG/VAL IDs and restore Stage-A gap references."""

    restored = deepcopy(dict(sections))
    hypotheses = restored.get("hypotheses")
    changes = restored.get("changeCandidates")
    if not isinstance(hypotheses, list) or not isinstance(changes, list):
        if reconstruction_diagnostics:
            _raise_reconstruction_failure(
                "INVALID_SECTION_WRAPPER",
                section="stageEnvelope",
                component="stageBOutputAuthority",
            )
        raise AnthropicOutputRefError(
            "Provider Stage B sections have an invalid output-ref shape."
        )
    for index, row in enumerate(hypotheses):
        if not isinstance(row, dict) or "id" in row or "evidenceGapRefs" not in row:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "UNKNOWN_OUTPUT_REF",
                    section="hypotheses",
                    component="evidenceGapRefs",
                    item_index=index,
                )
            raise AnthropicOutputRefError(
                "Provider Stage B hypothesis output references are invalid."
            )
        gap_refs = row.pop("evidenceGapRefs")
        if not isinstance(gap_refs, list):
            raise AnthropicOutputRefError(
                "Provider Stage B EvidenceGap refs must be an array."
            )
        if all(isinstance(value, int) and not isinstance(value, bool) for value in gap_refs) and len(gap_refs) != len(set(gap_refs)):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "OUTPUT_REF_SET_MISMATCH",
                    section="hypotheses",
                    component="evidenceGapRefs",
                    item_index=index,
                    details={
                        "observedCount": len(gap_refs),
                        "uniqueObservedCount": len(set(gap_refs)),
                    },
                )
            raise AnthropicOutputRefError(
                "Provider Stage B EvidenceGap refs contain duplicates."
            )
        try:
            row["evidenceGapIds"] = [
                output_refs.evidence_gaps.canonical_id(value) for value in gap_refs
            ]
        except AnthropicOutputRefError:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "UNKNOWN_OUTPUT_REF",
                    section="hypotheses",
                    component="evidenceGapRefs",
                    item_index=index,
                    details={
                        "aliasDomainSize": len(
                            output_refs.evidence_gaps.provider_refs
                        )
                    },
                )
            raise
        row["id"] = _host_output_id("hypothesis", index)
    plan_index = 0
    for index, row in enumerate(changes):
        include_plan = row.pop("includeValidationPlan", None) if isinstance(row, dict) else None
        if not isinstance(row, dict) or "id" in row or not isinstance(
            include_plan, bool
        ):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "INVALID_SENTINEL",
                    section="changeCandidates",
                    component="includeValidationPlan",
                    item_index=index,
                )
            raise AnthropicOutputRefError(
                "Provider Stage B plan-presence state is invalid."
            )
        row["id"] = _host_output_id("change", index)
        row["validationPlanId"] = (
            _host_output_id("validation", plan_index) if include_plan else ""
        )
        if include_plan:
            plan_index += 1
    return restored


def _validation_plan_catalog(
    table: ValidationPlanRefTable,
    output_refs: StageOutputRefTables,
) -> list[Mapping[str, Any]]:
    if output_refs.change_candidates is None:
        raise AnthropicOutputRefError(
            "ValidationPlan catalog requires ChangeCandidate refs."
        )
    return [
        {
            "validationPlanRef": ref,
            "changeCandidateRef": output_refs.change_candidates.provider_ref(
                candidate_id
            ),
        }
        for ref, (_plan_id, candidate_id) in zip(
            table.provider_refs, table.canonical_pairs, strict=True
        )
    ]


def project_canonical_stage_validation_plan_refs(
    sections: Mapping[str, Any],
    table: ValidationPlanRefTable,
) -> Mapping[str, Any]:
    """Replace canonical Stage-C plan linkage with one exact provider ref."""

    projected = deepcopy(dict(sections))
    plans = projected.get("validationPlans")
    if not isinstance(plans, list):
        raise AnthropicValidationPlanRefError(
            "Canonical Stage C validationPlans must be an array."
        )
    for plan in plans:
        if not isinstance(plan, dict):
            raise AnthropicValidationPlanRefError(
                "Canonical Stage C contains an invalid ValidationPlan."
            )
        plan["validationPlanRef"] = table.provider_ref(
            plan.pop("id", None), plan.pop("changeCandidateId", None)
        )
    restored = restore_provider_stage_validation_plan_refs(projected, table)
    if restored != sections:
        raise AnthropicValidationPlanRefError(
            "ValidationPlan ref projection failed exact round-trip validation."
        )
    return projected


def restore_provider_stage_validation_plan_refs(
    sections: Mapping[str, Any],
    table: ValidationPlanRefTable,
    *,
    reconstruction_diagnostics: bool = False,
) -> Mapping[str, Any]:
    """Restore Stage-C plan IDs and enforce exact once-only ref coverage."""

    restored = deepcopy(dict(sections))
    plans = restored.get("validationPlans")
    if not isinstance(plans, list):
        if reconstruction_diagnostics:
            _raise_reconstruction_failure(
                "VALIDATION_PLAN_REF_SET_MISMATCH",
                section="validationPlans",
                component="validationPlanRefs",
                details={
                    "expectedCount": len(table.provider_refs),
                    "observedCount": 0,
                },
            )
        raise AnthropicValidationPlanRefError(
            "Provider Stage C validationPlans must be an array."
        )
    observed: list[int] = []
    for item_index, plan in enumerate(plans):
        if not isinstance(plan, dict) or "validationPlanRef" not in plan:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "UNKNOWN_VALIDATION_PLAN_REF",
                    section="validationPlans",
                    component="validationPlanRef",
                    item_index=item_index,
                    details={"aliasDomainSize": len(table.provider_refs)},
                )
            raise AnthropicValidationPlanRefError(
                "Provider Stage C contains an invalid ValidationPlan ref."
            )
        ref = plan.pop("validationPlanRef")
        try:
            plan_id, candidate_id = table.canonical_pair(ref)
        except AnthropicValidationPlanRefError:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "UNKNOWN_VALIDATION_PLAN_REF",
                    section="validationPlans",
                    component="validationPlanRef",
                    item_index=item_index,
                    details={"aliasDomainSize": len(table.provider_refs)},
                )
            raise
        observed.append(ref)
        plan["id"] = plan_id
        plan["changeCandidateId"] = candidate_id
    expected = table.provider_refs
    if len(observed) != len(expected) or set(observed) != set(expected):
        if reconstruction_diagnostics:
            _raise_reconstruction_failure(
                "VALIDATION_PLAN_REF_SET_MISMATCH",
                section="validationPlans",
                component="validationPlanRefs",
                details={
                    "expectedCount": len(expected),
                    "observedCount": len(observed),
                    "uniqueObservedCount": len(set(observed)),
                },
            )
        raise AnthropicValidationPlanRefError(
            "Provider Stage C ValidationPlan refs do not exactly cover the expected set."
        )
    return restored


_EXECUTIVE_REF_FIELDS = {
    "executiveObservationIds": ("executiveObservationRefs", "observations"),
    "executiveHypothesisIds": ("executiveHypothesisRefs", "hypotheses"),
    "executiveEvidenceGapIds": ("executiveEvidenceGapRefs", "evidence_gaps"),
    "executiveChangeCandidateIds": (
        "executiveChangeCandidateRefs",
        "change_candidates",
    ),
}


def project_canonical_stage_c_summary_refs(
    sections: Mapping[str, Any],
    output_refs: StageOutputRefTables,
) -> Mapping[str, Any]:
    """Project executive-summary selections into exact provider-local refs."""

    projected = deepcopy(dict(sections))
    for canonical_field, (provider_field, table_name) in _EXECUTIVE_REF_FIELDS.items():
        values = projected.pop(canonical_field, None)
        table = getattr(output_refs, table_name)
        if not isinstance(values, list) or table is None:
            raise AnthropicOutputRefError(
                "Canonical executive summary references are invalid."
            )
        projected[provider_field] = [table.provider_ref(value) for value in values]
    restored = restore_provider_stage_c_summary_refs(projected, output_refs)
    if restored != sections:
        raise AnthropicOutputRefError(
            "Executive summary ref projection failed exact round-trip validation."
        )
    return projected


def restore_provider_stage_c_summary_refs(
    sections: Mapping[str, Any],
    output_refs: StageOutputRefTables,
    *,
    reconstruction_diagnostics: bool = False,
) -> Mapping[str, Any]:
    """Restore exact executive-summary refs to canonical artifact-local IDs."""

    restored = deepcopy(dict(sections))
    for canonical_field, (provider_field, table_name) in _EXECUTIVE_REF_FIELDS.items():
        values = restored.pop(provider_field, None)
        table = getattr(output_refs, table_name)
        if not isinstance(values, list) or table is None:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "UNKNOWN_OUTPUT_REF",
                    section="executiveSummary",
                    component=provider_field,
                )
            raise AnthropicOutputRefError(
                "Provider executive summary references are invalid."
            )
        if all(isinstance(value, int) and not isinstance(value, bool) for value in values) and len(values) != len(set(values)):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "OUTPUT_REF_SET_MISMATCH",
                    section="executiveSummary",
                    component=provider_field,
                    details={
                        "observedCount": len(values),
                        "uniqueObservedCount": len(set(values)),
                    },
                )
            raise AnthropicOutputRefError(
                "Provider executive summary references contain duplicates."
            )
        try:
            restored[canonical_field] = [
                table.canonical_id(value) for value in values
            ]
        except AnthropicOutputRefError:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "UNKNOWN_OUTPUT_REF",
                    section="executiveSummary",
                    component=provider_field,
                    details={"aliasDomainSize": len(table.provider_refs)},
                )
            raise
    return restored


def project_compact_evidence_aliases(
    payload: Mapping[str, Any],
    aliases: EvidenceAliasTable,
) -> Mapping[str, Any]:
    """Replace compact canonical Evidence IDs with provider-local integer refs."""

    projected = deepcopy(dict(payload))
    rows = projected.get("evidence")
    if not isinstance(rows, list):
        raise AnthropicEvidenceAliasError("Compact evidence rows are invalid.")
    for row in rows:
        if not isinstance(row, dict) or "evidenceId" not in row:
            raise AnthropicEvidenceAliasError(
                "Compact evidence row is missing its canonical Evidence ID."
            )
        row["evidenceRef"] = aliases.provider_ref(row.pop("evidenceId"))
    restored = restore_compact_evidence_aliases(projected, aliases)
    if restored != payload:
        raise AnthropicEvidenceAliasError(
            "Provider Evidence alias projection failed exact round-trip validation."
        )
    return projected


def restore_compact_evidence_aliases(
    payload: Mapping[str, Any],
    aliases: EvidenceAliasTable,
) -> Mapping[str, Any]:
    """Restore a provider compact payload to its canonical compact representation."""

    restored = deepcopy(dict(payload))
    rows = restored.get("evidence")
    if not isinstance(rows, list):
        raise AnthropicEvidenceAliasError("Compact evidence rows are invalid.")
    for row in rows:
        if not isinstance(row, dict) or "evidenceRef" not in row or "evidenceId" in row:
            raise AnthropicEvidenceAliasError(
                "Provider compact evidence row has an invalid alias shape."
            )
        row["evidenceId"] = aliases.canonical_id(row.pop("evidenceRef"))
    return restored


def _project_warning_context_value(value: Any, aliases: WarningAliasTable) -> Any:
    """Project warning-bearing context fields without changing their semantics."""

    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, child in value.items():
            if key == "warningCodes":
                if not isinstance(child, list):
                    raise AnthropicWarningAliasError(
                        "Canonical warning context field must be an array."
                    )
                projected["warningRefs"] = [aliases.provider_ref(code) for code in child]
            elif key == "criticalWarnings":
                if not isinstance(child, list):
                    raise AnthropicWarningAliasError(
                        "Canonical critical warning context must be an array."
                    )
                rows: list[Any] = []
                for item in child:
                    if not isinstance(item, Mapping) or "code" not in item:
                        raise AnthropicWarningAliasError(
                            "Canonical critical warning context has an invalid row."
                        )
                    row = {
                        nested_key: _project_warning_context_value(nested_value, aliases)
                        for nested_key, nested_value in item.items()
                        if nested_key != "code"
                    }
                    row["warningRef"] = aliases.provider_ref(item["code"])
                    rows.append(row)
                projected[key] = rows
            else:
                projected[key] = _project_warning_context_value(child, aliases)
        return projected
    if isinstance(value, list):
        return [_project_warning_context_value(item, aliases) for item in value]
    return deepcopy(value)


def _restore_warning_context_value(value: Any, aliases: WarningAliasTable) -> Any:
    if isinstance(value, Mapping):
        restored: dict[str, Any] = {}
        for key, child in value.items():
            if key == "warningRefs":
                if not isinstance(child, list):
                    raise AnthropicWarningAliasError(
                        "Provider warning context field must be an array."
                    )
                restored["warningCodes"] = [aliases.canonical_code(ref) for ref in child]
            elif key == "criticalWarnings":
                if not isinstance(child, list):
                    raise AnthropicWarningAliasError(
                        "Provider critical warning context must be an array."
                    )
                rows: list[Any] = []
                for item in child:
                    if not isinstance(item, Mapping) or "warningRef" not in item:
                        raise AnthropicWarningAliasError(
                            "Provider critical warning context has an invalid row."
                        )
                    row = {
                        nested_key: _restore_warning_context_value(nested_value, aliases)
                        for nested_key, nested_value in item.items()
                        if nested_key != "warningRef"
                    }
                    row["code"] = aliases.canonical_code(item["warningRef"])
                    rows.append(row)
                restored[key] = rows
            else:
                restored[key] = _restore_warning_context_value(child, aliases)
        return restored
    if isinstance(value, list):
        return [_restore_warning_context_value(item, aliases) for item in value]
    return deepcopy(value)


def _warning_catalog(aliases: WarningAliasTable) -> list[Mapping[str, Any]]:
    """Expose canonical warning labels once while output authority stays integer-only."""

    return [
        {"warningRef": ref, "warningCode": code}
        for ref, code in zip(
            aliases.provider_refs, aliases.canonical_warning_codes, strict=True
        )
    ]


def project_compact_warning_aliases(
    payload: Mapping[str, Any], aliases: WarningAliasTable
) -> Mapping[str, Any]:
    projected = deepcopy(dict(payload))
    projected["qualitySets"] = _project_warning_context_value(
        projected.get("qualitySets"), aliases
    )
    projected["brief"] = _project_warning_context_value(
        projected.get("brief"), aliases
    )
    projected["warningCatalog"] = _warning_catalog(aliases)
    restored = deepcopy(dict(projected))
    restored.pop("warningCatalog", None)
    restored["qualitySets"] = _restore_warning_context_value(
        restored.get("qualitySets"), aliases
    )
    restored["brief"] = _restore_warning_context_value(restored.get("brief"), aliases)
    if restored != payload:
        raise AnthropicWarningAliasError(
            "Provider warning context projection failed exact round-trip validation."
        )
    return projected


def assert_provider_request_has_no_canonical_evidence_ids(
    request_identity: Mapping[str, Any],
    aliases: EvidenceAliasTable,
    *,
    stage: str,
) -> None:
    """Fail before preflight if an exact canonical Evidence ID leaks to Anthropic."""

    encoded = _compact_json(request_identity)
    if any(evidence_id in encoded for evidence_id in aliases.canonical_evidence_ids):
        raise AnthropicEvidenceAliasError(
            "Anthropic provider request contains a canonical Evidence ID.",
            details={"stage": stage},
        )


def assert_provider_metric_output_schema_is_aliased(
    tools: Sequence[Mapping[str, Any]],
    aliases: MetricAliasTable,
    *,
    stage: str,
    provider_refs: Sequence[int] | None = None,
) -> None:
    """Fail before preflight unless every B/C output metric path is an exact int enum."""

    if stage not in {"B", "C"}:
        return
    expected_enum = list(provider_refs or aliases.provider_refs)
    expected_nullable_enum = [_NULL_METRIC_REFERENCE, *expected_enum]
    found_fields: list[str] = []
    forbidden_fields: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                for name, schema in properties.items():
                    if name in {"targetMetricFamily", "targetMetric"}:
                        forbidden_fields.append(name)
                    if name in {
                        "targetMetricRef",
                        "metricRef",
                        "metricsToWatchRefs",
                        "guardrailMetricRefs",
                    }:
                        found_fields.append(name)
                        if name == "targetMetricRef":
                            valid_schema = (
                                isinstance(schema, Mapping)
                                and schema.get("type") == "integer"
                                and schema.get("enum") == expected_nullable_enum
                                and "items" not in schema
                            )
                        elif name == "metricRef":
                            valid_schema = (
                                isinstance(schema, Mapping)
                                and schema.get("type") == "integer"
                                and schema.get("enum") == expected_enum
                                and "items" not in schema
                            )
                        else:
                            items = (
                                schema.get("items")
                                if isinstance(schema, Mapping)
                                else None
                            )
                            valid_schema = (
                                isinstance(items, Mapping)
                                and items.get("type") == "integer"
                                and items.get("enum") == expected_enum
                            )
                        if not valid_schema:
                            raise AnthropicMetricAliasError(
                                "Anthropic output metric reference schema is not the exact integer alias enum.",
                                details={"stage": stage, "field": name},
                            )
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(tools)
    expected_count = 2 if stage == "B" else 3
    if forbidden_fields or len(found_fields) != expected_count:
        raise AnthropicMetricAliasError(
            "Anthropic output schema exposes an invalid metric reference namespace.",
            details={
                "stage": stage,
                "metricRefFieldCount": len(found_fields),
                "legacyMetricFieldCount": len(forbidden_fields),
            },
        )


def assert_provider_warning_output_schema_is_aliased(
    tools: Sequence[Mapping[str, Any]],
    aliases: WarningAliasTable,
    *,
    stage: str,
) -> None:
    """Fail before preflight unless A/B warning output is integer-alias only."""

    found_fields: list[str] = []
    forbidden_fields: list[str] = []
    expected_enum = list(aliases.provider_refs)

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                for name, schema in properties.items():
                    if name == "limitationWarningCodes":
                        forbidden_fields.append(name)
                    if name == "limitationWarningRefs":
                        found_fields.append(name)
                        items = schema.get("items") if isinstance(schema, Mapping) else None
                        valid = isinstance(items, Mapping) and items.get("type") == "integer"
                        if expected_enum:
                            valid = valid and items.get("enum") == expected_enum
                        else:
                            valid = (
                                valid
                                and "enum" not in items
                                and schema.get("maxItems") == 0
                            )
                        if not valid:
                            raise AnthropicWarningAliasError(
                                "Anthropic output warning reference schema is not the exact integer alias contract.",
                                details={"stage": stage, "field": name},
                            )
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)

    walk(tools)
    expected_count = 1 if stage == "A" else 2 if stage == "B" else 0
    if forbidden_fields or len(found_fields) != expected_count:
        raise AnthropicWarningAliasError(
            "Anthropic output schema exposes an invalid warning reference namespace.",
            details={
                "stage": stage,
                "warningRefFieldCount": len(found_fields),
                "legacyWarningFieldCount": len(forbidden_fields),
            },
        )


def _quality_key(item: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    status = item.get("status")
    warnings = item.get("warningCodes")
    if not isinstance(status, str) or not isinstance(warnings, list) or not all(
        isinstance(code, str) for code in warnings
    ):
        raise AnthropicRequestValidationError(
            "Canonical evidence has an invalid status or warningCodes value."
        )
    return status, tuple(warnings)


def build_compact_llm_payload(
    package: AnalysisPromptPackage,
) -> Mapping[str, Any]:
    """Factor repeated C-1 metadata without changing canonical evidence semantics."""

    evidence_document = to_external(package.source.evidence)
    items = _factual_evidence_items(package)
    if not items:
        raise AnthropicRequestValidationError(
            "Canonical evidence document has no selected evidence."
        )

    source_values: set[tuple[str, str, str]] = set()
    artifact_values: set[tuple[str, str]] = set()
    quality_values: set[tuple[str, tuple[str, ...]]] = set()
    for item in items:
        item_provenance = item.get("provenance")
        if not isinstance(item_provenance, Mapping):
            raise AnthropicRequestValidationError(
                "Canonical evidence provenance is invalid."
            )
        source_value = (
            item.get("sourceAnalysisType"),
            item_provenance.get("sourceBundleId"),
            item_provenance.get("sourceBundleDigest"),
        )
        if not all(isinstance(value, str) and value for value in source_value):
            raise AnthropicRequestValidationError(
                "Canonical evidence source identity is invalid."
            )
        source_values.add(source_value)
        artifact = item_provenance.get("sourceArtifact")
        artifact_digest = item_provenance.get("sourceArtifactSha256")
        if not isinstance(artifact, str) or not isinstance(artifact_digest, str):
            raise AnthropicRequestValidationError(
                "Canonical evidence artifact provenance is invalid."
            )
        artifact_values.add((artifact, artifact_digest))
        quality_values.add(_quality_key(item))

    artifact_refs = {
        value: f"A{index:03d}"
        for index, value in enumerate(sorted(artifact_values), start=1)
    }
    quality_refs = {
        value: f"Q{index:03d}"
        for index, value in enumerate(sorted(quality_values), start=1)
    }
    single_source = len(source_values) == 1
    source_refs = {
        value: f"S{index:03d}"
        for index, value in enumerate(sorted(source_values), start=1)
    }
    artifacts = {
        reference: {"path": value[0], "sha256": value[1]}
        for value, reference in artifact_refs.items()
    }
    quality_sets = {
        reference: {"status": value[0], "warningCodes": list(value[1])}
        for value, reference in quality_refs.items()
    }

    compact_items: list[dict[str, Any]] = []
    for item in items:
        item_provenance = item["provenance"]
        artifact_key = (
            item_provenance["sourceArtifact"],
            item_provenance["sourceArtifactSha256"],
        )
        compact = {
            key: deepcopy(value)
            for key, value in item.items()
            if key not in {
                "canonicalIdentity",
                "sourceAnalysisType",
                "status",
                "warningCodes",
                "provenance",
            }
        }
        compact.update({
            "artifactRef": artifact_refs[artifact_key],
            "qualitySetRef": quality_refs[_quality_key(item)],
            "sourceRowKey": item_provenance["sourceRowKey"],
        })
        if not single_source:
            compact["sourceRef"] = source_refs[(
                item["sourceAnalysisType"],
                item_provenance["sourceBundleId"],
                item_provenance["sourceBundleDigest"],
            )]
        compact_items.append(compact)

    compact_brief = model_visible_brief(package)
    compact_source: dict[str, Any] = {
        "identity": to_external(package.source.identity),
        "analysisBriefVersion": evidence_document.get("analysisBriefVersion"),
    }
    if single_source:
        source_analysis_type, source_bundle_id, source_bundle_digest = next(iter(source_values))
        compact_source.update({
            "sourceAnalysisType": source_analysis_type,
            "sourceBundleId": source_bundle_id,
            "sourceBundleDigest": source_bundle_digest,
        })
    else:
        compact_source["sourceBundles"] = {
            reference: {
                "sourceAnalysisType": value[0],
                "sourceBundleId": value[1],
                "sourceBundleDigest": value[2],
            }
            for value, reference in source_refs.items()
        }
    payload: dict[str, Any] = {
        "projectionVersion": ANTHROPIC_INPUT_PROJECTION_VERSION,
        "source": compact_source,
        "analysisObjective": package.request.analysis_objective,
        "outputLanguage": package.request.output_language,
        "artifacts": artifacts,
        "qualitySets": quality_sets,
        "brief": compact_brief,
        "evidence": compact_items,
    }
    reconstructed = list(expand_compact_evidence_items(payload))
    if reconstructed != items:
        raise AnthropicRequestValidationError(
            "Compact evidence projection failed exact canonical round-trip validation."
        )
    return payload


def expand_compact_evidence_items(
    payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Reconstruct exact canonical evidence rows for traceability verification."""

    source = payload.get("source")
    artifacts = payload.get("artifacts")
    quality_sets = payload.get("qualitySets")
    rows = payload.get("evidence")
    if not all(isinstance(value, Mapping) for value in (source, artifacts, quality_sets)):
        raise AnthropicRequestValidationError("Compact evidence dictionaries are invalid.")
    if not isinstance(rows, list):
        raise AnthropicRequestValidationError("Compact evidence rows are invalid.")
    identity = source.get("identity")
    if not isinstance(identity, Mapping):
        raise AnthropicRequestValidationError("Compact source identity is invalid.")
    mode = identity.get("mode")
    source_bundles = source.get("sourceBundles")
    result: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise AnthropicRequestValidationError("Compact evidence row is invalid.")
        if source_bundles is not None:
            source_entry = (
                source_bundles.get(row.get("sourceRef"))
                if isinstance(source_bundles, Mapping) else None
            )
            if not isinstance(source_entry, Mapping):
                raise AnthropicRequestValidationError("Compact source reference is invalid.")
        else:
            source_entry = source
        source_analysis_type = source_entry.get("sourceAnalysisType")
        bundle_id = source_entry.get("sourceBundleId")
        bundle_digest = source_entry.get("sourceBundleDigest")
        if not all(isinstance(value, str) and value for value in (
            source_analysis_type, bundle_id, bundle_digest,
        )):
            raise AnthropicRequestValidationError("Compact source identity is invalid.")
        artifact = artifacts.get(row.get("artifactRef"))
        quality = quality_sets.get(row.get("qualitySetRef"))
        if not isinstance(artifact, Mapping) or not isinstance(quality, Mapping):
            raise AnthropicRequestValidationError(
                "Compact evidence dictionary reference is invalid."
            )
        expanded = {
            key: deepcopy(value)
            for key, value in row.items()
            if key not in {"artifactRef", "qualitySetRef", "sourceRowKey", "sourceRef"}
        }
        expanded.update({
            "canonicalIdentity": [
                mode,
                source_analysis_type,
                row.get("domain"),
                row.get("metricFamily"),
                row.get("metric"),
                row.get("entityType") or "",
                row.get("entityKey") or "",
                row.get("dimension") or "",
                row.get("dimensionValue") or "",
            ],
            "sourceAnalysisType": source_analysis_type,
            "status": quality.get("status"),
            "warningCodes": deepcopy(quality.get("warningCodes")),
            "provenance": {
                "sourceBundleId": bundle_id,
                "sourceBundleDigest": bundle_digest,
                "sourceArtifact": artifact.get("path"),
                "sourceArtifactSha256": artifact.get("sha256"),
                "sourceRowKey": row.get("sourceRowKey"),
            },
        })
        # Restore the canonical stable field order used by C-1 serialization.
        canonical_order = (
            "evidenceId", "canonicalIdentity", "sourceAnalysisType", "domain",
            "metricFamily", "metric", "entityType", "entityKey", "dimension",
            "dimensionValue", "valueType", "unit", "observationUnit", "value",
            "status", "warningCodes", "sample", "priority", "core",
            "sourceDesignated", "authority", "provenance",
        )
        result.append({key: expanded[key] for key in canonical_order})
    return tuple(result)


def build_anthropic_prompt_parts(
    package: AnalysisPromptPackage,
) -> tuple[str, list[dict[str, str]], Mapping[str, Any]]:
    """Build the actual compact Anthropic system/message representation."""

    semantic, marker, _ = package.prompt.partition("# Response Contract")
    if not marker:
        raise AnthropicRequestValidationError(
            "The provider-neutral prompt is missing its response contract boundary."
        )
    system = semantic.rstrip() + "\n\n" + _strict_tool_instruction()
    payload = build_compact_llm_payload(package)
    user = (
        _DATA_MARKER
        + "\n\n"
        + _compact_json(payload)
        + "\n\n"
        + _DATA_END
        + "\n"
    )
    return system, [{"role": "user", "content": user}], payload


def _metric_catalog(
    aliases: MetricAliasTable,
    *,
    allowed_keys: frozenset[tuple[str, str, str]] | None = None,
) -> list[Mapping[str, Any]]:
    """Expose the exact registry only as a compact provider selection catalog."""

    return [
        {
            "metricRef": reference,
            "key": encode_metric_reference(*key),
        }
        for reference, key in zip(
            aliases.provider_refs, aliases.canonical_metric_keys, strict=True
        )
        if allowed_keys is None or key in allowed_keys
    ]


def _metric_key_from_reference(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, str):
        raise AnthropicMetricAliasError(
            "Anthropic canonical metric reference must be a string."
        )
    parts = value.split(_METRIC_SEPARATOR)
    key = tuple(parts)
    if len(parts) != 3 or key not in _provider_metric_keys():
        raise AnthropicMetricAliasError(
            "Anthropic canonical metric reference is not in the exact registry."
        )
    return key  # type: ignore[return-value]


def _metric_reference_from_key(key: tuple[str, str, str]) -> str:
    return encode_metric_reference(*key)


_STAGE_EVIDENCE_REFERENCE_FIELDS: Mapping[
    str, Mapping[str, Mapping[str, str]]
] = {
    "A": {
        "observations": {"evidenceIds": "evidenceRefs"},
        "interpretations": {
            "evidenceIds": "evidenceRefs",
            "limitationEvidenceIds": "limitationEvidenceRefs",
        },
        "evidenceGaps": {"relatedEvidenceIds": "relatedEvidenceRefs"},
    },
    "B": {
        "hypotheses": {
            "supportingEvidenceIds": "supportingEvidenceRefs",
            "counterEvidenceIds": "counterEvidenceRefs",
        },
        "changeCandidates": {
            "supportingEvidenceIds": "supportingEvidenceRefs",
            "counterEvidenceIds": "counterEvidenceRefs",
        },
    },
}

_STAGE_WARNING_REFERENCE_FIELDS: Mapping[str, Mapping[str, str]] = {
    "A": {"interpretations": "limitationWarningCodes"},
    "B": {
        "hypotheses": "limitationWarningCodes",
        "changeCandidates": "limitationWarningCodes",
    },
}

_COUNTER_EVIDENCE_SECTIONS = frozenset({"hypotheses", "changeCandidates"})


def _derive_counter_evidence_search_status(
    counter_evidence_ids: Sequence[Any],
) -> str:
    """Derive the redundant canonical status from selected counter evidence."""

    return (
        COUNTER_EVIDENCE_FOUND_STATUS
        if counter_evidence_ids
        else COUNTER_EVIDENCE_NOT_IDENTIFIED_STATUS
    )


def _map_evidence_reference_array(
    value: Any,
    mapper: Any,
) -> list[Any]:
    if not isinstance(value, list):
        raise AnthropicEvidenceAliasError(
            "Anthropic Evidence reference field must be an array."
        )
    return [mapper(item) for item in value]


def project_canonical_stage_evidence_aliases(
    sections: Mapping[str, Any],
    stage: str,
    aliases: EvidenceAliasTable,
) -> Mapping[str, Any]:
    """Project canonical A/B section Evidence fields into one alias namespace."""

    field_map = _STAGE_EVIDENCE_REFERENCE_FIELDS.get(stage)
    if field_map is None:
        if stage == "C":
            return deepcopy(dict(sections))
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    projected = deepcopy(dict(sections))
    for section, fields in field_map.items():
        rows = projected.get(section)
        if not isinstance(rows, list):
            raise AnthropicEvidenceAliasError(
                "Canonical analysis section has an invalid Evidence alias shape."
            )
        for row in rows:
            if not isinstance(row, dict):
                raise AnthropicEvidenceAliasError(
                    "Canonical analysis row has an invalid Evidence alias shape."
                )
            for canonical_field, provider_field in fields.items():
                if canonical_field not in row or provider_field in row:
                    raise AnthropicEvidenceAliasError(
                        "Canonical analysis row is missing an Evidence reference field."
                    )
                row[provider_field] = _map_evidence_reference_array(
                    row.pop(canonical_field), aliases.provider_ref
                )
            if stage == "B" and section in _COUNTER_EVIDENCE_SECTIONS:
                expected_status = _derive_counter_evidence_search_status(
                    row["counterEvidenceRefs"]
                )
                if row.pop("counterEvidenceSearchStatus", None) != expected_status:
                    raise AnthropicEvidenceAliasError(
                        "Canonical counter-evidence status does not match its Evidence IDs."
                    )
    restored = restore_provider_stage_evidence_aliases(projected, stage, aliases)
    if restored != sections:
        raise AnthropicEvidenceAliasError(
            "Prior-stage Evidence alias projection failed exact round-trip validation."
        )
    return projected


def restore_provider_stage_evidence_aliases(
    sections: Mapping[str, Any],
    stage: str,
    aliases: EvidenceAliasTable,
    *,
    reconstruction_diagnostics: bool = False,
) -> Mapping[str, Any]:
    """Restore provider-local A/B Evidence refs to canonical Evidence IDs."""

    field_map = _STAGE_EVIDENCE_REFERENCE_FIELDS.get(stage)
    if field_map is None:
        if stage == "C":
            return deepcopy(dict(sections))
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    restored = deepcopy(dict(sections))
    for section, fields in field_map.items():
        rows = restored.get(section)
        if not isinstance(rows, list):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "INVALID_SECTION_WRAPPER",
                    section=section,
                    component="evidenceAliases",
                    details={"observedType": type(rows).__name__},
                )
            raise AnthropicEvidenceAliasError(
                "Provider analysis section has an invalid Evidence alias shape."
            )
        for item_index, row in enumerate(rows):
            if not isinstance(row, dict):
                if reconstruction_diagnostics:
                    _raise_reconstruction_failure(
                        "INVALID_SECTION_ITEM_SHAPE",
                        section=section,
                        component="evidenceAliases",
                        item_index=item_index,
                        details={"observedType": type(row).__name__},
                    )
                raise AnthropicEvidenceAliasError(
                    "Provider analysis row has an invalid Evidence alias shape."
                )
            for canonical_field, provider_field in fields.items():
                if provider_field not in row or canonical_field in row:
                    if reconstruction_diagnostics:
                        _raise_reconstruction_failure(
                            (
                                "MISSING_REQUIRED_RECONSTRUCTION_FIELD"
                                if provider_field not in row
                                else "UNEXPECTED_RECONSTRUCTION_FIELD"
                            ),
                            section=section,
                            component=provider_field,
                            item_index=item_index,
                        )
                    raise AnthropicEvidenceAliasError(
                        "Provider analysis row is missing an Evidence alias field."
                    )
                try:
                    row[canonical_field] = _map_evidence_reference_array(
                        row.pop(provider_field), aliases.canonical_id
                    )
                except AnthropicEvidenceAliasError:
                    if reconstruction_diagnostics:
                        _raise_reconstruction_failure(
                            "UNKNOWN_EVIDENCE_ALIAS",
                            section=section,
                            component=provider_field,
                            item_index=item_index,
                            details={"aliasDomainSize": len(aliases.provider_refs)},
                        )
                    raise
            if stage == "B" and section in _COUNTER_EVIDENCE_SECTIONS:
                if "counterEvidenceSearchStatus" in row:
                    if reconstruction_diagnostics:
                        _raise_reconstruction_failure(
                            "UNEXPECTED_RECONSTRUCTION_FIELD",
                            section=section,
                            component="counterEvidenceSearchStatus",
                            item_index=item_index,
                        )
                    raise AnthropicEvidenceAliasError(
                        "Provider counter-evidence status must be host-derived."
                    )
                row["counterEvidenceSearchStatus"] = (
                    _derive_counter_evidence_search_status(
                        row["counterEvidenceIds"]
                    )
                )
    return restored


def project_canonical_stage_warning_aliases(
    sections: Mapping[str, Any],
    stage: str,
    aliases: WarningAliasTable,
) -> Mapping[str, Any]:
    """Project canonical A/B warning lists to provider-local integer refs."""

    field_map = _STAGE_WARNING_REFERENCE_FIELDS.get(stage)
    if field_map is None:
        if stage == "C":
            return deepcopy(dict(sections))
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    projected = deepcopy(dict(sections))
    for section, canonical_field in field_map.items():
        rows = projected.get(section)
        if not isinstance(rows, list):
            raise AnthropicWarningAliasError(
                "Canonical analysis section has an invalid warning alias shape."
            )
        for row in rows:
            if not isinstance(row, dict) or canonical_field not in row:
                raise AnthropicWarningAliasError(
                    "Canonical analysis row is missing a warning reference field."
                )
            value = row.pop(canonical_field)
            if not isinstance(value, list):
                raise AnthropicWarningAliasError(
                    "Canonical warning reference field must be an array."
                )
            row["limitationWarningRefs"] = [
                aliases.provider_ref(code) for code in value
            ]
    restored = restore_provider_stage_warning_aliases(projected, stage, aliases)
    if restored != sections:
        raise AnthropicWarningAliasError(
            "Provider warning alias projection failed exact round-trip validation."
        )
    return projected


def restore_provider_stage_warning_aliases(
    sections: Mapping[str, Any],
    stage: str,
    aliases: WarningAliasTable,
    *,
    reconstruction_diagnostics: bool = False,
) -> Mapping[str, Any]:
    """Restore provider-local A/B warning refs to canonical warning codes."""

    field_map = _STAGE_WARNING_REFERENCE_FIELDS.get(stage)
    if field_map is None:
        if stage == "C":
            return deepcopy(dict(sections))
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    restored = deepcopy(dict(sections))
    for section, canonical_field in field_map.items():
        rows = restored.get(section)
        if not isinstance(rows, list):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "INVALID_SECTION_WRAPPER",
                    section=section,
                    component="warningAliases",
                    details={"observedType": type(rows).__name__},
                )
            raise AnthropicWarningAliasError(
                "Provider analysis section has an invalid warning alias shape."
            )
        for item_index, row in enumerate(rows):
            if not isinstance(row, dict):
                if reconstruction_diagnostics:
                    _raise_reconstruction_failure(
                        "INVALID_SECTION_ITEM_SHAPE",
                        section=section,
                        component="warningAliases",
                        item_index=item_index,
                        details={"observedType": type(row).__name__},
                    )
                raise AnthropicWarningAliasError(
                    "Provider analysis row has an invalid warning alias shape."
                )
            if "limitationWarningRefs" not in row or canonical_field in row:
                if reconstruction_diagnostics:
                    _raise_reconstruction_failure(
                        (
                            "MISSING_REQUIRED_RECONSTRUCTION_FIELD"
                            if "limitationWarningRefs" not in row
                            else "UNEXPECTED_RECONSTRUCTION_FIELD"
                        ),
                        section=section,
                        component="limitationWarningRefs",
                        item_index=item_index,
                    )
                raise AnthropicWarningAliasError(
                    "Provider analysis row is missing a warning alias field."
                )
            refs = row.pop("limitationWarningRefs")
            if not isinstance(refs, list):
                if reconstruction_diagnostics:
                    _raise_reconstruction_failure(
                        "UNKNOWN_WARNING_ALIAS",
                        section=section,
                        component="limitationWarningRefs",
                        item_index=item_index,
                        details={"observedType": type(refs).__name__},
                    )
                raise AnthropicWarningAliasError(
                    "Provider warning reference field must be an array."
                )
            try:
                row[canonical_field] = [aliases.canonical_code(ref) for ref in refs]
            except AnthropicWarningAliasError:
                if reconstruction_diagnostics:
                    _raise_reconstruction_failure(
                        "UNKNOWN_WARNING_ALIAS",
                        section=section,
                        component="limitationWarningRefs",
                        item_index=item_index,
                        details={"aliasDomainSize": len(aliases.provider_refs)},
                    )
                raise
    return restored


def project_canonical_prior_warning_aliases(
    prior: Mapping[str, Any], aliases: WarningAliasTable
) -> Mapping[str, Any]:
    """Project validated A/B warning codes before they enter later stage context."""

    projected = deepcopy(dict(prior))
    for stage in ("A", "B"):
        applicable = {
            section: field
            for section, field in _STAGE_WARNING_REFERENCE_FIELDS[stage].items()
            if section in projected
        }
        if not applicable:
            continue
        for section, canonical_field in applicable.items():
            rows = projected.get(section)
            if not isinstance(rows, list):
                raise AnthropicWarningAliasError(
                    "Validated prior analysis has an invalid warning section."
                )
            for row in rows:
                if not isinstance(row, dict) or canonical_field not in row:
                    raise AnthropicWarningAliasError(
                        "Validated prior analysis has an invalid warning row."
                    )
                values = row.pop(canonical_field)
                if not isinstance(values, list):
                    raise AnthropicWarningAliasError(
                        "Validated prior warning field must be an array."
                    )
                row["limitationWarningRefs"] = [
                    aliases.provider_ref(code) for code in values
                ]
    restored = restore_provider_prior_warning_aliases(projected, aliases)
    if restored != prior:
        raise AnthropicWarningAliasError(
            "Prior analysis warning alias projection failed exact round-trip validation."
        )
    return projected


def restore_provider_prior_warning_aliases(
    prior: Mapping[str, Any], aliases: WarningAliasTable
) -> Mapping[str, Any]:
    restored = deepcopy(dict(prior))
    for stage in ("A", "B"):
        for section, canonical_field in _STAGE_WARNING_REFERENCE_FIELDS[stage].items():
            rows = restored.get(section)
            if rows is None:
                continue
            if not isinstance(rows, list):
                raise AnthropicWarningAliasError(
                    "Provider prior analysis has an invalid warning section."
                )
            for row in rows:
                if not isinstance(row, dict) or "limitationWarningRefs" not in row:
                    raise AnthropicWarningAliasError(
                        "Provider prior analysis has an invalid warning row."
                    )
                refs = row.pop("limitationWarningRefs")
                if not isinstance(refs, list):
                    raise AnthropicWarningAliasError(
                        "Provider prior warning field must be an array."
                    )
                row[canonical_field] = [aliases.canonical_code(ref) for ref in refs]
    return restored


_STAGE_METRIC_REFERENCE_ARRAY_FIELDS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "B": {"changeCandidates": ()},
    "C": {
        "validationPlans": (
            "metricsToWatchRefs",
            "guardrailMetricRefs",
        ),
    },
}


def _project_structured_metric_pairs(
    row: dict[str, Any],
    *,
    reference_field: str,
    value_field: str,
    provider_field: str,
    provider_value_field: str,
    aliases: MetricAliasTable,
) -> None:
    references = row.pop(reference_field, None)
    values = row.pop(value_field, None)
    if (
        not isinstance(references, list)
        or not isinstance(values, list)
        or len(references) != len(values)
    ):
        raise AnthropicMetricAliasError(
            "Canonical semantic metric pairs must have equal array lengths."
        )
    row[provider_field] = [
        {
            "metricRef": aliases.provider_ref(_metric_key_from_reference(reference)),
            provider_value_field: value,
        }
        for reference, value in zip(references, values, strict=True)
    ]


def _restore_structured_metric_pairs(
    row: dict[str, Any],
    *,
    provider_field: str,
    provider_value_field: str,
    reference_field: str,
    value_field: str,
    aliases: MetricAliasTable,
    section: str,
    item_index: int,
    component: str,
    reconstruction_diagnostics: bool,
) -> None:
    pairs = row.pop(provider_field, None)
    if not isinstance(pairs, list):
        if reconstruction_diagnostics:
            _raise_reconstruction_failure(
                "MISSING_REQUIRED_RECONSTRUCTION_FIELD"
                if pairs is None else "INVALID_SECTION_ITEM_SHAPE",
                section=section,
                component=provider_field,
                item_index=item_index,
                details={"observedType": type(pairs).__name__},
            )
        raise AnthropicMetricAliasError(
            "Provider semantic metric pairs must be an array."
        )
    references: list[str] = []
    values: list[Any] = []
    expected_keys = {"metricRef", provider_value_field}
    for pair_index, pair in enumerate(pairs):
        if not isinstance(pair, Mapping):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "INVALID_SECTION_ITEM_SHAPE",
                    section=section,
                    component=component,
                    item_index=item_index,
                    details={
                        "pairIndex": pair_index,
                        "observedType": type(pair).__name__,
                    },
                )
            raise AnthropicMetricAliasError(
                "Provider semantic metric pair must be an object."
            )
        keys = set(map(str, pair))
        missing = expected_keys - keys
        extra = keys - expected_keys
        if missing or extra:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "MISSING_REQUIRED_RECONSTRUCTION_FIELD"
                    if missing else "UNEXPECTED_RECONSTRUCTION_FIELD",
                    section=section,
                    component=component,
                    item_index=item_index,
                    details={
                        "pairIndex": pair_index,
                        "missingFieldCount": len(missing),
                        "unexpectedFieldCount": len(extra),
                    },
                )
            raise AnthropicMetricAliasError(
                "Provider semantic metric pair has an invalid object shape."
            )
        try:
            canonical_key = aliases.canonical_key(pair.get("metricRef"))
        except AnthropicMetricAliasError:
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "UNKNOWN_METRIC_ALIAS",
                    section=section,
                    component=component,
                    item_index=item_index,
                    details={
                        "pairIndex": pair_index,
                        "aliasDomainSize": len(aliases.provider_refs),
                    },
                )
            raise
        references.append(_metric_reference_from_key(canonical_key))
        values.append(pair.get(provider_value_field))
    row[reference_field] = references
    row[value_field] = values


def _project_metric_reference_array(
    value: Any,
    aliases: MetricAliasTable,
) -> list[int]:
    if not isinstance(value, list):
        raise AnthropicMetricAliasError(
            "Anthropic canonical metric reference field must be an array."
        )
    return [aliases.provider_ref(_metric_key_from_reference(item)) for item in value]


def _restore_metric_reference_array(
    value: Any,
    aliases: MetricAliasTable,
) -> list[str]:
    if not isinstance(value, list):
        raise AnthropicMetricAliasError(
            "Anthropic provider metric reference field must be an array."
        )
    return [_metric_reference_from_key(aliases.canonical_key(item)) for item in value]


def project_canonical_stage_metric_aliases(
    sections: Mapping[str, Any],
    stage: str,
    aliases: MetricAliasTable,
) -> Mapping[str, Any]:
    """Project flat B/C metric paths into one request-local integer namespace."""

    field_map = _STAGE_METRIC_REFERENCE_ARRAY_FIELDS.get(stage)
    if field_map is None:
        if stage == "A":
            return deepcopy(dict(sections))
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    projected = deepcopy(dict(sections))
    for section, fields in field_map.items():
        rows = projected.get(section)
        if not isinstance(rows, list):
            raise AnthropicMetricAliasError(
                "Canonical analysis section has an invalid metric alias shape."
            )
        for row in rows:
            if not isinstance(row, dict):
                raise AnthropicMetricAliasError(
                    "Canonical analysis row has an invalid metric alias shape."
                )
            for field in fields:
                row[field] = _project_metric_reference_array(row.get(field), aliases)
            if stage == "B":
                _project_structured_metric_pairs(
                    row,
                    reference_field="expectedMetricRefs",
                    value_field="expectedDirections",
                    provider_field="expectedObservables",
                    provider_value_field="direction",
                    aliases=aliases,
                )
                family = row.pop("targetMetricFamily", None)
                metric = row.pop("targetMetric", None)
                domain = row.get("targetDomain")
                if not all(isinstance(value, str) for value in (domain, family, metric)):
                    raise AnthropicMetricAliasError(
                        "Canonical change target has invalid metric transport fields."
                    )
                if bool(family) != bool(metric):
                    raise AnthropicMetricAliasError(
                        "Canonical change target has a partial metric reference."
                    )
                row["targetMetricRef"] = (
                    aliases.provider_ref((domain, family, metric))
                    if family and metric
                    else _NULL_METRIC_REFERENCE
                )
            elif stage == "C":
                _project_structured_metric_pairs(
                    row,
                    reference_field="rollbackMetricRefs",
                    value_field="rollbackConditions",
                    provider_field="rollbackIndicators",
                    provider_value_field="condition",
                    aliases=aliases,
                )
    restored = restore_provider_stage_metric_aliases(projected, stage, aliases)
    if restored != sections:
        raise AnthropicMetricAliasError(
            "Provider metric alias projection failed exact round-trip validation."
        )
    return projected


def restore_provider_stage_metric_aliases(
    sections: Mapping[str, Any],
    stage: str,
    aliases: MetricAliasTable,
    *,
    reconstruction_diagnostics: bool = False,
) -> Mapping[str, Any]:
    """Restore provider-local flat B/C metric refs to canonical flat strings."""

    field_map = _STAGE_METRIC_REFERENCE_ARRAY_FIELDS.get(stage)
    if field_map is None:
        if stage == "A":
            return deepcopy(dict(sections))
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    restored = deepcopy(dict(sections))
    for section, fields in field_map.items():
        rows = restored.get(section)
        if not isinstance(rows, list):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "INVALID_SECTION_WRAPPER",
                    section=section,
                    component="metricAliases",
                    details={"observedType": type(rows).__name__},
                )
            raise AnthropicMetricAliasError(
                "Provider analysis section has an invalid metric alias shape."
            )
        for item_index, row in enumerate(rows):
            if not isinstance(row, dict):
                if reconstruction_diagnostics:
                    _raise_reconstruction_failure(
                        "INVALID_SECTION_ITEM_SHAPE",
                        section=section,
                        component="metricAliases",
                        item_index=item_index,
                        details={"observedType": type(row).__name__},
                    )
                raise AnthropicMetricAliasError(
                    "Provider analysis row has an invalid metric alias shape."
                )
            for field in fields:
                if field not in row:
                    if reconstruction_diagnostics:
                        _raise_reconstruction_failure(
                            "MISSING_REQUIRED_RECONSTRUCTION_FIELD",
                            section=section,
                            component=field,
                            item_index=item_index,
                        )
                    raise AnthropicMetricAliasError(
                        "Provider analysis row is missing a metric alias field."
                    )
                try:
                    row[field] = _restore_metric_reference_array(row.get(field), aliases)
                except AnthropicMetricAliasError:
                    if reconstruction_diagnostics:
                        _raise_reconstruction_failure(
                            "UNKNOWN_METRIC_ALIAS",
                            section=section,
                            component=field,
                            item_index=item_index,
                            details={"aliasDomainSize": len(aliases.provider_refs)},
                        )
                    raise
            if stage == "B":
                _restore_structured_metric_pairs(
                    row,
                    provider_field="expectedObservables",
                    provider_value_field="direction",
                    reference_field="expectedMetricRefs",
                    value_field="expectedDirections",
                    aliases=aliases,
                    section=section,
                    item_index=item_index,
                    component="expectedObservableDirections",
                    reconstruction_diagnostics=reconstruction_diagnostics,
                )
                target_ref = row.pop("targetMetricRef", None)
                if not isinstance(target_ref, int) or isinstance(target_ref, bool):
                    if reconstruction_diagnostics:
                        _raise_reconstruction_failure(
                            "INVALID_TARGET_RECONSTRUCTION",
                            section=section,
                            component="targetMetricRef",
                            item_index=item_index,
                            details={
                                "observedType": type(target_ref).__name__,
                                "expectedType": "integer",
                            },
                        )
                    raise AnthropicMetricAliasError(
                        "Provider targetMetricRef must be an integer alias or null sentinel."
                    )
                if target_ref != _NULL_METRIC_REFERENCE:
                    try:
                        domain, family, metric = aliases.canonical_key(target_ref)
                    except AnthropicMetricAliasError:
                        if reconstruction_diagnostics:
                            _raise_reconstruction_failure(
                                "UNKNOWN_METRIC_ALIAS",
                                section=section,
                                component="targetMetricRef",
                                item_index=item_index,
                                details={"aliasDomainSize": len(aliases.provider_refs)},
                            )
                        raise
                    if row.get("targetDomain") != domain:
                        if reconstruction_diagnostics:
                            _raise_reconstruction_failure(
                                "INVALID_TARGET_RECONSTRUCTION",
                                section=section,
                                component="targetDomain",
                                item_index=item_index,
                                details={"reason": "METRIC_DOMAIN_MISMATCH"},
                            )
                        raise AnthropicMetricAliasError(
                            "Provider target domain does not match its metric alias."
                        )
                    row["targetMetricFamily"] = family
                    row["targetMetric"] = metric
                else:
                    row["targetMetricFamily"] = ""
                    row["targetMetric"] = ""
            elif stage == "C":
                _restore_structured_metric_pairs(
                    row,
                    provider_field="rollbackIndicators",
                    provider_value_field="condition",
                    reference_field="rollbackMetricRefs",
                    value_field="rollbackConditions",
                    aliases=aliases,
                    section=section,
                    item_index=item_index,
                    component="rollbackIndicators",
                    reconstruction_diagnostics=reconstruction_diagnostics,
                )
    return restored


def project_canonical_prior_metric_aliases(
    prior: Mapping[str, Any],
    aliases: MetricAliasTable,
) -> Mapping[str, Any]:
    """Project canonical Stage B metric objects before they enter Stage C context."""

    projected = deepcopy(dict(prior))
    rows = projected.get("changeCandidates")
    if not isinstance(rows, list):
        raise AnthropicMetricAliasError(
            "Validated prior change candidates have an invalid metric shape."
        )
    for row in rows:
        if not isinstance(row, dict):
            raise AnthropicMetricAliasError(
                "Validated prior change candidate has an invalid metric shape."
            )
        target = row.get("target")
        expected = row.get("expectedObservableDirections")
        if not isinstance(target, dict) or not isinstance(expected, list):
            raise AnthropicMetricAliasError(
                "Validated prior change candidate has invalid metric fields."
            )
        family = target.pop("metricFamily", None)
        metric = target.pop("metric", None)
        domain = target.get("domain")
        if bool(family) != bool(metric):
            raise AnthropicMetricAliasError(
                "Validated prior target has a partial metric reference."
            )
        target["metricRef"] = (
            aliases.provider_ref((domain, family, metric))
            if family and metric and isinstance(domain, str)
            else _NULL_METRIC_REFERENCE
        )
        target_type = target.get("targetType")
        if target_type in {"EvidenceMetric", "EvidenceEntity"}:
            if target.get("description") is not None:
                raise AnthropicMetricAliasError(
                    "Validated structured target description was not normalized."
                )
            # Structured identity is authoritative. Do not send an empty,
            # non-authoritative annotation back to the Stage C model.
            target.pop("description", None)
        projected_expected: list[dict[str, Any]] = []
        for item in expected:
            if not isinstance(item, Mapping):
                raise AnthropicMetricAliasError(
                    "Validated expected direction has an invalid metric shape."
                )
            projected_expected.append({
                "metricRef": aliases.provider_ref((
                    item.get("domain"), item.get("metricFamily"), item.get("metric")
                )),
                "direction": item.get("direction"),
            })
        row["expectedObservableDirections"] = projected_expected
    restored = restore_provider_prior_metric_aliases(projected, aliases)
    if restored != prior:
        raise AnthropicMetricAliasError(
            "Prior analysis metric alias projection failed exact round-trip validation."
        )
    return projected


def restore_provider_prior_metric_aliases(
    prior: Mapping[str, Any],
    aliases: MetricAliasTable,
) -> Mapping[str, Any]:
    restored = deepcopy(dict(prior))
    rows = restored.get("changeCandidates")
    if not isinstance(rows, list):
        raise AnthropicMetricAliasError(
            "Provider prior change candidates have an invalid metric shape."
        )
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("target"), dict):
            raise AnthropicMetricAliasError(
                "Provider prior change candidate has an invalid metric shape."
            )
        target = row["target"]
        metric_ref = target.pop("metricRef", None)
        if not isinstance(metric_ref, int) or isinstance(metric_ref, bool):
            raise AnthropicMetricAliasError(
                "Provider prior target metricRef must be an integer alias or null sentinel."
            )
        if metric_ref != _NULL_METRIC_REFERENCE:
            domain, family, metric = aliases.canonical_key(metric_ref)
            if target.get("domain") != domain:
                raise AnthropicMetricAliasError(
                    "Provider prior target domain does not match its metric alias."
                )
            target["metricFamily"] = family
            target["metric"] = metric
        else:
            target["metricFamily"] = None
            target["metric"] = None
        if target.get("targetType") in {"EvidenceMetric", "EvidenceEntity"}:
            target["description"] = None
        expected = row.get("expectedObservableDirections")
        if not isinstance(expected, list):
            raise AnthropicMetricAliasError(
                "Provider prior expected directions are invalid."
            )
        restored_expected: list[dict[str, Any]] = []
        for item in expected:
            if not isinstance(item, Mapping):
                raise AnthropicMetricAliasError(
                    "Provider prior expected direction is invalid."
                )
            domain, family, metric = aliases.canonical_key(item.get("metricRef"))
            restored_expected.append({
                "domain": domain,
                "metricFamily": family,
                "metric": metric,
                "direction": item.get("direction"),
            })
        row["expectedObservableDirections"] = restored_expected
    return restored


def _stage_c_prior_context_without_evidence_references(
    stage_a: ValidatedStageA,
    stage_b: ValidatedStageB,
) -> Mapping[str, Any]:
    """Provide derived prior analysis to Stage C without any Evidence namespace."""

    combined = deepcopy(dict(_combined_prior_context(stage_a, stage_b)))
    for stage in ("A", "B"):
        for section, fields in _STAGE_EVIDENCE_REFERENCE_FIELDS[stage].items():
            rows = combined.get(section)
            if not isinstance(rows, list):
                raise AnthropicEvidenceAliasError(
                    "Validated prior analysis has an invalid section shape."
                )
            for row in rows:
                if not isinstance(row, dict):
                    raise AnthropicEvidenceAliasError(
                        "Validated prior analysis has an invalid row shape."
                    )
                for canonical_field in fields:
                    row.pop(canonical_field, None)
    return combined


def _project_prior_output_refs(
    prior: Mapping[str, Any],
    output_refs: StageOutputRefTables,
    *,
    stage: str,
    validation_plan_refs: ValidationPlanRefTable | None = None,
) -> Mapping[str, Any]:
    """Replace copy-only prior output IDs with stage-local integer references."""

    projected = deepcopy(dict(prior))

    def replace_row_ids(section: str, field: str, table: OutputRefTable) -> None:
        rows = projected.get(section)
        if not isinstance(rows, list):
            raise AnthropicOutputRefError(
                "Validated prior analysis has an invalid output-ref section."
            )
        for row in rows:
            if not isinstance(row, dict) or "id" not in row:
                raise AnthropicOutputRefError(
                    "Validated prior analysis has an invalid output-ref row."
                )
            row[field] = table.provider_ref(row.pop("id"))

    replace_row_ids(
        "evidenceGaps", "evidenceGapRef", output_refs.evidence_gaps
    )
    if stage == "B":
        return projected
    if stage != "C" or output_refs.hypotheses is None or output_refs.change_candidates is None:
        raise AnthropicOutputRefError(
            "Stage C prior analysis is missing output-ref mappings."
        )
    replace_row_ids("observations", "observationRef", output_refs.observations)
    replace_row_ids("hypotheses", "hypothesisRef", output_refs.hypotheses)
    replace_row_ids(
        "changeCandidates", "changeCandidateRef", output_refs.change_candidates
    )
    for row in projected["hypotheses"]:
        gap_ids = row.pop("evidenceGapIds", None)
        if not isinstance(gap_ids, list):
            raise AnthropicOutputRefError(
                "Validated hypothesis gap references are invalid."
            )
        row["evidenceGapRefs"] = [
            output_refs.evidence_gaps.provider_ref(value) for value in gap_ids
        ]
    if validation_plan_refs is None:
        raise AnthropicOutputRefError(
            "Stage C prior analysis is missing ValidationPlan refs."
        )
    for row in projected["changeCandidates"]:
        plan_id = row.pop("validationPlanId", None)
        change_ref = row["changeCandidateRef"]
        change_id = output_refs.change_candidates.canonical_id(change_ref)
        row["validationPlanRef"] = (
            validation_plan_refs.provider_ref(plan_id, change_id)
            if plan_id is not None
            else 0
        )
    assessments = projected.pop("hostAssessmentsById", None)
    if not isinstance(assessments, dict):
        raise AnthropicOutputRefError(
            "Validated prior host assessments are invalid."
        )
    for section in ("interpretations", "hypotheses", "changeCandidates"):
        rows = projected.get(section)
        if not isinstance(rows, list):
            raise AnthropicOutputRefError(
                "Validated prior assessment section is invalid."
            )
        ref_field = {
            "interpretations": "id",
            "hypotheses": "hypothesisRef",
            "changeCandidates": "changeCandidateRef",
        }[section]
        table = {
            "hypotheses": output_refs.hypotheses,
            "changeCandidates": output_refs.change_candidates,
        }.get(section)
        for row in rows:
            if not isinstance(row, dict):
                raise AnthropicOutputRefError(
                    "Validated prior assessment row is invalid."
                )
            key = row.get(ref_field)
            canonical_id = (
                table.canonical_id(key) if table is not None else key
            )
            assessment = assessments.get(canonical_id)
            if assessment is not None:
                row["hostAssessment"] = deepcopy(assessment)
    return projected


def _stage_a_prior_context(stage_a: ValidatedStageA) -> Mapping[str, Any]:
    assessments = {
        item.id: {"evidenceStrength": item.evidence_strength}
        for item in stage_a.interpretations
    }
    return {
        **deepcopy(dict(stage_a.canonical_sections)),
        "hostAssessmentsById": assessments,
    }


def _stage_b_prior_context(stage_b: ValidatedStageB) -> Mapping[str, Any]:
    assessments: dict[str, Mapping[str, str]] = {
        item.id: {
            "evidenceStrength": item.evidence_strength,
            "evidenceStrengthScope": item.evidence_strength_scope,
        }
        for item in stage_b.hypotheses
    }
    assessments.update({
        item.id: {
            "evidenceStrength": item.evidence_strength,
            "evidenceStrengthScope": item.evidence_strength_scope,
            "actionability": item.actionability,
        }
        for item in stage_b.change_candidates
    })
    return {
        **deepcopy(dict(stage_b.canonical_sections)),
        "hostAssessmentsById": assessments,
    }


def _combined_prior_context(
    stage_a: ValidatedStageA,
    stage_b: ValidatedStageB,
) -> Mapping[str, Any]:
    """Flatten validated canonical sections while keeping host assessments separate."""

    stage_a_context = dict(_stage_a_prior_context(stage_a))
    stage_b_context = dict(_stage_b_prior_context(stage_b))
    stage_a_assessments = stage_a_context.pop("hostAssessmentsById")
    stage_b_assessments = stage_b_context.pop("hostAssessmentsById")
    return {
        **stage_a_context,
        **stage_b_context,
        "hostAssessmentsById": {
            **dict(stage_a_assessments),
            **dict(stage_b_assessments),
        },
    }


def build_anthropic_stage_context(
    package: AnalysisPromptPackage,
    stage: str,
    *,
    stage_a: ValidatedStageA | None = None,
    stage_b: ValidatedStageB | None = None,
    evidence_aliases: EvidenceAliasTable | None = None,
    metric_aliases: MetricAliasTable | None = None,
    warning_aliases: WarningAliasTable | None = None,
    validation_plan_refs: ValidationPlanRefTable | None = None,
    output_refs: StageOutputRefTables | None = None,
) -> Mapping[str, Any]:
    """Build one deterministic staged context without weakening C-1 authority."""

    if stage not in ANTHROPIC_STAGE_SPECS:
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    compact = build_compact_llm_payload(package)
    warning_aliases = warning_aliases or build_warning_alias_table(package)
    if stage in {"A", "B"}:
        if evidence_aliases is None:
            raise AnthropicEvidenceAliasError(
                f"Anthropic Stage {stage} requires an Evidence alias table."
            )
        provider_compact = project_compact_evidence_aliases(
            compact, evidence_aliases
        )
    else:
        provider_compact = compact
    provider_compact = project_compact_warning_aliases(
        provider_compact, warning_aliases
    )
    common = {
        "stageContextVersion": _ANTHROPIC_STAGE_CONTEXT_VERSIONS[stage],
        "stage": stage,
        "source": deepcopy(provider_compact["source"]),
        "analysisObjective": provider_compact["analysisObjective"],
        "outputLanguage": provider_compact["outputLanguage"],
        "brief": deepcopy(provider_compact["brief"]),
        "warningCatalog": deepcopy(provider_compact["warningCatalog"]),
    }
    if stage == "A":
        if stage_a is not None or stage_b is not None:
            raise AnthropicRequestValidationError(
                "Stage A context cannot contain prior-stage analysis."
            )
        return {
            **common,
            "projectionVersion": provider_compact["projectionVersion"],
            "artifacts": deepcopy(provider_compact["artifacts"]),
            "qualitySets": deepcopy(provider_compact["qualitySets"]),
            "evidence": deepcopy(provider_compact["evidence"]),
        }
    if stage == "B":
        if stage_a is None or stage_b is not None:
            raise AnthropicRequestValidationError(
                "Stage B context requires only validated Stage A analysis."
            )
        metric_aliases = metric_aliases or build_metric_alias_table()
        output_refs = output_refs or build_stage_output_ref_tables(stage_a)
        decision_refs = decision_evidence_refs(package, evidence_aliases)
        decision_ref_set = set(decision_refs)
        return {
            **common,
            "projectionVersion": provider_compact["projectionVersion"],
            "artifacts": deepcopy(provider_compact["artifacts"]),
            "qualitySets": deepcopy(provider_compact["qualitySets"]),
            "evidence": [
                deepcopy(item)
                for item in provider_compact["evidence"]
                if item.get("evidenceRef") in decision_ref_set
            ],
            "decisionEvidenceRefs": list(decision_refs),
            "metricCatalog": _metric_catalog(
                metric_aliases, allowed_keys=target_metric_keys()
            ),
            "validatedPriorAnalysis": _project_prior_output_refs(
                project_canonical_prior_warning_aliases(
                    project_canonical_stage_evidence_aliases(
                        _stage_a_prior_context(stage_a), "A", evidence_aliases
                    ),
                    warning_aliases,
                ),
                output_refs,
                stage="B",
            ),
        }
    if stage_a is None or stage_b is None:
        raise AnthropicRequestValidationError(
            "Stage C context requires validated Stage A and Stage B analysis."
        )
    metric_aliases = metric_aliases or build_metric_alias_table()
    validation_plan_refs = validation_plan_refs or build_validation_plan_ref_table(stage_b)
    output_refs = output_refs or build_stage_output_ref_tables(stage_a, stage_b)
    return {
        **common,
        "metricCatalog": _metric_catalog(metric_aliases),
        "validationPlanCatalog": _validation_plan_catalog(
            validation_plan_refs, output_refs
        ),
        "validatedPriorAnalysis": _project_prior_output_refs(
            project_canonical_prior_warning_aliases(
                project_canonical_prior_metric_aliases(
                    _stage_c_prior_context_without_evidence_references(stage_a, stage_b),
                    metric_aliases,
                ),
                warning_aliases,
            ),
            output_refs,
            stage="C",
            validation_plan_refs=validation_plan_refs,
        ),
    }


def build_anthropic_stage_prompt_parts(
    package: AnalysisPromptPackage,
    stage: str,
    *,
    stage_a: ValidatedStageA | None = None,
    stage_b: ValidatedStageB | None = None,
    evidence_aliases: EvidenceAliasTable | None = None,
    metric_aliases: MetricAliasTable | None = None,
    warning_aliases: WarningAliasTable | None = None,
    validation_plan_refs: ValidationPlanRefTable | None = None,
    output_refs: StageOutputRefTables | None = None,
) -> tuple[str, list[dict[str, str]], Mapping[str, Any]]:
    """Build the immutable system/messages/context for one production stage."""

    semantic, marker, _ = package.prompt.partition("# Response Contract")
    if not marker:
        raise AnthropicRequestValidationError(
            "The provider-neutral prompt is missing its response contract boundary."
        )
    payload = build_anthropic_stage_context(
        package,
        stage,
        stage_a=stage_a,
        stage_b=stage_b,
        evidence_aliases=evidence_aliases,
        metric_aliases=metric_aliases,
        warning_aliases=warning_aliases,
        validation_plan_refs=validation_plan_refs,
        output_refs=output_refs,
    )
    system = semantic.rstrip() + "\n\n" + _stage_strict_tool_instruction(stage)
    user = _DATA_MARKER + "\n\n" + _compact_json(payload) + "\n\n" + _DATA_END + "\n"
    return system, [{"role": "user", "content": user}], payload


def _stage_strict_tool_instruction(stage: str) -> str:
    sections = ANTHROPIC_STAGE_SPECS.get(stage)
    if sections is None:
        raise ValueError(f"unknown Anthropic analysis stage: {stage}")
    names = ", ".join(name for name, _fields in sections)
    def id_example(kind: str) -> str:
        return f"{OUTPUT_ID_PREFIXES[kind]}-{'0' * (OUTPUT_ID_DIGITS - 1)}1"

    focus = {
        "A": (
            "Frame direct observations, bounded interpretations, and evidence gaps. "
            "Keep three epistemic levels distinct: an observation reports what was directly measured; "
            "an association reports that observed facts co-occurred or differed in this sample; causality "
            "claims that one factor produced another outcome and is not established by observational Evidence. "
            "Allowed examples include 'The observed death rate was higher', 'Deaths were associated with shorter "
            "runs in this sample', and 'The evidence is consistent with a difference, but does not establish why'. "
            "Do not write conclusions such as 'Weapon X caused more deaths', 'Stage difficulty led to abandonment', "
            "or equivalent claims that one observed factor caused, drove, produced, explained, or was responsible "
            "for another. Apply this boundary to every observational domain, including stage, weapon, upgrade, "
            "post-run, content-version, and retention evidence. "
            "Factual-only Evidence may be cited here; it does not gain decision or target authority. "
            f"Canonical Evidence is the only factual authority. Return at most {MAX_OBSERVATIONS} "
            f"observations, {MAX_RESPONSE_SECTION_ITEMS} interpretations, and "
            f"{MAX_RESPONSE_SECTION_ITEMS} evidence gaps. IDs must be unique uppercase "
            f"{id_example('observation')}, {id_example('interpretation')}, and "
            f"{id_example('gap')} forms with exactly {OUTPUT_ID_DIGITS} digits. Evidence references "
            "are supplied as integer evidenceRef values; use only values present in the supplied "
            "catalog. Warning limitations use only integer warningRef values from warningCatalog; "
            "do not invent warning references. Every observation must cite at least one supplied reference. Do not put "
            "digits, percentages, counts, ratios, "
            "deltas, or other numeric facts in any freeform prose; the host renderer supplies them. "
            "An Evidence Gap may describe a possible causal relationship only as an unresolved "
            "question, uncertainty, or validation need. It must not state that relationship as an "
            "established fact. State what the supplied evidence cannot yet establish and what "
            "additional analysis or observation would distinguish alternative explanations. "
            "For retention, NewAttemptReturn is observed behavior, ObservedAppReturn is observed lifecycle activity, "
            "and ObservedUninstall is an observed app-instance removal; none proves engagement, churn, uninstall "
            "permanence, or the reason for an outcome. NoObservedReturn is bounded absence and RightCensored is "
            "insufficient observation."
        ),
        "B": (
            "Produce hypotheses and change candidates using the complete canonical Evidence. "
            "A causal possibility may appear only as an explicit, falsifiable hypothesis, using wording such as "
            "'One hypothesis is that early difficulty contributes to abandonment'. Never recast it as an observed "
            "fact or write that the data shows a factor causes an outcome. Hypothesis wording does not grant "
            "causal authority, and the supplied observational evidence remains non-causal. "
            "Only refs in decisionEvidenceRefs may support hypotheses or change candidates; "
            "if that catalog is empty, return empty hypothesis and candidate sections. "
            "Factual-only prior observations provide context but cannot justify a decision. "
            "validatedPriorAnalysis is derived context, not independent factual evidence. Return "
            f"at most {MAX_RESPONSE_SECTION_ITEMS} hypotheses and "
            f"{MAX_RESPONSE_SECTION_ITEMS} change candidates. The host assigns canonical hypothesis, "
            "change-candidate, and validation-plan IDs from returned array order; do not generate "
            "those bookkeeping IDs. Evidence references are integer evidenceRef values and "
            "must come from the supplied catalog. Metric references are integer metricRef values and must come from "
            "the supplied metricCatalog. Warning limitations use only integer warningRef values from warningCatalog; "
            "do not invent warning references. Evidence-gap references use only integer evidenceGapRef values from "
            "validated Stage A gaps. Set includeValidationPlan to select semantic plan presence; the host assigns its "
            "canonical ID, and the canonical validator still requires plans for actionable candidates. Do not put numeric facts "
            "in freeform prose: no measurements, counts, percentages, ratios, deltas, or tuning magnitudes. Use only "
            "the dedicated structured numeric fields when the canonical contract explicitly permits them. "
            "Every Evidence reference array must contain no more than twenty unique refs. "
            "Describe a proposed change only as an action to test, using tentative prospective wording; "
            "do not state that a metric caused another outcome or that the change will certainly improve it. "
            "For EvidenceMetric and EvidenceEntity targets, structured identity is authoritative and the host "
            "sets canonical description to null. conceptualTargetDescription is optional semantic context only "
            "for Conceptual targets; the host derives their required game-design-context flag. "
            "Select counter-evidence references only from the supplied Evidence catalog. Use an empty "
            "counter-evidence list when none is identified in the supplied brief; this does not prove that "
            "counter evidence does not exist. "
            "Risks describe possible adverse consequences or uncertainties of the proposed change. Use prospective, "
            "uncertain wording and do not state a causal consequence as established or certain."
        ),
        "C": (
            "Link validation plans and select executive-summary IDs from validated prior analysis. "
            "A validation plan may prospectively test a causal hypothesis by changing a factor and observing whether "
            "a metric changes. That plan does not make the existing observational evidence causal and must not be "
            "described as proof of an established cause. "
            "Only target-eligible metrics may be targets, guardrails, or rollback indicators. "
            "A supplied retentionEvidence metric may be used only in metricsToWatch as MonitorOnly "
            "when its authority says monitorOnlyEligible=true and runtimeComparison.decision=Allowed. "
            "It must never be used in a guardrail or rollback indicator. "
            "Do not add factual claims or numeric facts; use only integer metricRef values from metricCatalog. "
            f"Return at most {MAX_RESPONSE_SECTION_ITEMS} validation plans. Use each supplied integer "
            "validationPlanRef exactly once and do not invent plan or candidate IDs. Executive-summary selections use "
            "only the supplied integer observationRef, hypothesisRef, evidenceGapRef, and changeCandidateRef values. Select "
            "minimumEvidenceRequirements and rollback indicator conditions only from their closed schema values; "
            "do not invent numeric sample requirements. "
            f"Each executive-summary ref array contains at most {MAX_EXECUTIVE_SUMMARY_IDS} "
            "existing prior-stage refs. Do not put digits or numeric facts in qualitativeOverview."
        ),
    }[stage]
    return f"""# Anthropic Three-Stage Strict Tools Transport — Stage {stage}

Call every one of these tools exactly once in this response: {names}. Parallel calls are allowed and call order is irrelevant. Do not omit a tool even when its section is empty. Do not call any tool more than once and do not add explanatory text.

{focus}

The tool schemas use the flat C-2 wire DTO. Empty strings and explicit presence flags are transport sentinels; preserve them exactly. Provider output metric references must use only integer metricRef values present in metricCatalog; canonical metric keys are context labels, not output values. Do not return analysisVersion, sourceBriefIdentity, or comparisonDirectionAcknowledgement because the host injects them. The host validates this stage, reconstructs the final canonical response without repair, aliasing, case correction, or fuzzy matching, and applies the existing authoritative full C-2 validator before any artifact write. Human review is required and this response grants no modification or deployment authority."""


def _strict_tool_instruction() -> str:
    """Return the concise production strict-tool contract supplied to Anthropic."""

    names = ", ".join(name for name, _fields in ANTHROPIC_STRICT_TOOL_SECTIONS)
    return f"""# Anthropic Seven Strict Tools Transport

Call every one of these seven tools exactly once in this single response: {names}. Parallel calls are allowed and call order is irrelevant. Do not omit a tool even when its section is empty. Do not call any tool more than once and do not add explanatory text.

The tool schemas use the flat C-2 wire DTO. Empty strings and explicit presence flags are transport sentinels; preserve them exactly. Metric references must use exact case-sensitive domain::metricFamily::metric keys from the supplied registry. Do not return analysisVersion, sourceBriefIdentity, or comparisonDirectionAcknowledgement because the host injects them. The host performs exact tool-set validation, reconstructs the canonical response without repair or fuzzy matching, and applies the existing authoritative C-2 semantic validator."""


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _strict_json_loads(text: str, *, field: str) -> Any:
    try:
        value = json.loads(
            text,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        _assert_finite_json(value)
        return value
    except (json.JSONDecodeError, ValueError, TypeError):
        raise AnthropicMalformedResponseError(
            f"Anthropic serialized section {field} is not strict JSON."
        ) from None


def _assert_finite_json(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _assert_finite_json(item)


def serialize_canonical_response_sections(
    canonical: Mapping[str, Any],
) -> Mapping[str, str]:
    """Encode canonical sections for deterministic fixtures and reference tests."""

    result: dict[str, str] = {}
    for wire_field, (canonical_field, _root_type) in _SERIALIZED_SECTION_ROOTS.items():
        result[wire_field] = _compact_json(canonical[canonical_field])
    return result


def reconstruct_anthropic_serialized_response(
    envelope: Mapping[str, Any],
    package: AnalysisPromptPackage,
) -> Mapping[str, object]:
    """Parse only the wire syntax and rebuild the canonical validator input."""

    if set(envelope) != set(_SERIALIZED_SECTION_ROOTS):
        raise AnthropicMalformedResponseError(
            "Anthropic serialized envelope has an invalid top-level shape."
        )

    sections: dict[str, Any] = {}
    for wire_field, (canonical_field, root_type) in _SERIALIZED_SECTION_ROOTS.items():
        encoded = envelope.get(wire_field)
        if not isinstance(encoded, str):
            raise AnthropicMalformedResponseError(
                f"Anthropic serialized section {wire_field} must be a string."
            )
        decoded = _strict_json_loads(encoded, field=wire_field)
        if not isinstance(decoded, root_type):
            expected = "array" if root_type is list else "object"
            raise AnthropicMalformedResponseError(
                f"Anthropic serialized section {wire_field} must contain a JSON {expected}."
            )
        sections[canonical_field] = decoded

    identity = package.source.identity
    direction = (
        "candidateMinusBaseline"
        if identity.mode == "contentVersionCompare"
        else "notApplicable"
    )
    return {
        "analysisVersion": ANALYSIS_VERSION,
        "sourceBriefIdentity": {
            "semanticOutputDigest": identity.semantic_output_digest,
            "scopeHash": identity.scope_hash,
            "mode": identity.mode,
            "baselineContentVersion": identity.baseline_content_version,
            "candidateContentVersion": identity.candidate_content_version,
        },
        "comparisonDirectionAcknowledgement": direction,
        "executiveSummary": sections["executiveSummary"],
        "observations": sections["observations"],
        "interpretations": sections["interpretations"],
        "hypotheses": sections["hypotheses"],
        "evidenceGaps": sections["evidenceGaps"],
        "changeCandidates": sections["changeCandidates"],
        "validationPlans": sections["validationPlans"],
    }


def parse_anthropic_serialized_response_text(
    text: str,
    package: AnalysisPromptPackage,
) -> Mapping[str, object]:
    """Strictly decode the outer envelope and its serialized JSON sections."""

    outer = _strict_json_loads(text, field="outerEnvelope")
    if not isinstance(outer, Mapping):
        raise AnthropicMalformedResponseError(
            "Anthropic serialized envelope must be a JSON object."
        )
    return reconstruct_anthropic_serialized_response(outer, package)


def encode_metric_reference(domain: str, family: str, metric: str) -> str:
    values = (domain, family, metric)
    if any(not value or _METRIC_SEPARATOR in value for value in values):
        raise AnthropicMalformedResponseError("Anthropic metric reference is invalid.")
    key = tuple(values)
    if key not in _provider_metric_keys():
        raise AnthropicMalformedResponseError(
            "Anthropic metric reference is not in the canonical registry."
        )
    return _METRIC_SEPARATOR.join(values)


def flatten_canonical_response(canonical: Mapping[str, Any]) -> Mapping[str, Any]:
    """Encode the legacy flat DTO for reference-only regression tests."""

    executive = canonical["executiveSummary"]
    gaps = []
    for original in canonical["evidenceGaps"]:
        item = deepcopy(original)
        item["suggestedAnalysis"] = item["suggestedAnalysis"] or _NONE_SENTINEL
        gaps.append(item)
    changes = []
    for item in canonical["changeCandidates"]:
        target = item["target"]
        change = item["proposedChange"]
        expected = item["expectedObservableDirections"]
        amount = change["amountPercent"]
        changes.append({
            "id": item["id"],
            "domain": item["domain"],
            "targetType": target["targetType"],
            "targetDomain": target["domain"],
            "targetEntityType": target["entityType"] or "",
            "targetEntityKey": target["entityKey"] or "",
            "targetMetricFamily": target["metricFamily"] or "",
            "targetMetric": target["metric"] or "",
            "conceptualTargetDescription": (
                target["description"] or ""
                if target["targetType"] == "Conceptual"
                else ""
            ),
            "structuredTargetRequiresGameDesignContext": (
                target["requiresGameDesignContext"]
                if target["targetType"] in {"EvidenceMetric", "EvidenceEntity"}
                else False
            ),
            "actionType": item["actionType"],
            "changeDescription": change["description"],
            "changeParameter": change["parameter"] or "",
            "changeDirection": change["direction"],
            "changeAmountPercentPresent": amount is not None,
            "changeAmountPercent": amount if amount is not None else 0,
            "changeHeuristic": change["heuristic"],
            "changeMagnitudeBasis": change["magnitudeBasis"] or "",
            "rationale": item["rationale"],
            "supportingEvidenceIds": deepcopy(item["supportingEvidenceIds"]),
            "counterEvidenceIds": deepcopy(item["counterEvidenceIds"]),
            "counterEvidenceSearchStatus": item["counterEvidenceSearchStatus"],
            "limitationWarningCodes": deepcopy(item["limitationWarningCodes"]),
            "risks": deepcopy(item["risks"]),
            "expectedMetricRefs": [
                encode_metric_reference(value["domain"], value["metricFamily"], value["metric"])
                for value in expected
            ],
            "expectedDirections": [value["direction"] for value in expected],
            "validationPlanId": item["validationPlanId"] or "",
        })
    plans = []
    for item in canonical["validationPlans"]:
        plans.append({
            "id": item["id"],
            "changeCandidateId": item["changeCandidateId"],
            "analysesToRerun": deepcopy(item["analysesToRerun"]),
            "metricsToWatchRefs": [
                encode_metric_reference(value["domain"], value["metricFamily"], value["metric"])
                for value in item["metricsToWatch"]
            ],
            "guardrailMetricRefs": [
                encode_metric_reference(value["domain"], value["metricFamily"], value["metric"])
                for value in item["guardrailMetrics"]
            ],
            "minimumEvidenceRequirements": deepcopy(item["minimumEvidenceRequirements"]),
            "comparisonPlan": item["comparisonPlan"],
            "rollbackMetricRefs": [
                encode_metric_reference(
                    value["metric"]["domain"],
                    value["metric"]["metricFamily"],
                    value["metric"]["metric"],
                )
                for value in item["rollbackIndicators"]
            ],
            "rollbackConditions": [
                value["condition"] for value in item["rollbackIndicators"]
            ],
        })
    return {
        "executiveQualitativeOverview": executive["qualitativeOverview"],
        "executiveObservationIds": deepcopy(executive["observationIds"]),
        "executiveHypothesisIds": deepcopy(executive["hypothesisIds"]),
        "executiveEvidenceGapIds": deepcopy(executive["evidenceGapIds"]),
        "executiveChangeCandidateIds": deepcopy(executive["changeCandidateIds"]),
        "observations": deepcopy(canonical["observations"]),
        "interpretations": deepcopy(canonical["interpretations"]),
        "hypotheses": deepcopy(canonical["hypotheses"]),
        "evidenceGaps": gaps,
        "changeCandidates": changes,
        "validationPlans": plans,
    }


def _decode_metric_reference(value: Any) -> dict[str, str]:
    if not isinstance(value, str):
        raise AnthropicMalformedResponseError("Anthropic metric reference is invalid.")
    parts = value.split(_METRIC_SEPARATOR)
    if len(parts) != 3 or tuple(parts) not in _provider_metric_keys():
        raise AnthropicMalformedResponseError(
            "Anthropic metric reference is not in the canonical registry."
        )
    return {"domain": parts[0], "metricFamily": parts[1], "metric": parts[2]}


def _nullable_string(
    value: Any,
    field: str,
    *,
    section: str | None = None,
    item_index: int | None = None,
    component: str | None = None,
) -> str | None:
    if not isinstance(value, str):
        if section is not None:
            _raise_reconstruction_failure(
                "INVALID_SENTINEL",
                section=section,
                component=component or field,
                item_index=item_index,
                details={
                    "field": field,
                    "expectedType": "str",
                    "observedType": type(value).__name__,
                },
            )
        raise AnthropicMalformedResponseError(f"Anthropic {field} must be a string.")
    return None if value == "" else value


def _parallel_metrics(
    refs: Any,
    values: Any | None = None,
    *,
    section: str | None = None,
    item_index: int | None = None,
    component: str = "metricReferences",
) -> tuple[list[dict[str, str]], list[Any] | None]:
    if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
        if section is not None:
            _raise_reconstruction_failure(
                "INVALID_METRIC_RECONSTRUCTION",
                section=section,
                component=component,
                item_index=item_index,
                details={
                    "expectedType": "list[str]",
                    "observedType": type(refs).__name__,
                },
            )
        raise AnthropicMalformedResponseError("Anthropic metric reference array is invalid.")
    try:
        decoded = [_decode_metric_reference(item) for item in refs]
    except AnthropicMalformedResponseError:
        if section is not None:
            _raise_reconstruction_failure(
                "INVALID_METRIC_RECONSTRUCTION",
                section=section,
                component=component,
                item_index=item_index,
                details={"metricCount": len(refs)},
            )
        raise
    if values is None:
        return decoded, None
    if not isinstance(values, list) or len(values) != len(refs):
        if section is not None:
            _raise_reconstruction_failure(
                "PARALLEL_LENGTH_MISMATCH",
                section=section,
                component=component,
                item_index=item_index,
                details={
                    "leftCount": len(refs),
                    "rightCount": len(values) if isinstance(values, list) else None,
                    "rightObservedType": type(values).__name__,
                },
            )
        raise AnthropicMalformedResponseError(
            "Anthropic parallel metric arrays have different lengths."
        )
    return decoded, list(values)


def _flat_fields_for_sections(
    sections: Sequence[tuple[str, Sequence[str]]],
) -> tuple[str, ...]:
    return tuple(field for _name, fields in sections for field in fields)


def _reconstruction_string(
    item: Mapping[str, Any],
    field: str,
    *,
    section: str,
    item_index: int,
    component: str,
    diagnostics: bool,
) -> str | None:
    return _nullable_string(
        item.get(field),
        field,
        section=section if diagnostics else None,
        item_index=item_index,
        component=component,
    )


def _reconstruct_expected_directions(
    item: Mapping[str, Any],
    item_index: int,
    *,
    diagnostics: bool,
) -> list[dict[str, Any]]:
    refs, directions = _parallel_metrics(
        item.get("expectedMetricRefs"),
        item.get("expectedDirections"),
        section="changeCandidates" if diagnostics else None,
        item_index=item_index,
        component="expectedMetricDirections",
    )
    try:
        return [
            {**metric, "direction": direction}
            for metric, direction in zip(refs, directions or [], strict=True)
        ]
    except (TypeError, ValueError):
        if diagnostics:
            _raise_reconstruction_failure(
                "INVALID_EXPECTED_DIRECTION_RECONSTRUCTION",
                section="changeCandidates",
                component="expectedMetricDirections",
                item_index=item_index,
                details={
                    "metricCount": len(refs),
                    "directionCount": len(directions or []),
                },
            )
        raise AnthropicMalformedResponseError(
            "Anthropic expected directions could not be reconstructed."
        ) from None


def _reconstruct_change_target(
    item: Mapping[str, Any],
    item_index: int,
    *,
    diagnostics: bool,
) -> Mapping[str, Any]:
    try:
        target_type = item.get("targetType")
        conceptual_description = _reconstruction_string(
            item, "conceptualTargetDescription", section="changeCandidates",
            item_index=item_index, component="changeTarget", diagnostics=diagnostics,
        )
        structured_requires_context = item.get(
            "structuredTargetRequiresGameDesignContext"
        )
        if not isinstance(structured_requires_context, bool):
            if diagnostics:
                _raise_reconstruction_failure(
                    "INVALID_TARGET_RECONSTRUCTION",
                    section="changeCandidates",
                    component="changeTarget",
                    item_index=item_index,
                    details={
                        "field": "structuredTargetRequiresGameDesignContext",
                        "observedType": type(structured_requires_context).__name__,
                        "expectedType": "boolean",
                    },
                )
            raise AnthropicMalformedResponseError(
                "Anthropic structured target context flag must be a boolean."
            )
        return {
            "targetType": target_type,
            "domain": item.get("targetDomain"),
            "entityType": _reconstruction_string(
                item, "targetEntityType", section="changeCandidates",
                item_index=item_index, component="changeTarget", diagnostics=diagnostics,
            ),
            "entityKey": _reconstruction_string(
                item, "targetEntityKey", section="changeCandidates",
                item_index=item_index, component="changeTarget", diagnostics=diagnostics,
            ),
            "metricFamily": _reconstruction_string(
                item, "targetMetricFamily", section="changeCandidates",
                item_index=item_index, component="changeTarget", diagnostics=diagnostics,
            ),
            "metric": _reconstruction_string(
                item, "targetMetric", section="changeCandidates",
                item_index=item_index, component="changeTarget", diagnostics=diagnostics,
            ),
            "description": (
                conceptual_description if target_type == "Conceptual" else None
            ),
            "requiresGameDesignContext": (
                True
                if target_type == "Conceptual"
                else structured_requires_context
            ),
        }
    except _ReconstructionInvariantError:
        raise
    except Exception:
        if diagnostics:
            _raise_reconstruction_failure(
                "INVALID_TARGET_RECONSTRUCTION",
                section="changeCandidates",
                component="changeTarget",
                item_index=item_index,
            )
        raise


def _reconstruct_proposed_change(
    item: Mapping[str, Any],
    item_index: int,
    *,
    diagnostics: bool,
) -> Mapping[str, Any]:
    present = item.get("changeAmountPercentPresent")
    amount = item.get("changeAmountPercent")
    if (
        not isinstance(present, bool)
        or not isinstance(amount, (int, float))
        or isinstance(amount, bool)
    ):
        if diagnostics:
            _raise_reconstruction_failure(
                "INVALID_AMOUNT_SENTINEL_STATE",
                section="changeCandidates",
                component="changeAmountPercent",
                item_index=item_index,
                details={
                    "presenceType": type(present).__name__,
                    "amountType": type(amount).__name__,
                    "state": "INVALID_TYPES",
                },
            )
        raise AnthropicMalformedResponseError(
            "Anthropic change amount transport fields are invalid."
        )
    if not present and amount != 0:
        if diagnostics:
            _raise_reconstruction_failure(
                "INVALID_AMOUNT_SENTINEL_STATE",
                section="changeCandidates",
                component="changeAmountPercent",
                item_index=item_index,
                details={"state": "ABSENT_WITH_NONZERO_SENTINEL"},
            )
        raise AnthropicMalformedResponseError(
            "Anthropic null amount sentinel must use numeric zero."
        )
    try:
        return {
            "description": item.get("changeDescription"),
            "parameter": _reconstruction_string(
                item, "changeParameter", section="changeCandidates",
                item_index=item_index, component="proposedChange", diagnostics=diagnostics,
            ),
            "direction": item.get("changeDirection"),
            "amountPercent": amount if present else None,
            "heuristic": item.get("changeHeuristic"),
            "magnitudeBasis": _reconstruction_string(
                item, "changeMagnitudeBasis", section="changeCandidates",
                item_index=item_index, component="proposedChange", diagnostics=diagnostics,
            ),
        }
    except _ReconstructionInvariantError:
        raise
    except Exception:
        if diagnostics:
            _raise_reconstruction_failure(
                "INVALID_PROPOSED_CHANGE_RECONSTRUCTION",
                section="changeCandidates",
                component="proposedChange",
                item_index=item_index,
            )
        raise


def _reconstruct_change_candidate(
    item: Mapping[str, Any],
    item_index: int,
    *,
    diagnostics: bool,
) -> Mapping[str, Any]:
    try:
        return {
            "id": item.get("id"),
            "domain": item.get("domain"),
            "target": _reconstruct_change_target(
                item, item_index, diagnostics=diagnostics
            ),
            "actionType": item.get("actionType"),
            "proposedChange": _reconstruct_proposed_change(
                item, item_index, diagnostics=diagnostics
            ),
            "rationale": item.get("rationale"),
            "supportingEvidenceIds": item.get("supportingEvidenceIds"),
            "counterEvidenceIds": item.get("counterEvidenceIds"),
            "counterEvidenceSearchStatus": item.get("counterEvidenceSearchStatus"),
            "limitationWarningCodes": item.get("limitationWarningCodes"),
            "risks": item.get("risks"),
            "expectedObservableDirections": _reconstruct_expected_directions(
                item, item_index, diagnostics=diagnostics
            ),
            "validationPlanId": _reconstruction_string(
                item, "validationPlanId", section="changeCandidates",
                item_index=item_index, component="changeCandidate", diagnostics=diagnostics,
            ),
        }
    except _ReconstructionInvariantError:
        raise
    except Exception:
        if diagnostics:
            _raise_reconstruction_failure(
                "CANONICAL_SECTION_CONSTRUCTION_FAILED",
                section="changeCandidates",
                component="changeCandidate",
                item_index=item_index,
            )
        raise


def _reconstruct_validation_plan(
    item: Mapping[str, Any],
    item_index: int,
    *,
    diagnostics: bool,
) -> Mapping[str, Any]:
    watch, _ = _parallel_metrics(
        item.get("metricsToWatchRefs"),
        section="validationPlans" if diagnostics else None,
        item_index=item_index,
        component="metricsToWatch",
    )
    guards, _ = _parallel_metrics(
        item.get("guardrailMetricRefs"),
        section="validationPlans" if diagnostics else None,
        item_index=item_index,
        component="guardrailMetrics",
    )
    rollback, conditions = _parallel_metrics(
        item.get("rollbackMetricRefs"),
        item.get("rollbackConditions"),
        section="validationPlans" if diagnostics else None,
        item_index=item_index,
        component="rollbackIndicators",
    )
    return {
        "id": item.get("id"),
        "changeCandidateId": item.get("changeCandidateId"),
        "analysesToRerun": item.get("analysesToRerun"),
        "metricsToWatch": watch,
        "guardrailMetrics": guards,
        "minimumEvidenceRequirements": item.get("minimumEvidenceRequirements"),
        "comparisonPlan": item.get("comparisonPlan"),
        "rollbackIndicators": [
            {"metric": metric, "condition": condition}
            for metric, condition in zip(rollback, conditions or [], strict=True)
        ],
    }


def reconstruct_anthropic_flat_sections(
    flat: Mapping[str, Any],
    stage: str,
    *,
    evidence_aliases: EvidenceAliasTable | None = None,
    metric_aliases: MetricAliasTable | None = None,
    warning_aliases: WarningAliasTable | None = None,
    validation_plan_refs: ValidationPlanRefTable | None = None,
    output_refs: StageOutputRefTables | None = None,
    reconstruction_diagnostics: bool = False,
) -> Mapping[str, Any]:
    """Reconstruct only the canonical response sections owned by one stage."""

    try:
        spec = (
            ANTHROPIC_STAGE_SPECS[stage]
            if stage == "C" and output_refs is None
            else ANTHROPIC_PROVIDER_STAGE_SPECS[stage]
        )
    except KeyError as error:
        raise ValueError(f"unknown Anthropic analysis stage: {stage}") from error
    expected = set(_flat_fields_for_sections(spec))
    if set(flat) != expected:
        if reconstruction_diagnostics:
            missing_count = len(expected - set(flat))
            unexpected_count = len(set(flat) - expected)
            _raise_reconstruction_failure(
                (
                    "MISSING_REQUIRED_RECONSTRUCTION_FIELD"
                    if missing_count
                    else "UNEXPECTED_RECONSTRUCTION_FIELD"
                ),
                section="stageEnvelope",
                component="topLevelFields",
                details={
                    "missingFieldCount": missing_count,
                    "unexpectedFieldCount": unexpected_count,
                },
            )
        raise AnthropicMalformedResponseError(
            "Anthropic staged flat response has an invalid top-level shape."
        )

    if stage in {"A", "B"} and evidence_aliases is not None:
        flat = restore_provider_stage_evidence_aliases(
            flat,
            stage,
            evidence_aliases,
            reconstruction_diagnostics=reconstruction_diagnostics,
        )
    if stage in {"A", "B"} and warning_aliases is not None:
        flat = restore_provider_stage_warning_aliases(
            flat,
            stage,
            warning_aliases,
            reconstruction_diagnostics=reconstruction_diagnostics,
        )
    if stage in {"B", "C"} and metric_aliases is not None:
        flat = restore_provider_stage_metric_aliases(
            flat,
            stage,
            metric_aliases,
            reconstruction_diagnostics=reconstruction_diagnostics,
        )
    if stage == "C" and validation_plan_refs is not None:
        flat = restore_provider_stage_validation_plan_refs(
            flat,
            validation_plan_refs,
            reconstruction_diagnostics=reconstruction_diagnostics,
        )
    if stage == "B" and output_refs is not None:
        flat = restore_provider_stage_b_authority(
            flat,
            output_refs,
            reconstruction_diagnostics=reconstruction_diagnostics,
        )
    if stage == "C" and output_refs is not None:
        flat = restore_provider_stage_c_summary_refs(
            flat,
            output_refs,
            reconstruction_diagnostics=reconstruction_diagnostics,
        )

    if stage == "A":
        gaps: list[dict[str, Any]] = []
        for item in _object_rows(
            flat["evidenceGaps"],
            "evidenceGaps",
            reconstruction_diagnostics=reconstruction_diagnostics,
        ):
            value = dict(item)
            suggested = value.get("suggestedAnalysis")
            value["suggestedAnalysis"] = (
                None if suggested == _NONE_SENTINEL else suggested
            )
            gaps.append(value)
        return {
            "observations": deepcopy(flat["observations"]),
            "interpretations": deepcopy(flat["interpretations"]),
            "evidenceGaps": gaps,
        }

    if stage == "B":
        changes: list[dict[str, Any]] = []
        for item_index, item in enumerate(_object_rows(
            flat["changeCandidates"],
            "changeCandidates",
            reconstruction_diagnostics=reconstruction_diagnostics,
        )):
            changes.append(dict(_reconstruct_change_candidate(
                item,
                item_index,
                diagnostics=reconstruction_diagnostics,
            )))
        return {
            "hypotheses": deepcopy(flat["hypotheses"]),
            "changeCandidates": changes,
        }

    plans: list[dict[str, Any]] = []
    for item_index, item in enumerate(_object_rows(
        flat["validationPlans"],
        "validationPlans",
        reconstruction_diagnostics=reconstruction_diagnostics,
    )):
        plans.append(dict(_reconstruct_validation_plan(
            item,
            item_index,
            diagnostics=reconstruction_diagnostics,
        )))
    return {
        "validationPlans": plans,
        "executiveSummary": {
            "qualitativeOverview": flat["executiveQualitativeOverview"],
            "observationIds": flat["executiveObservationIds"],
            "hypothesisIds": flat["executiveHypothesisIds"],
            "evidenceGapIds": flat["executiveEvidenceGapIds"],
            "changeCandidateIds": flat["executiveChangeCandidateIds"],
        },
    }


def reconstruct_anthropic_flat_response(
    flat: Mapping[str, Any],
    package: AnalysisPromptPackage,
) -> Mapping[str, object]:
    """Rebuild the reference all-section flat DTO using staged primitives."""

    expected = set(_flat_fields_for_sections(ANTHROPIC_STRICT_TOOL_SECTIONS))
    if set(flat) != expected:
        raise AnthropicMalformedResponseError(
            "Anthropic flattened C-2 response has an invalid top-level shape."
        )
    canonical_sections: dict[str, Any] = {}
    for stage in ("A", "B", "C"):
        stage_fields = _flat_fields_for_sections(ANTHROPIC_STAGE_SPECS[stage])
        canonical_sections.update(reconstruct_anthropic_flat_sections(
            {field: flat[field] for field in stage_fields},
            stage,
        ))

    identity = package.source.identity
    direction = (
        "candidateMinusBaseline"
        if identity.mode == "contentVersionCompare"
        else "notApplicable"
    )
    return {
        "analysisVersion": ANALYSIS_VERSION,
        "sourceBriefIdentity": {
            "semanticOutputDigest": identity.semantic_output_digest,
            "scopeHash": identity.scope_hash,
            "mode": identity.mode,
            "baselineContentVersion": identity.baseline_content_version,
            "candidateContentVersion": identity.candidate_content_version,
        },
        "comparisonDirectionAcknowledgement": direction,
        **canonical_sections,
    }
def _object_rows(
    value: Any,
    field: str,
    *,
    reconstruction_diagnostics: bool = False,
) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        if reconstruction_diagnostics:
            _raise_reconstruction_failure(
                "INVALID_SECTION_WRAPPER",
                section=field,
                component="sectionItems",
                details={"expectedType": "list", "observedType": type(value).__name__},
            )
        raise AnthropicMalformedResponseError(f"Anthropic {field} must be an object array.")
    for item_index, item in enumerate(value):
        if not isinstance(item, Mapping):
            if reconstruction_diagnostics:
                _raise_reconstruction_failure(
                    "INVALID_SECTION_ITEM_SHAPE",
                    section=field,
                    component="sectionItem",
                    item_index=item_index,
                    details={"expectedType": "object", "observedType": type(item).__name__},
                )
            raise AnthropicMalformedResponseError(
                f"Anthropic {field} must be an object array."
            )
    return list(value)


@dataclass(frozen=True)
class AnthropicToolCollection:
    """Canonical response plus non-sensitive strict-tool collection metadata."""

    response: Mapping[str, object]
    observed_tool_count: int
    ignored_text_block_count: int


@dataclass(frozen=True)
class AnthropicStageToolCollection:
    """Canonical stage sections plus non-sensitive collection metadata."""

    stage: str
    sections: Mapping[str, Any]
    observed_tool_count: int
    ignored_text_block_count: int


def _block_attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def split_anthropic_flat_tool_inputs(
    flat: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Split a flat DTO into deterministic strict-tool inputs for fixtures."""

    expected_fields = {
        field for _name, fields in ANTHROPIC_STRICT_TOOL_SECTIONS for field in fields
    }
    if set(flat) != expected_fields:
        raise AnthropicInvalidToolInputError(
            "Anthropic flat DTO has an invalid field set.",
            details={"stage": "toolInputSplit"},
        )
    return tuple(
        (name, {field: deepcopy(flat[field]) for field in fields})
        for name, fields in ANTHROPIC_STRICT_TOOL_SECTIONS
    )


def split_anthropic_stage_tool_inputs(
    flat: Mapping[str, Any],
    stage: str,
    *,
    evidence_aliases: EvidenceAliasTable | None = None,
    metric_aliases: MetricAliasTable | None = None,
    warning_aliases: WarningAliasTable | None = None,
    validation_plan_refs: ValidationPlanRefTable | None = None,
    output_refs: StageOutputRefTables | None = None,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Split one staged flat DTO into deterministic strict-tool inputs."""

    try:
        spec = ANTHROPIC_PROVIDER_STAGE_SPECS[stage]
    except KeyError as error:
        raise ValueError(f"unknown Anthropic analysis stage: {stage}") from error
    if stage == "B" and output_refs is None:
        hypotheses = flat.get("hypotheses")
        gap_ids: list[str] = []
        if isinstance(hypotheses, list):
            for row in hypotheses:
                if isinstance(row, Mapping) and isinstance(row.get("evidenceGapIds"), list):
                    for value in row["evidenceGapIds"]:
                        if isinstance(value, str) and value not in gap_ids:
                            gap_ids.append(value)
        output_refs = StageOutputRefTables(
            observations=OutputRefTable("observation", ()),
            evidence_gaps=OutputRefTable("gap", tuple(gap_ids)),
        )
    if stage == "C" and output_refs is None:
        output_refs = StageOutputRefTables(
            observations=OutputRefTable(
                "observation", tuple(flat.get("executiveObservationIds", ()))
            ),
            evidence_gaps=OutputRefTable(
                "gap", tuple(flat.get("executiveEvidenceGapIds", ()))
            ),
            hypotheses=OutputRefTable(
                "hypothesis", tuple(flat.get("executiveHypothesisIds", ()))
            ),
            change_candidates=OutputRefTable(
                "change", tuple(flat.get("executiveChangeCandidateIds", ()))
            ),
        )
    if stage in {"A", "B"} and evidence_aliases is not None:
        flat = project_canonical_stage_evidence_aliases(
            flat, stage, evidence_aliases
        )
    if stage in {"A", "B"} and warning_aliases is not None:
        flat = project_canonical_stage_warning_aliases(
            flat, stage, warning_aliases
        )
    if stage in {"B", "C"}:
        flat = project_canonical_stage_metric_aliases(
            flat, stage, metric_aliases or build_metric_alias_table()
        )
    if stage == "C" and validation_plan_refs is not None:
        flat = project_canonical_stage_validation_plan_refs(
            flat, validation_plan_refs
        )
    if stage == "B" and output_refs is not None:
        flat = project_canonical_stage_b_authority(flat, output_refs)
    if stage == "C" and output_refs is not None:
        flat = project_canonical_stage_c_summary_refs(flat, output_refs)
    expected_fields = set(_flat_fields_for_sections(spec))
    if set(flat) != expected_fields:
        raise AnthropicInvalidToolInputError(
            "Anthropic staged flat DTO has an invalid field set.",
            details={"stage": stage},
        )
    return tuple(
        (name, {field: deepcopy(flat[field]) for field in fields})
        for name, fields in spec
    )


def _collect_flat_tool_inputs(
    content: Sequence[Any],
    sections: Sequence[tuple[str, Sequence[str]]],
    *,
    stage: str,
) -> tuple[Mapping[str, Any], int, int]:
    expected = {name: tuple(fields) for name, fields in sections}
    collected: dict[str, Mapping[str, Any]] = {}
    ignored_text_blocks = 0
    observed_tool_count = 0
    for block in content:
        block_type = _block_attr(block, "type")
        if block_type == "text":
            ignored_text_blocks += 1
            continue
        if block_type != "tool_use":
            raise AnthropicMalformedResponseError(
                "Anthropic returned an unsupported response content block.",
                details={"stage": stage, "contentBlockType": str(block_type)},
            )
        observed_tool_count += 1
        name = _block_attr(block, "name")
        if not isinstance(name, str) or name not in expected:
            raise AnthropicUnknownToolUseError(
                "Anthropic returned an unknown strict tool call.",
                details={"stage": stage, "toolName": str(name)},
            )
        if name in collected:
            raise AnthropicDuplicateToolUseError(
                "Anthropic returned a duplicate strict tool call.",
                details={"stage": stage, "toolName": name},
            )
        tool_input = _block_attr(block, "input")
        fields = expected[name]
        if not isinstance(tool_input, Mapping) or set(tool_input) != set(fields):
            raise AnthropicInvalidToolInputError(
                "Anthropic strict tool input has an invalid wrapper shape.",
                details={"stage": stage, "toolName": name},
            )
        for field in fields:
            value = tool_input[field]
            expected_type = str if field == "executiveQualitativeOverview" else list
            if not isinstance(value, expected_type):
                raise AnthropicInvalidToolInputError(
                    "Anthropic strict tool input has an invalid container type.",
                    details={"stage": stage, "toolName": name},
                )
        collected[name] = tool_input

    missing = [name for name, _fields in sections if name not in collected]
    if missing:
        raise AnthropicRequiredToolMissingError(
            "Anthropic omitted one or more required strict tool calls.",
            details={"stage": stage, "missingToolNames": missing},
        )
    flat: dict[str, Any] = {}
    for name, fields in sections:
        for field in fields:
            flat[field] = collected[name][field]
    return flat, observed_tool_count, ignored_text_blocks


def collect_anthropic_stage_tool_response(
    content: Sequence[Any],
    stage: str,
    *,
    evidence_aliases: EvidenceAliasTable | None = None,
    metric_aliases: MetricAliasTable | None = None,
    warning_aliases: WarningAliasTable | None = None,
    validation_plan_refs: ValidationPlanRefTable | None = None,
    output_refs: StageOutputRefTables | None = None,
) -> AnthropicStageToolCollection:
    """Collect exactly one call for each tool owned by one analysis stage."""

    try:
        spec = ANTHROPIC_PROVIDER_STAGE_SPECS[stage]
    except KeyError as error:
        raise ValueError(f"unknown Anthropic analysis stage: {stage}") from error
    flat, observed, ignored = _collect_flat_tool_inputs(
        content,
        spec,
        stage=stage,
    )
    if stage in {"B", "C"} and output_refs is None:
        output_refs = _infer_fixture_output_refs(flat, stage)
    try:
        effective_metric_aliases = (
            metric_aliases or build_metric_alias_table()
            if stage in {"B", "C"}
            else None
        )
        sections = reconstruct_anthropic_flat_sections(
            flat,
            stage,
            evidence_aliases=evidence_aliases,
            metric_aliases=effective_metric_aliases,
            warning_aliases=warning_aliases if stage in {"A", "B"} else None,
            validation_plan_refs=validation_plan_refs if stage == "C" else None,
            output_refs=output_refs if stage in {"B", "C"} else None,
            reconstruction_diagnostics=True,
        )
    except _ReconstructionInvariantError as error:
        raise AnthropicFlatReconstructionError(
            "Anthropic strict tool inputs could not be reconstructed.",
            details={
                "stage": stage,
                "processingBoundary": "reconstruction",
                **error.safe_details,
            },
        ) from None
    except (
        AnthropicMalformedResponseError,
        AnthropicEvidenceAliasError,
        AnthropicMetricAliasError,
        AnthropicWarningAliasError,
        AnthropicValidationPlanRefError,
        AnthropicOutputRefError,
    ) as error:
        invariant = (
            "UNKNOWN_EVIDENCE_ALIAS"
            if isinstance(error, AnthropicEvidenceAliasError)
            else "UNKNOWN_METRIC_ALIAS"
            if isinstance(error, AnthropicMetricAliasError)
            else "UNKNOWN_WARNING_ALIAS"
            if isinstance(error, AnthropicWarningAliasError)
            else "UNKNOWN_VALIDATION_PLAN_REF"
            if isinstance(error, AnthropicValidationPlanRefError)
            else "UNKNOWN_OUTPUT_REF"
            if isinstance(error, AnthropicOutputRefError)
            else "CANONICAL_SECTION_CONSTRUCTION_FAILED"
        )
        raise AnthropicFlatReconstructionError(
            "Anthropic strict tool inputs could not be reconstructed.",
            details={
                "stage": stage,
                "processingBoundary": "reconstruction",
                "section": "stageEnvelope",
                "reconstructionComponent": "stageSections",
                "reconstructionInvariant": invariant,
            },
        ) from None
    return AnthropicStageToolCollection(
        stage=stage,
        sections=sections,
        observed_tool_count=observed,
        ignored_text_block_count=ignored,
    )


def merge_anthropic_stage_sections(
    package: AnalysisPromptPackage,
    stage_a: ValidatedStageA,
    stage_b: ValidatedStageB,
    stage_c_sections: Mapping[str, Any],
) -> Mapping[str, object]:
    """Merge validated stage submissions and inject host-controlled identity."""

    if set(stage_c_sections) != {"validationPlans", "executiveSummary"}:
        raise AnthropicMalformedResponseError(
            "Anthropic Stage C canonical sections are incomplete."
        )
    identity = package.source.identity
    return {
        "analysisVersion": ANALYSIS_VERSION,
        "sourceBriefIdentity": {
            "semanticOutputDigest": identity.semantic_output_digest,
            "scopeHash": identity.scope_hash,
            "mode": identity.mode,
            "baselineContentVersion": identity.baseline_content_version,
            "candidateContentVersion": identity.candidate_content_version,
        },
        "comparisonDirectionAcknowledgement": (
            "candidateMinusBaseline"
            if identity.mode == "contentVersionCompare"
            else "notApplicable"
        ),
        **deepcopy(dict(stage_a.canonical_sections)),
        **deepcopy(dict(stage_b.canonical_sections)),
        **deepcopy(dict(stage_c_sections)),
    }


def collect_anthropic_tool_response(
    content: Sequence[Any],
    package: AnalysisPromptPackage,
) -> AnthropicToolCollection:
    """Collect the deprecated all-seven response for reference-only tests."""

    flat, observed, ignored = _collect_flat_tool_inputs(
        content,
        ANTHROPIC_STRICT_TOOL_SECTIONS,
        stage="all",
    )
    try:
        reconstructed = reconstruct_anthropic_flat_response(flat, package)
    except AnthropicMalformedResponseError as error:
        raise AnthropicFlatReconstructionError(
            "Anthropic strict tool inputs could not be reconstructed.",
            details={"stage": "flatReconstruction"},
        ) from error
    return AnthropicToolCollection(
        response=reconstructed,
        observed_tool_count=observed,
        ignored_text_block_count=ignored,
    )


__all__ = [
    "ANTHROPIC_EVIDENCE_ALIAS_VERSION",
    "ANTHROPIC_METRIC_ALIAS_VERSION",
    "ANTHROPIC_WARNING_ALIAS_VERSION",
    "ANTHROPIC_FLAT_RESPONSE_VERSION",
    "ANTHROPIC_INPUT_PROJECTION_VERSION",
    "ANTHROPIC_SERIALIZED_ENVELOPE_VERSION",
    "ANTHROPIC_STAGE_CONTEXT_VERSION",
    "ANTHROPIC_STAGE_B_CONTEXT_VERSION",
    "ANTHROPIC_STAGE_C_CONTEXT_VERSION",
    "ANTHROPIC_STRICT_TOOL_TRANSPORT_VERSION",
    "ANTHROPIC_THREE_STAGE_STRICT_TOOL_TRANSPORT_VERSION",
    "ANTHROPIC_VALIDATION_PLAN_REF_VERSION",
    "ANTHROPIC_OUTPUT_REF_VERSION",
    "ANTHROPIC_STAGE_B_IDENTITY_VERSION",
    "AnthropicStageToolCollection",
    "AnthropicToolCollection",
    "EvidenceAliasTable",
    "MetricAliasTable",
    "WarningAliasTable",
    "ValidationPlanRefTable",
    "OutputRefTable",
    "StageOutputRefTables",
    "assert_provider_metric_output_schema_is_aliased",
    "assert_provider_warning_output_schema_is_aliased",
    "assert_provider_request_has_no_canonical_evidence_ids",
    "build_anthropic_prompt_parts",
    "build_anthropic_stage_context",
    "build_anthropic_stage_prompt_parts",
    "build_compact_llm_payload",
    "build_evidence_alias_table",
    "build_metric_alias_table",
    "build_warning_alias_table",
    "build_validation_plan_ref_table",
    "build_stage_output_ref_tables",
    "compact_payload_digest",
    "collect_anthropic_tool_response",
    "collect_anthropic_stage_tool_response",
    "encode_metric_reference",
    "expand_compact_evidence_items",
    "flatten_canonical_response",
    "merge_anthropic_stage_sections",
    "parse_anthropic_serialized_response_text",
    "project_canonical_stage_evidence_aliases",
    "project_canonical_stage_metric_aliases",
    "project_canonical_stage_validation_plan_refs",
    "project_canonical_stage_warning_aliases",
    "project_canonical_stage_b_authority",
    "project_canonical_stage_c_summary_refs",
    "project_canonical_prior_warning_aliases",
    "project_canonical_prior_metric_aliases",
    "project_compact_evidence_aliases",
    "reconstruct_anthropic_flat_response",
    "reconstruct_anthropic_flat_sections",
    "reconstruct_anthropic_serialized_response",
    "restore_compact_evidence_aliases",
    "restore_provider_stage_evidence_aliases",
    "restore_provider_stage_metric_aliases",
    "restore_provider_stage_validation_plan_refs",
    "restore_provider_stage_warning_aliases",
    "restore_provider_stage_b_authority",
    "restore_provider_stage_c_summary_refs",
    "restore_provider_prior_warning_aliases",
    "restore_provider_prior_metric_aliases",
    "serialize_canonical_response_sections",
    "split_anthropic_flat_tool_inputs",
    "split_anthropic_stage_tool_inputs",
]
