"""Deterministic C-1 JSON and compact Markdown renderers."""

from __future__ import annotations

import json
import math
from numbers import Real
import re
from typing import Any, Iterable, Mapping

from defence_project_analytics.brief.models import (
    ANALYSIS_BRIEF_VERSION,
    COMPARISON_MODE,
    AnalysisBriefScope,
    BriefWarning,
    EvidenceItem,
    EvidenceSelectionSummary,
    LoadedSourceBundle,
    SnapshotCompatibility,
)
from defence_project_analytics.reporting.renderers import to_external


_PROHIBITED_PROSE = re.compile(
    r"\b(buff|nerf|overtuned|undertuned|better|worse|regressed|caused|should)\b",
    re.IGNORECASE,
)


def render_json(value: Any) -> str:
    return json.dumps(
        to_external(value), ensure_ascii=False, allow_nan=False, indent=2
    ) + "\n"


def _number(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Real):
        if not math.isfinite(float(value)):
            raise ValueError("Non-finite evidence value")
        if isinstance(value, int) or float(value).is_integer():
            return f"{int(value):,}"
        return f"{float(value):.6g}"
    return str(value)


def _ratio(value: Mapping[str, Any]) -> str:
    count, denominator, ratio = (
        value.get("count"), value.get("denominator"), value.get("ratio")
    )
    rendered = (
        "unavailable" if ratio is None else f"{float(ratio):.1%}"
    )
    return f"{_number(count)}/{_number(denominator)} ({rendered})"


def _comparison(value: Mapping[str, Any]) -> str:
    if value.get("baselineCount") is not None or value.get("baselineDenominator") is not None:
        baseline = _ratio({
            "count": value.get("baselineCount"),
            "denominator": value.get("baselineDenominator"),
            "ratio": value.get("baselineValue"),
        })
    else:
        baseline = _number(value.get("baselineValue"))
    if value.get("candidateCount") is not None or value.get("candidateDenominator") is not None:
        candidate = _ratio({
            "count": value.get("candidateCount"),
            "denominator": value.get("candidateDenominator"),
            "ratio": value.get("candidateValue"),
        })
    else:
        candidate = _number(value.get("candidateValue"))
    if value.get("percentagePointDelta") is not None:
        delta = f"{float(value['percentagePointDelta']):+.1f}pp"
    elif value.get("absoluteDelta") is not None:
        delta = f"{float(value['absoluteDelta']):+.6g}"
    else:
        delta = "unavailable"
    return f"baseline {baseline}; candidate {candidate}; candidate-minus-baseline {delta}"


def evidence_sentence(item: EvidenceItem) -> str:
    label = item.metric
    if item.entity_key:
        label += f" [{item.entity_key}]"
    if "baselineValue" in item.value or "candidateValue" in item.value:
        value = _comparison(item.value)
    elif item.value_type == "ratio":
        value = _ratio(item.value)
    elif item.value_type == "distribution":
        value = "; ".join(
            f"{key}={_number(item.value.get(key))}"
            for key in ("observedCount", "p25", "median", "p75", "p90")
            if key in item.value
        )
    else:
        value = _number(item.value.get("value"))
    return f"{label}: {value} {item.unit}."


def evidence_markdown(item: EvidenceItem) -> str:
    lines = [
        f"- [{item.evidence_id}] {evidence_sentence(item)}",
        f"  Status: `{item.status}`; observation unit: `{item.observation_unit}`.",
    ]
    if item.warning_codes:
        lines.append("  Warnings: " + ", ".join(f"`{code}`" for code in item.warning_codes) + ".")
    return "\n".join(lines) + "\n"


