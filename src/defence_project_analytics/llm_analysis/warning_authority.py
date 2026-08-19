"""Canonical warning authority shared by compact transport and C-2 validation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping

from defence_project_analytics.llm_analysis.models import AnalysisPromptPackage
from defence_project_analytics.brief.registry import HISTORICAL_ONLY_WARNING_CODES
from defence_project_analytics.metric_registry import is_decision_evidence_item
from defence_project_analytics.reporting.renderers import to_external


MODEL_VISIBLE_BRIEF_KEYS = (
    "analysisBriefVersion",
    "mode",
    "scope",
    "overallStatus",
    "sourceStatusByDomain",
    "criticalWarnings",
    "snapshotCompatibility",
    "interpretationConstraints",
    "selectionSummary",
)


def model_visible_brief(package: AnalysisPromptPackage) -> Mapping[str, Any]:
    """Return the exact C-1 brief subset exposed by the Anthropic projection."""

    brief = to_external(package.source.brief)
    visible = {
        key: deepcopy(brief[key])
        for key in MODEL_VISIBLE_BRIEF_KEYS
        if key in brief
    }
    critical = visible.get("criticalWarnings")
    if isinstance(critical, list):
        visible["criticalWarnings"] = [
            item for item in critical
            if not isinstance(item, Mapping)
            or item.get("code") not in HISTORICAL_ONLY_WARNING_CODES
        ]
    constraints = visible.get("interpretationConstraints")
    if isinstance(constraints, list):
        visible["interpretationConstraints"] = [
            item for item in constraints
            if not isinstance(item, str) or "feedback" not in item.casefold()
        ]
    eligible_ids = {
        evidence_id
        for evidence_id, item in package.source.evidence_by_id.items()
        if is_decision_evidence_item(item)
    }
    priority = visible.get("priorityEvidenceIds")
    if isinstance(priority, list):
        visible["priorityEvidenceIds"] = [item for item in priority if item in eligible_ids]
    core = visible.get("coreEvidenceIdsByDomain")
    if isinstance(core, Mapping):
        visible["coreEvidenceIdsByDomain"] = {
            key: [item for item in items if item in eligible_ids]
            for key, items in core.items()
            if isinstance(items, list)
        }
    return visible


def _warning_codes_from_visible_brief(value: Mapping[str, Any]) -> set[str]:
    codes: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            warning_codes = item.get("warningCodes")
            if isinstance(warning_codes, list):
                codes.update(
                    code for code in warning_codes
                    if isinstance(code, str) and code
                )
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    critical = value.get("criticalWarnings")
    if isinstance(critical, list):
        codes.update(
            str(item["code"])
            for item in critical
            if isinstance(item, Mapping) and item.get("code")
        )
    return codes


def collect_allowed_warning_codes(package: AnalysisPromptPackage) -> tuple[str, ...]:
    """Collect exact warning codes visible to the model from canonical C-1 input."""

    codes = _warning_codes_from_visible_brief(model_visible_brief(package))
    for item in package.source.evidence_by_id.values():
        if not is_decision_evidence_item(item):
            continue
        warning_codes = item.get("warningCodes")
        if isinstance(warning_codes, list):
            codes.update(
                code for code in warning_codes
                if isinstance(code, str) and code
            )
    return tuple(sorted(codes))


def warning_authority_digest(package: AnalysisPromptPackage) -> str:
    """Return a deterministic digest for warning-authority regression checks."""

    encoded = json.dumps(
        collect_allowed_warning_codes(package),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "MODEL_VISIBLE_BRIEF_KEYS",
    "collect_allowed_warning_codes",
    "model_visible_brief",
    "warning_authority_digest",
]
