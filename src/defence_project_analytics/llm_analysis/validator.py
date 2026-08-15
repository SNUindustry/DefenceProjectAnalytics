"""Strict provider-response validation and deterministic C-2 normalization."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from defence_project_analytics.llm_analysis.errors import AnalysisResponseValidationError
from defence_project_analytics.llm_analysis.loader import canonical_digest
from defence_project_analytics.llm_analysis.models import (
    ACTION_TYPES,
    ANALYSIS_VERSION,
    COMPARISON_PLANS,
    COUNTER_SEARCH_VALUES,
    DIRECTIONS,
    FINDING_TYPES,
    IMPORTANCE_VALUES,
    KNOWN_ANALYSES,
    MINIMUM_REQUIREMENTS,
    MAX_EXECUTIVE_SUMMARY_IDS,
    MAX_OBSERVATIONS,
    MAX_RESPONSE_SECTION_ITEMS,
    MAX_ROLLBACK_INDICATORS,
    OBSERVABLE_DIRECTIONS,
    RESPONSE_CONTRACT_VERSION,
    ROLLBACK_CONDITIONS,
    TARGET_TYPES,
    AnalysisPromptPackage,
    ChangeCandidate,
    ChangeTarget,
    CitationCoverage,
    EvidenceGap,
    ExecutiveSummary,
    ExpectedObservableDirection,
    Hypothesis,
    Interpretation,
    MetricReference,
    Observation,
    ProposedChange,
    RollbackIndicator,
    ValidatedAnalysis,
    ValidationPlan,
)
from defence_project_analytics.llm_analysis.policy import (
    actionability,
    contains_numeric_claim,
    evidence_strength,
    forbidden_private_text,
    overall_assessment,
    prohibited_language,
)
from defence_project_analytics.metric_registry import known_metric_keys
from defence_project_analytics.reporting.renderers import to_external


_ID_PATTERNS = {
    "observation": re.compile(r"OBS-\d{3}$"),
    "interpretation": re.compile(r"INT-\d{3}$"),
    "hypothesis": re.compile(r"HYP-\d{3}$"),
    "gap": re.compile(r"GAP-\d{3}$"),
    "change": re.compile(r"CHG-\d{3}$"),
    "validation": re.compile(r"VAL-\d{3}$"),
}
_ACTIONABLE = frozenset({"Experiment", "BalanceChange", "UXChange", "TelemetryChange"})


class _Validator:
    def __init__(self, package: AnalysisPromptPackage):
        self.package = package
        self.issues: list[dict[str, str]] = []
        self.evidence = package.source.evidence_by_id
        self.brief_status = str(package.source.brief["overallStatus"])
        self.warning_codes = {
            str(item.get("code"))
            for item in package.source.brief.get("criticalWarnings", [])
            if isinstance(item, dict) and item.get("code")
        }
        for item in self.evidence.values():
            self.warning_codes.update(map(str, item.get("warningCodes", [])))
        self.metric_keys = set(known_metric_keys())
        self.metric_keys.update(
            (str(item.get("domain")), str(item.get("metricFamily")), str(item.get("metric")))
            for item in self.evidence.values()
        )

    def issue(self, code: str, path: str, message: str) -> None:
        self.issues.append({"code": code, "path": path, "message": message})

    def obj(self, value: Any, path: str, keys: set[str]) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            self.issue("INVALID_TYPE", path, "must be an object")
            return {}
        extra = set(map(str, value)) - keys
        missing = keys - set(map(str, value))
        for key in sorted(extra):
            self.issue("UNKNOWN_FIELD", f"{path}.{key}", "field is not allowed")
        for key in sorted(missing):
            self.issue("MISSING_FIELD", f"{path}.{key}", "field is required")
        return value

    def array(self, value: Any, path: str, maximum: int) -> list[Any]:
        if not isinstance(value, list):
            self.issue("INVALID_TYPE", path, "must be an array")
            return []
        if len(value) > maximum:
            self.issue("LIMIT_EXCEEDED", path, f"maximum is {maximum}")
        return value[:maximum]

    def text(
        self,
        value: Any,
        path: str,
        *,
        nullable: bool = False,
        allow_numeric: bool = False,
        maximum: int = 2_000,
    ) -> str | None:
        if value is None and nullable:
            return None
        if not isinstance(value, str) or not value.strip():
            self.issue("INVALID_TEXT", path, "must be a nonblank string")
            return None if nullable else ""
        result = value.strip()
        if len(result) > maximum:
            self.issue("LIMIT_EXCEEDED", path, f"maximum length is {maximum}")
        if not allow_numeric and contains_numeric_claim(result):
            self.issue("FREEFORM_NUMERIC_CLAIM", path, "numeric facts must be rendered from evidence")
        prohibited = prohibited_language(result)
        if prohibited:
            self.issue("UNSUPPORTED_CAUSAL_LANGUAGE", path, f"prohibited wording: {prohibited}")
        private = forbidden_private_text(result)
        if private:
            self.issue("FORBIDDEN_PRIVATE_TEXT", path, f"forbidden text: {private}")
        return result

    def string_list(self, value: Any, path: str, maximum: int = 10) -> tuple[str, ...]:
        items = self.array(value, path, maximum)
        result: list[str] = []
        for index, item in enumerate(items):
            parsed = self.text(item, f"{path}[{index}]")
            if parsed is not None:
                result.append(parsed)
        if len(result) != len(set(result)):
            self.issue("DUPLICATE_VALUE", path, "values must be unique")
        return tuple(result)

    def id(self, value: Any, path: str, kind: str) -> str:
        if not isinstance(value, str) or not _ID_PATTERNS[kind].fullmatch(value):
            self.issue("INVALID_ID", path, f"must match {_ID_PATTERNS[kind].pattern}")
            return ""
        return value

    def enum(self, value: Any, path: str, allowed: Iterable[str]) -> str:
        allowed_set = set(allowed)
        if not isinstance(value, str) or value not in allowed_set:
            self.issue("INVALID_ENUM", path, f"must be one of {sorted(allowed_set)}")
            return ""
        return value

    def bool(self, value: Any, path: str) -> bool:
        if not isinstance(value, bool):
            self.issue("INVALID_TYPE", path, "must be boolean")
            return False
        return value

    def evidence_ids(
        self, value: Any, path: str, *, minimum: int = 0, maximum: int = 20
    ) -> tuple[str, ...]:
        items = self.array(value, path, maximum)
        result: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, str):
                self.issue("INVALID_TYPE", f"{path}[{index}]", "must be an Evidence ID")
                continue
            if item not in self.evidence:
                self.issue("UNKNOWN_EVIDENCE_ID", f"{path}[{index}]", item)
                continue
            result.append(item)
        if len(result) < minimum:
            self.issue("MISSING_EVIDENCE", path, f"requires at least {minimum} valid Evidence IDs")
        if len(result) != len(set(result)):
            self.issue("DUPLICATE_EVIDENCE_REFERENCE", path, "Evidence IDs must be unique")
        return tuple(result)

    def output_ids(
        self, value: Any, path: str, *, kind: str, maximum: int = 10
    ) -> tuple[str, ...]:
        items = self.array(value, path, maximum)
        result = tuple(
            self.id(item, f"{path}[{index}]", kind)
            for index, item in enumerate(items)
        )
        if len(result) != len(set(result)):
            self.issue("DUPLICATE_VALUE", path, "output IDs must be unique")
        return result

    def warning_list(self, value: Any, path: str) -> tuple[str, ...]:
        values = self.string_list(value, path)
        for code in values:
            if code not in self.warning_codes:
                self.issue("UNKNOWN_WARNING_CODE", path, code)
        return values

    def check_unique_ids(self, groups: Iterable[tuple[str, Iterable[Any]]]) -> None:
        seen: dict[str, str] = {}
        for group, items in groups:
            for item in items:
                item_id = getattr(item, "id", "")
                if not item_id:
                    continue
                if item_id in seen:
                    self.issue("DUPLICATE_OUTPUT_ID", f"$.{group}", f"{item_id} also appears in {seen[item_id]}")
                seen[item_id] = group


def _response_payload(response: Mapping[str, Any] | Path | str) -> Mapping[str, Any]:
    try:
        if isinstance(response, Path):
            raw = response.read_text(encoding="utf-8")
            if len(raw) > 100_000:
                raise AnalysisResponseValidationError(({
                    "code": "RESPONSE_TOO_LARGE", "path": "$", "message": "response exceeds 100000 characters"
                },))
            value = json.loads(raw, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
        elif isinstance(response, str):
            if len(response) > 100_000:
                raise AnalysisResponseValidationError(({
                    "code": "RESPONSE_TOO_LARGE", "path": "$", "message": "response exceeds 100000 characters"
                },))
            value = json.loads(response, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
        else:
            value = response
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise AnalysisResponseValidationError(({
            "code": "INVALID_JSON", "path": "$", "message": str(exc)
        },)) from exc
    if not isinstance(value, dict):
        raise AnalysisResponseValidationError(({
            "code": "INVALID_TYPE", "path": "$", "message": "response must be an object"
        },))
    return value


def _coverage(count: int, denominator: int) -> CitationCoverage:
    return CitationCoverage(count, denominator, None if denominator == 0 else count / denominator)


def validate_response(
    package: AnalysisPromptPackage,
    response: Mapping[str, Any] | Path | str,
) -> ValidatedAnalysis:
    raw = _response_payload(response)
    v = _Validator(package)
    top = v.obj(raw, "$", {
        "analysisVersion", "sourceBriefIdentity", "comparisonDirectionAcknowledgement",
        "executiveSummary", "observations", "interpretations", "hypotheses",
        "evidenceGaps", "changeCandidates", "validationPlans",
    })
    if top.get("analysisVersion") != ANALYSIS_VERSION:
        v.issue("INCOMPATIBLE_ANALYSIS_VERSION", "$.analysisVersion", ANALYSIS_VERSION)
    identity = v.obj(top.get("sourceBriefIdentity"), "$.sourceBriefIdentity", {
        "semanticOutputDigest", "scopeHash", "mode",
        "baselineContentVersion", "candidateContentVersion",
    })
    expected = package.source.identity
    identity_expected = {
        "semanticOutputDigest": expected.semantic_output_digest,
        "scopeHash": expected.scope_hash,
        "mode": expected.mode,
        "baselineContentVersion": expected.baseline_content_version,
        "candidateContentVersion": expected.candidate_content_version,
    }
    if identity != identity_expected:
        v.issue("SOURCE_BRIEF_IDENTITY_MISMATCH", "$.sourceBriefIdentity", "must exactly match the analysis request")
    expected_direction = "candidateMinusBaseline" if expected.mode == "contentVersionCompare" else "notApplicable"
    if top.get("comparisonDirectionAcknowledgement") != expected_direction:
        v.issue("COMPARISON_DIRECTION_MISMATCH", "$.comparisonDirectionAcknowledgement", expected_direction)

    observations: list[Observation] = []
    for index, raw_item in enumerate(v.array(top.get("observations"), "$.observations", MAX_OBSERVATIONS)):
        path = f"$.observations[{index}]"
        item = v.obj(raw_item, path, {"id", "findingType", "qualitativeStatement", "evidenceIds", "importance"})
        ids = v.evidence_ids(item.get("evidenceIds"), f"{path}.evidenceIds", minimum=1)
        finding = v.enum(item.get("findingType"), f"{path}.findingType", FINDING_TYPES)
        for evidence_id in ids:
            evidence = v.evidence[evidence_id]
            value = evidence.get("value", {})
            if finding == "BaselineCandidateDifference" and not (
                isinstance(value, dict) and "baselineValue" in value and "candidateValue" in value
            ):
                v.issue("FINDING_EVIDENCE_MISMATCH", f"{path}.findingType", evidence_id)
            if finding == "ObservedOnlyInBaseline" and not (
                value.get("baselineObserved") is True and value.get("candidateObserved") is False
            ):
                v.issue("FINDING_EVIDENCE_MISMATCH", f"{path}.findingType", evidence_id)
            if finding == "ObservedOnlyInCandidate" and not (
                value.get("candidateObserved") is True and value.get("baselineObserved") is False
            ):
                v.issue("FINDING_EVIDENCE_MISMATCH", f"{path}.findingType", evidence_id)
            if finding == "LimitedOrUnavailable" and evidence.get("status") not in {"Limited", "Unavailable"}:
                v.issue("FINDING_EVIDENCE_MISMATCH", f"{path}.findingType", evidence_id)
        observations.append(Observation(
            id=v.id(item.get("id"), f"{path}.id", "observation"),
            finding_type=finding,
            qualitative_statement=v.text(item.get("qualitativeStatement"), f"{path}.qualitativeStatement") or "",
            evidence_ids=ids,
            importance=v.enum(item.get("importance"), f"{path}.importance", IMPORTANCE_VALUES),
        ))

    interpretations: list[Interpretation] = []
    for index, raw_item in enumerate(v.array(top.get("interpretations"), "$.interpretations", MAX_RESPONSE_SECTION_ITEMS)):
        path = f"$.interpretations[{index}]"
        item = v.obj(raw_item, path, {"id", "statement", "evidenceIds", "limitationEvidenceIds", "limitationWarningCodes"})
        ids = v.evidence_ids(item.get("evidenceIds"), f"{path}.evidenceIds", minimum=1)
        limits = v.evidence_ids(item.get("limitationEvidenceIds"), f"{path}.limitationEvidenceIds")
        interpretations.append(Interpretation(
            id=v.id(item.get("id"), f"{path}.id", "interpretation"),
            statement=v.text(item.get("statement"), f"{path}.statement") or "",
            evidence_ids=ids,
            limitation_evidence_ids=limits,
            limitation_warning_codes=v.warning_list(item.get("limitationWarningCodes"), f"{path}.limitationWarningCodes"),
            evidence_strength=evidence_strength(v.brief_status, (v.evidence[x] for x in ids)),
        ))

    gaps: list[EvidenceGap] = []
    for index, raw_item in enumerate(v.array(top.get("evidenceGaps"), "$.evidenceGaps", MAX_RESPONSE_SECTION_ITEMS)):
        path = f"$.evidenceGaps[{index}]"
        item = v.obj(raw_item, path, {"id", "question", "whyItMatters", "relatedEvidenceIds", "suggestedAnalysis", "requiresNewTelemetry"})
        suggested = item.get("suggestedAnalysis")
        if suggested is not None:
            suggested = v.enum(suggested, f"{path}.suggestedAnalysis", KNOWN_ANALYSES)
        gaps.append(EvidenceGap(
            id=v.id(item.get("id"), f"{path}.id", "gap"),
            question=v.text(item.get("question"), f"{path}.question") or "",
            why_it_matters=v.text(item.get("whyItMatters"), f"{path}.whyItMatters") or "",
            related_evidence_ids=v.evidence_ids(item.get("relatedEvidenceIds"), f"{path}.relatedEvidenceIds"),
            suggested_analysis=suggested,
            requires_new_telemetry=v.bool(item.get("requiresNewTelemetry"), f"{path}.requiresNewTelemetry"),
        ))
    gap_ids = {item.id for item in gaps if item.id}

    hypotheses: list[Hypothesis] = []
    for index, raw_item in enumerate(v.array(top.get("hypotheses"), "$.hypotheses", MAX_RESPONSE_SECTION_ITEMS)):
        path = f"$.hypotheses[{index}]"
        item = v.obj(raw_item, path, {
            "id", "statement", "supportingEvidenceIds", "counterEvidenceIds",
            "counterEvidenceSearchStatus", "limitationWarningCodes", "assumptions",
            "alternativeExplanations", "evidenceGapIds", "falsificationChecks",
        })
        support = v.evidence_ids(item.get("supportingEvidenceIds"), f"{path}.supportingEvidenceIds", minimum=1)
        counter = v.evidence_ids(item.get("counterEvidenceIds"), f"{path}.counterEvidenceIds")
        search = v.enum(item.get("counterEvidenceSearchStatus"), f"{path}.counterEvidenceSearchStatus", COUNTER_SEARCH_VALUES)
        if search == "FoundInSuppliedBrief" and not counter:
            v.issue("COUNTER_EVIDENCE_INVARIANT", f"{path}.counterEvidenceIds", "FoundInSuppliedBrief requires counter evidence")
        if search == "NotIdentifiedInSuppliedBrief" and counter:
            v.issue("COUNTER_EVIDENCE_INVARIANT", f"{path}.counterEvidenceIds", "NotIdentifiedInSuppliedBrief requires an empty list")
        referenced_gaps = v.output_ids(
            item.get("evidenceGapIds"), f"{path}.evidenceGapIds", kind="gap"
        )
        for gap_id in referenced_gaps:
            if gap_id not in gap_ids:
                v.issue("UNKNOWN_OUTPUT_REFERENCE", f"{path}.evidenceGapIds", gap_id)
        hypotheses.append(Hypothesis(
            id=v.id(item.get("id"), f"{path}.id", "hypothesis"),
            statement=v.text(item.get("statement"), f"{path}.statement") or "",
            supporting_evidence_ids=support,
            counter_evidence_ids=counter,
            counter_evidence_search_status=search,
            limitation_warning_codes=v.warning_list(item.get("limitationWarningCodes"), f"{path}.limitationWarningCodes"),
            assumptions=v.string_list(item.get("assumptions"), f"{path}.assumptions"),
            alternative_explanations=v.string_list(item.get("alternativeExplanations"), f"{path}.alternativeExplanations", 5),
            evidence_gap_ids=referenced_gaps,
            falsification_checks=v.string_list(item.get("falsificationChecks"), f"{path}.falsificationChecks"),
            evidence_strength=evidence_strength(
                v.brief_status,
                (v.evidence[x] for x in support),
                (v.evidence[x] for x in counter),
            ),
        ))

    changes: list[ChangeCandidate] = []
    for index, raw_item in enumerate(v.array(top.get("changeCandidates"), "$.changeCandidates", MAX_RESPONSE_SECTION_ITEMS)):
        path = f"$.changeCandidates[{index}]"
        item = v.obj(raw_item, path, {
            "id", "domain", "target", "actionType", "proposedChange", "rationale",
            "supportingEvidenceIds", "counterEvidenceIds", "counterEvidenceSearchStatus",
            "limitationWarningCodes", "risks", "expectedObservableDirections", "validationPlanId",
        })
        domain = v.enum(item.get("domain"), f"{path}.domain", set(KNOWN_ANALYSES) | {"crossDomain"})
        target_raw = v.obj(item.get("target"), f"{path}.target", {
            "targetType", "domain", "entityType", "entityKey", "metricFamily", "metric",
            "description", "requiresGameDesignContext",
        })
        target_type = v.enum(target_raw.get("targetType"), f"{path}.target.targetType", TARGET_TYPES)
        target_domain = v.enum(target_raw.get("domain"), f"{path}.target.domain", set(KNOWN_ANALYSES) | {"crossDomain"})
        entity_type = v.text(target_raw.get("entityType"), f"{path}.target.entityType", nullable=True)
        entity_key = v.text(target_raw.get("entityKey"), f"{path}.target.entityKey", nullable=True)
        metric_family = v.text(target_raw.get("metricFamily"), f"{path}.target.metricFamily", nullable=True)
        metric = v.text(target_raw.get("metric"), f"{path}.target.metric", nullable=True)
        description = v.text(target_raw.get("description"), f"{path}.target.description", nullable=True)
        requires_context = v.bool(target_raw.get("requiresGameDesignContext"), f"{path}.target.requiresGameDesignContext")
        if target_type == "EvidenceEntity" and not any(
            evidence.get("domain") == target_domain
            and evidence.get("entityType") == entity_type
            and evidence.get("entityKey") == entity_key
            for evidence in v.evidence.values()
        ):
            v.issue("UNKNOWN_EVIDENCE_ENTITY", f"{path}.target", "entity is not present in selected evidence")
        if target_type == "EvidenceMetric" and (target_domain, metric_family, metric) not in v.metric_keys:
            v.issue("UNKNOWN_METRIC", f"{path}.target", "metric is not in the shared registry")
        if target_type == "Conceptual" and not requires_context:
            v.issue("CONCEPTUAL_TARGET_REQUIRES_CONTEXT", f"{path}.target.requiresGameDesignContext", "must be true")
        proposed_raw = v.obj(item.get("proposedChange"), f"{path}.proposedChange", {
            "description", "parameter", "direction", "amountPercent", "heuristic", "magnitudeBasis",
        })
        amount = proposed_raw.get("amountPercent")
        if amount is not None and (
            isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(float(amount)) or float(amount) <= 0
        ):
            v.issue("INVALID_NUMERIC_TUNING", f"{path}.proposedChange.amountPercent", "must be a finite positive number or null")
            amount = None
        heuristic = v.bool(proposed_raw.get("heuristic"), f"{path}.proposedChange.heuristic")
        action_type = v.enum(item.get("actionType"), f"{path}.actionType", ACTION_TYPES)
        direction = v.enum(proposed_raw.get("direction"), f"{path}.proposedChange.direction", DIRECTIONS)
        magnitude_basis = v.text(proposed_raw.get("magnitudeBasis"), f"{path}.proposedChange.magnitudeBasis", nullable=True)
        if amount is not None and (
            not heuristic or magnitude_basis != "HeuristicExperimentCandidate"
            or action_type not in {"Experiment", "BalanceChange", "UXChange"}
            or direction not in {"Increase", "Decrease"}
        ):
            v.issue("HEURISTIC_TUNING_POLICY", f"{path}.proposedChange", "numeric tuning requires the heuristic experiment contract")
        support = v.evidence_ids(item.get("supportingEvidenceIds"), f"{path}.supportingEvidenceIds", minimum=1)
        counter = v.evidence_ids(item.get("counterEvidenceIds"), f"{path}.counterEvidenceIds")
        search = v.enum(item.get("counterEvidenceSearchStatus"), f"{path}.counterEvidenceSearchStatus", COUNTER_SEARCH_VALUES)
        if (search == "FoundInSuppliedBrief") != bool(counter):
            v.issue("COUNTER_EVIDENCE_INVARIANT", f"{path}.counterEvidenceIds", "search status and list disagree")
        strength = evidence_strength(
            v.brief_status,
            (v.evidence[x] for x in support),
            (v.evidence[x] for x in counter),
        )
        if v.brief_status == "Insufficient" and action_type in _ACTIONABLE:
            v.issue("INSUFFICIENT_ACTION_BLOCKED", f"{path}.actionType", action_type)
        if expected.mode == "singleVersion" and not package.request.analysis_objective and action_type in {"BalanceChange", "UXChange"}:
            v.issue("DESIGN_OBJECTIVE_REQUIRED", f"{path}.actionType", "single-version balance/UX change requires design context")
        expected_directions: list[ExpectedObservableDirection] = []
        for dindex, raw_direction in enumerate(v.array(item.get("expectedObservableDirections"), f"{path}.expectedObservableDirections", 10)):
            dpath = f"{path}.expectedObservableDirections[{dindex}]"
            direction_item = v.obj(raw_direction, dpath, {"domain", "metricFamily", "metric", "direction"})
            key = (
                str(direction_item.get("domain")),
                str(direction_item.get("metricFamily")),
                str(direction_item.get("metric")),
            )
            if key not in v.metric_keys:
                v.issue("UNKNOWN_METRIC", dpath, ".".join(key))
            expected_directions.append(ExpectedObservableDirection(
                domain=key[0], metric_family=key[1], metric=key[2],
                direction=v.enum(direction_item.get("direction"), f"{dpath}.direction", OBSERVABLE_DIRECTIONS),
            ))
        validation_plan_id = item.get("validationPlanId")
        if validation_plan_id is not None:
            validation_plan_id = v.id(validation_plan_id, f"{path}.validationPlanId", "validation")
        if action_type in _ACTIONABLE and not validation_plan_id:
            v.issue("VALIDATION_PLAN_REQUIRED", f"{path}.validationPlanId", action_type)
        changes.append(ChangeCandidate(
            id=v.id(item.get("id"), f"{path}.id", "change"),
            domain=domain,
            target=ChangeTarget(
                target_type, target_domain, entity_type, entity_key, metric_family, metric,
                description, requires_context,
            ),
            action_type=action_type,
            proposed_change=ProposedChange(
                v.text(proposed_raw.get("description"), f"{path}.proposedChange.description") or "",
                v.text(proposed_raw.get("parameter"), f"{path}.proposedChange.parameter", nullable=True),
                direction, None if amount is None else float(amount), heuristic, magnitude_basis,
            ),
            rationale=v.text(item.get("rationale"), f"{path}.rationale") or "",
            supporting_evidence_ids=support,
            counter_evidence_ids=counter,
            counter_evidence_search_status=search,
            limitation_warning_codes=v.warning_list(item.get("limitationWarningCodes"), f"{path}.limitationWarningCodes"),
            risks=v.string_list(item.get("risks"), f"{path}.risks"),
            expected_observable_directions=tuple(expected_directions),
            validation_plan_id=validation_plan_id,
            evidence_strength=strength,
            actionability=actionability(
                action_type=action_type,
                strength=strength,
                brief_status=v.brief_status,
                has_design_objective=bool(package.request.analysis_objective),
                conceptual_target=target_type == "Conceptual",
                heuristic_numeric=amount is not None,
            ),
        ))

    validations: list[ValidationPlan] = []
    for index, raw_item in enumerate(v.array(top.get("validationPlans"), "$.validationPlans", MAX_RESPONSE_SECTION_ITEMS)):
        path = f"$.validationPlans[{index}]"
        item = v.obj(raw_item, path, {
            "id", "changeCandidateId", "analysesToRerun", "metricsToWatch",
            "guardrailMetrics", "minimumEvidenceRequirements", "comparisonPlan", "rollbackIndicators",
        })
        def metric_refs(value: Any, mpath: str, maximum: int = 10) -> tuple[MetricReference, ...]:
            refs: list[MetricReference] = []
            for mindex, raw_metric in enumerate(v.array(value, mpath, maximum)):
                ipath = f"{mpath}[{mindex}]"
                metric_item = v.obj(raw_metric, ipath, {"domain", "metricFamily", "metric"})
                key = (str(metric_item.get("domain")), str(metric_item.get("metricFamily")), str(metric_item.get("metric")))
                if key not in v.metric_keys:
                    v.issue("UNKNOWN_METRIC", ipath, ".".join(key))
                refs.append(MetricReference(*key))
            return tuple(refs)
        analyses = v.string_list(item.get("analysesToRerun"), f"{path}.analysesToRerun")
        for analysis in analyses:
            if analysis not in KNOWN_ANALYSES:
                v.issue("UNKNOWN_ANALYSIS", f"{path}.analysesToRerun", analysis)
        requirements = v.string_list(item.get("minimumEvidenceRequirements"), f"{path}.minimumEvidenceRequirements")
        for requirement in requirements:
            if requirement not in MINIMUM_REQUIREMENTS:
                v.issue("INVENTED_SAMPLE_REQUIREMENT", f"{path}.minimumEvidenceRequirements", requirement)
        rollback: list[RollbackIndicator] = []
        for rindex, raw_rollback in enumerate(v.array(item.get("rollbackIndicators"), f"{path}.rollbackIndicators", MAX_ROLLBACK_INDICATORS)):
            rpath = f"{path}.rollbackIndicators[{rindex}]"
            rollback_item = v.obj(raw_rollback, rpath, {"metric", "condition"})
            refs = metric_refs([rollback_item.get("metric")], f"{rpath}.metric", 1)
            if refs:
                rollback.append(RollbackIndicator(
                    refs[0], v.enum(rollback_item.get("condition"), f"{rpath}.condition", ROLLBACK_CONDITIONS)
                ))
        validations.append(ValidationPlan(
            id=v.id(item.get("id"), f"{path}.id", "validation"),
            change_candidate_id=v.id(item.get("changeCandidateId"), f"{path}.changeCandidateId", "change"),
            analyses_to_rerun=analyses,
            metrics_to_watch=metric_refs(item.get("metricsToWatch"), f"{path}.metricsToWatch"),
            guardrail_metrics=metric_refs(item.get("guardrailMetrics"), f"{path}.guardrailMetrics"),
            minimum_evidence_requirements=requirements,
            comparison_plan=v.enum(item.get("comparisonPlan"), f"{path}.comparisonPlan", COMPARISON_PLANS),
            rollback_indicators=tuple(rollback),
        ))

    change_ids = {item.id for item in changes if item.id}
    validation_by_id = {item.id: item for item in validations if item.id}
    for item in validations:
        if item.change_candidate_id not in change_ids:
            v.issue("UNKNOWN_OUTPUT_REFERENCE", "$.validationPlans", item.change_candidate_id)
    for item in changes:
        if item.validation_plan_id:
            plan = validation_by_id.get(item.validation_plan_id)
            if plan is None or plan.change_candidate_id != item.id:
                v.issue("VALIDATION_LINK_MISMATCH", "$.changeCandidates", item.id)
            elif not plan.analyses_to_rerun or not plan.metrics_to_watch:
                v.issue("INCOMPLETE_VALIDATION_PLAN", "$.validationPlans", plan.id)

    executive_raw = v.obj(top.get("executiveSummary"), "$.executiveSummary", {
        "qualitativeOverview", "observationIds", "hypothesisIds", "evidenceGapIds", "changeCandidateIds"
    })
    def output_refs(
        value: Any, path: str, allowed: set[str], kind: str
    ) -> tuple[str, ...]:
        refs = v.output_ids(value, path, kind=kind, maximum=MAX_EXECUTIVE_SUMMARY_IDS)
        for ref in refs:
            if ref not in allowed:
                v.issue("UNKNOWN_OUTPUT_REFERENCE", path, ref)
        return refs
    executive = ExecutiveSummary(
        qualitative_overview=v.text(executive_raw.get("qualitativeOverview"), "$.executiveSummary.qualitativeOverview") or "",
        observation_ids=output_refs(executive_raw.get("observationIds"), "$.executiveSummary.observationIds", {x.id for x in observations}, "observation"),
        hypothesis_ids=output_refs(executive_raw.get("hypothesisIds"), "$.executiveSummary.hypothesisIds", {x.id for x in hypotheses}, "hypothesis"),
        evidence_gap_ids=output_refs(executive_raw.get("evidenceGapIds"), "$.executiveSummary.evidenceGapIds", gap_ids, "gap"),
        change_candidate_ids=output_refs(executive_raw.get("changeCandidateIds"), "$.executiveSummary.changeCandidateIds", change_ids, "change"),
    )

    v.check_unique_ids((
        ("observations", observations), ("interpretations", interpretations),
        ("hypotheses", hypotheses), ("evidenceGaps", gaps),
        ("changeCandidates", changes), ("validationPlans", validations),
    ))
    if v.issues:
        raise AnalysisResponseValidationError(tuple(v.issues))

    observations.sort(key=lambda item: item.id)
    interpretations.sort(key=lambda item: item.id)
    hypotheses.sort(key=lambda item: item.id)
    gaps.sort(key=lambda item: item.id)
    changes.sort(key=lambda item: item.id)
    validations.sort(key=lambda item: item.id)
    actionable = [item for item in changes if item.action_type in _ACTIONABLE]
    valid_actionable = [item for item in actionable if item.validation_plan_id in validation_by_id]
    analysis = ValidatedAnalysis(
        analysis_version=ANALYSIS_VERSION,
        source_brief_identity=package.source.identity,
        comparison_direction_acknowledgement=expected_direction,
        executive_summary=executive,
        observations=tuple(observations),
        interpretations=tuple(interpretations),
        hypotheses=tuple(hypotheses),
        evidence_gaps=tuple(gaps),
        change_candidates=tuple(changes),
        validation_plans=tuple(validations),
        overall_assessment=overall_assessment(
            v.brief_status,
            (item.action_type for item in changes),
            (item.actionability for item in changes),
        ),
        observation_citation_coverage=_coverage(sum(bool(x.evidence_ids) for x in observations), len(observations)),
        hypothesis_support_coverage=_coverage(sum(bool(x.supporting_evidence_ids) for x in hypotheses), len(hypotheses)),
        change_candidate_evidence_coverage=_coverage(sum(bool(x.supporting_evidence_ids) for x in changes), len(changes)),
        actionable_candidate_validation_coverage=_coverage(len(valid_actionable), len(actionable)),
        invalid_evidence_reference_count=0,
        normalized_analysis_digest="",
    )
    digest_payload = to_external(analysis)
    digest_payload.pop("normalizedAnalysisDigest", None)
    return replace(analysis, normalized_analysis_digest=canonical_digest(digest_payload))


__all__ = ["validate_response", "RESPONSE_CONTRACT_VERSION"]