def mandatory_markdown(
    scope: AnalysisBriefScope,
    sources: tuple[LoadedSourceBundle, ...],
    warnings: tuple[BriefWarning, ...],
    snapshot: SnapshotCompatibility,
) -> str:
    lines = [
        "# Analysis Brief",
        "",
        "## Scope and Mode",
        "",
        f"- Mode: `{scope.mode}`",
        f"- Environment: `{scope.environment}`",
    ]
    if scope.mode == COMPARISON_MODE:
        lines.extend((
            f"- Baseline contentVersion: `{scope.baseline_content_version}`",
            f"- Candidate contentVersion: `{scope.candidate_content_version}`",
            "- Direction: `candidate - baseline`",
        ))
    else:
        lines.append(f"- contentVersion: `{scope.content_version}`")
    lines.extend((
        f"- Stage: `{scope.stage_key or 'domain-specific/all-stages'}`",
        f"- Included domains: `{', '.join(scope.domains)}`",
        "",
        "## Critical Interpretation Constraints",
        "",
        "- Evidence is observational and does not establish causality.",
        "- Missing or unobserved values are not zero.",
        "- Best-effort telemetry absence does not establish behavior absence.",
        "- Programmatic presentation does not establish user intent.",
        "- No-next telemetry does not establish player retention behavior.",
        "- This is a selected subset of aggregate evidence.",
        "",
        "## Source / Comparability Status",
        "",
    ))
    for source in sources:
        warning_codes = [item.get("code") for item in source.metadata.get("warnings", ())]
        lines.append(
            f"- `{source.domain}`: sample `{json.dumps(source.metadata.get('sample', {}), ensure_ascii=False, separators=(',', ':'))}`; "
            f"source warnings `{','.join(str(code) for code in warning_codes) or 'none'}`."
        )
    lines.extend((
        f"- Snapshot modes: `{', '.join(snapshot.source_modes) or 'none'}`",
        f"- Cutoff difference seconds: `{_number(snapshot.cutoff_difference_seconds)}`",
        "",
        "## Brief Warnings",
        "",
    ))
    if warnings:
        lines.extend(f"- `{item.code}`: {item.message}" for item in warnings)
    else:
        lines.append("- None.")
    lines.extend(("", "## Sample and Coverage", ""))
    for source in sources:
        quality = json.dumps(
            source.metadata.get("quality", {}),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines.append(f"- `{source.domain}` quality: `{quality}`")
    return "\n".join(lines) + "\n"


def render_brief_markdown(
    mandatory: str,
    evidence: tuple[EvidenceItem, ...],
    summary: EvidenceSelectionSummary,
) -> str:
    designated = tuple(item for item in evidence if item.source_designated)
    core = tuple(item for item in evidence if item.core and not item.source_designated)
    supporting = tuple(item for item in evidence if not item.core and not item.source_designated)
    parts = [mandatory]
    for heading, items in (
        ("Highest-Priority Observed Evidence", designated),
        ("Domain Core Evidence", core),
        ("Supporting Domain Evidence", supporting),
    ):
        parts.append(f"\n## {heading}\n\n")
        if items:
            parts.extend(evidence_markdown(item) for item in items)
        else:
            parts.append("- No selected evidence in this category.\n")
    parts.extend((
        "\n## Missing or Limited Evidence\n\n",
        f"- Unavailable source facts: `{summary.unavailable_in_source}`.\n",
        f"- Evidence omitted by selection budget: `{summary.omitted_by_budget}`.\n",
        "\n## Selection / Truncation Summary\n\n",
        f"- Selected: `{summary.selected_evidence_count}` of `{summary.eligible_evidence_count}` observed evidence items.\n",
        f"- Truncated: `{str(summary.truncated).lower()}`.\n",
        "\n## Evidence Reference\n\n",
        "- Canonical selected facts and provenance are in `evidence.json`.\n",
        "- Reproducibility and source hashes are in `manifest.json`.\n",
    ))
    markdown = "".join(parts).rstrip() + "\n"
    assert_safe_brief_markdown(markdown)
    return markdown


def assert_safe_brief_markdown(markdown: str) -> None:
    match = _PROHIBITED_PROSE.search(markdown)
    if match:
        raise ValueError(f"Analysis brief contains prohibited prose: {match.group(0)}")


def brief_payload(
    scope: AnalysisBriefScope,
    overall_status: str,
    sources: tuple[LoadedSourceBundle, ...],
    warnings: tuple[BriefWarning, ...],
    snapshot: SnapshotCompatibility,
    evidence: tuple[EvidenceItem, ...],
    summary: EvidenceSelectionSummary,
) -> Mapping[str, Any]:
    by_domain = {
        domain: [item.evidence_id for item in evidence if item.domain == domain]
        for domain in scope.domains
    }
    return {
        "analysisBriefVersion": ANALYSIS_BRIEF_VERSION,
        "mode": scope.mode,
        "scope": scope,
        "overallStatus": overall_status,
        "sourceStatusByDomain": {
            source.domain: {
                "analysisType": source.analysis_type,
                "sample": source.metadata.get("sample", {}),
                "quality": source.metadata.get("quality", {}),
                "warningCodes": [
                    item.get("code") for item in source.metadata.get("warnings", ())
                ],
            }
            for source in sources
        },
        "criticalWarnings": warnings,
        "snapshotCompatibility": snapshot,
        "priorityEvidenceIds": [
            item.evidence_id for item in evidence if item.source_designated
        ],
        "coreEvidenceIdsByDomain": {
            domain: [
                item.evidence_id for item in evidence
                if item.domain == domain and item.core
            ]
            for domain in scope.domains
        },
        "domainEvidenceIds": by_domain,
        "interpretationConstraints": {
            "causalClaimsAllowed": False,
            "missingMeansZero": False,
            "bestEffortAbsenceIsDefinitive": False,
            "programmaticNavigationMeansUserIntent": False,
            "noNextRunMeansRetentionOutcome": False,
        },
        "selectionSummary": summary,
    }


def evidence_payload(evidence: Iterable[EvidenceItem]) -> Mapping[str, Any]:
    return {
        "analysisBriefVersion": ANALYSIS_BRIEF_VERSION,
        "evidenceItems": tuple(evidence),
    }
