"""Deterministic canonical JSON and Korean-first Markdown for validated C-2 output."""

from __future__ import annotations

import json
import math
from numbers import Real
from typing import Any, Mapping

from defence_project_analytics.llm_analysis.models import ValidatedAnalysis
from defence_project_analytics.llm_analysis.policy import forbidden_private_text
from defence_project_analytics.reporting.renderers import render_json, to_external


def render_analysis_json(analysis: ValidatedAnalysis) -> str:
    return render_json(analysis)


def _number(value: Any) -> str:
    if value is None:
        return "관측 불가"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Real):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("Non-finite evidence value")
        if numeric.is_integer():
            return f"{int(numeric):,}"
        return f"{numeric:.6g}"
    return str(value)


def _ratio(count: Any, denominator: Any, ratio: Any) -> str:
    rendered = "관측 불가" if ratio is None else f"{float(ratio):.1%}"
    return f"{_number(count)}/{_number(denominator)} ({rendered})"


def evidence_fact(evidence: Mapping[str, Any]) -> str:
    label = str(evidence.get("metric"))
    if evidence.get("entityKey"):
        label += f" [{evidence['entityKey']}]"
    value = evidence.get("value") or {}
    if "baselineValue" in value or "candidateValue" in value:
        if value.get("baselineCount") is not None or value.get("baselineDenominator") is not None:
            baseline = _ratio(
                value.get("baselineCount"), value.get("baselineDenominator"),
                value.get("baselineValue"),
            )
        else:
            baseline = _number(value.get("baselineValue"))
        if value.get("candidateCount") is not None or value.get("candidateDenominator") is not None:
            candidate = _ratio(
                value.get("candidateCount"), value.get("candidateDenominator"),
                value.get("candidateValue"),
            )
        else:
            candidate = _number(value.get("candidateValue"))
        if value.get("percentagePointDelta") is not None:
            delta = f"{float(value['percentagePointDelta']):+.1f}pp"
        elif value.get("absoluteDelta") is not None:
            delta = f"{float(value['absoluteDelta']):+.6g}"
        else:
            delta = "관측 불가"
        rendered = f"baseline {baseline}; candidate {candidate}; candidate-minus-baseline {delta}"
    elif evidence.get("valueType") == "ratio":
        rendered = _ratio(value.get("count"), value.get("denominator"), value.get("ratio"))
    elif evidence.get("valueType") == "distribution":
        rendered = "; ".join(
            f"{key}={_number(value.get(key))}"
            for key in ("observedCount", "p25", "median", "p75", "p90")
            if key in value
        )
    else:
        rendered = _number(value.get("value"))
    status = str(evidence.get("status"))
    warnings = ", ".join(map(str, evidence.get("warningCodes", []))) or "없음"
    return (
        f"{label}: {rendered} {evidence.get('unit', '')}. "
        f"Status={status}; warnings={warnings}."
    )


def render_analysis_markdown(
    analysis: ValidatedAnalysis,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    *,
    source_overall_status: str,
    source_warning_codes: tuple[str, ...],
    analysis_objective: str | None,
    output_language: str = "ko",
) -> str:
    # The stable v1 contract is Korean-first. English is intentionally compact.
    if output_language == "en":
        heading = "# Evidence-Grounded Analysis"
        human_notice = (
            "HumanReviewCandidate means a human-reviewed experiment candidate; "
            "it is not approval for production change or automatic deployment."
        )
    else:
        heading = "# 증거 기반 분석"
        human_notice = (
            "HumanReviewCandidate는 사람이 검토할 실험 후보를 뜻하며, "
            "production 변경이나 자동 수정·배포를 승인하지 않습니다."
        )
    observations = {item.id: item for item in analysis.observations}
    hypotheses = {item.id: item for item in analysis.hypotheses}
    gaps = {item.id: item for item in analysis.evidence_gaps}
    changes = {item.id: item for item in analysis.change_candidates}
    lines = [
        heading, "", "## 범위 및 증거 품질", "",
        f"- Mode: `{analysis.source_brief_identity.mode}`",
        f"- Environment: `{analysis.source_brief_identity.environment}`",
        f"- Source overall status: `{source_overall_status}`",
        "- Comparison direction: `candidate - baseline`" if analysis.source_brief_identity.mode == "contentVersionCompare" else "- Comparison direction: `notApplicable`",
        f"- Source warnings: `{', '.join(source_warning_codes) or 'none'}`",
        f"- Overall assessment: `{analysis.overall_assessment}`",
        f"- {human_notice}",
        "- 모든 evidence strength는 supplied C-1 brief 범위에 한정됩니다.",
        "- NotIdentifiedInSuppliedBrief는 실제 상충 증거의 부재를 의미하지 않습니다.",
    ]
    if analysis_objective:
        lines.extend(("", "## Design Objective", "", "> 측정 evidence가 아닌 사용자 제공 설계 맥락입니다.", "", analysis_objective))
    lines.extend(("", "## Executive Summary", "", analysis.executive_summary.qualitative_overview))
    for observation_id in analysis.executive_summary.observation_ids:
        observation = observations[observation_id]
        lines.append(f"- `{observation.id}` {observation.qualitative_statement}")
        for evidence_id in observation.evidence_ids:
            lines.append(f"  - [{evidence_id}] {evidence_fact(evidence_by_id[evidence_id])}")
    lines.extend(("", "## Observed Facts", ""))
    if not analysis.observations:
        lines.append("- 검증된 observation이 제출되지 않았습니다.")
    for item in analysis.observations:
        lines.extend((f"### {item.id} — {item.finding_type}", "", item.qualitative_statement, ""))
        for evidence_id in item.evidence_ids:
            lines.append(f"- [{evidence_id}] {evidence_fact(evidence_by_id[evidence_id])}")
    lines.extend(("", "## Interpretations", ""))
    for item in analysis.interpretations:
        lines.extend((f"### {item.id}", "", item.statement, "", f"- Evidence strength: `{item.evidence_strength}` (`suppliedC1Brief` 범위)"))
        lines.extend(f"- Evidence: [{value}]" for value in item.evidence_ids)
        lines.extend(f"- Limitation: [{value}]" for value in item.limitation_evidence_ids)
        lines.extend(f"- Warning: `{value}`" for value in item.limitation_warning_codes)
    lines.extend(("", "## Hypotheses", ""))
    for item in analysis.hypotheses:
        lines.extend((f"### {item.id}", "", item.statement, "", f"- Evidence strength: `{item.evidence_strength}` (`{item.evidence_strength_scope}` 범위)"))
        lines.extend(f"- Supporting evidence: [{value}]" for value in item.supporting_evidence_ids)
        lines.extend(f"- Counter evidence: [{value}]" for value in item.counter_evidence_ids)
        if item.counter_evidence_search_status == "NotIdentifiedInSuppliedBrief":
            lines.append("- 제공된 C-1 brief에서 상충 evidence를 식별하지 못했습니다. 실제 부재를 의미하지 않습니다.")
        else:
            lines.append("- 제공된 C-1 brief 안에서 counter evidence가 식별되었습니다.")
        lines.extend(f"- Alternative explanation: {value}" for value in item.alternative_explanations)
        lines.extend(f"- Falsification check: {value}" for value in item.falsification_checks)
    lines.extend(("", "## Evidence Gaps", ""))
    for item in analysis.evidence_gaps:
        lines.extend((f"### {item.id}", "", item.question, "", item.why_it_matters, "", f"- Suggested analysis: `{item.suggested_analysis or 'none'}`", f"- Requires new telemetry: `{str(item.requires_new_telemetry).lower()}`"))
    lines.extend(("", "## Change Candidates", ""))
    if not analysis.change_candidates:
        lines.append("- Evidence-supported change candidate가 식별되지 않았습니다.")
    for item in analysis.change_candidates:
        lines.extend((
            f"### {item.id} — {item.action_type}", "",
            f"- Actionability: `{item.actionability}`",
            f"- Evidence strength: `{item.evidence_strength}` (`{item.evidence_strength_scope}` 범위)",
            f"- Proposal: {item.proposed_change.description}",
            f"- Rationale: {item.rationale}",
        ))
        if item.proposed_change.amount_percent is not None:
            lines.append(
                f"- Heuristic tuning candidate: {item.proposed_change.direction} "
                f"{_number(item.proposed_change.amount_percent)}%. 이 magnitude는 evidence-derived optimum이 아닙니다."
            )
        lines.extend(f"- Supporting evidence: [{value}]" for value in item.supporting_evidence_ids)
        lines.extend(f"- Counter evidence: [{value}]" for value in item.counter_evidence_ids)
        if item.counter_evidence_search_status == "NotIdentifiedInSuppliedBrief":
            lines.append("- supplied brief에서 counter evidence를 식별하지 못했으며 실제 부재를 뜻하지 않습니다.")
        if item.actionability == "HumanReviewCandidate":
            lines.append(f"- {human_notice}")
        if item.validation_plan_id:
            lines.append(f"- Validation: `{item.validation_plan_id}`")
    lines.extend(("", "## Validation Plans", ""))
    for item in analysis.validation_plans:
        lines.extend((f"### {item.id}", "", f"- Change candidate: `{item.change_candidate_id}`", f"- Analyses: `{', '.join(item.analyses_to_rerun)}`", f"- Comparison plan: `{item.comparison_plan}`"))
        lines.extend(f"- Watch: `{x.domain}.{x.metric_family}.{x.metric}`" for x in item.metrics_to_watch)
        lines.extend(f"- Guardrail: `{x.domain}.{x.metric_family}.{x.metric}`" for x in item.guardrail_metrics)
        lines.extend(f"- Requirement: `{x}`" for x in item.minimum_evidence_requirements)
    lines.extend(("", "## Evidence References", ""))
    referenced = sorted({
        value
        for collection in (
            (x.evidence_ids for x in analysis.observations),
            (x.evidence_ids + x.limitation_evidence_ids for x in analysis.interpretations),
            (x.supporting_evidence_ids + x.counter_evidence_ids for x in analysis.hypotheses),
            (x.related_evidence_ids for x in analysis.evidence_gaps),
            (x.supporting_evidence_ids + x.counter_evidence_ids for x in analysis.change_candidates),
        )
        for ids in collection for value in ids
    })
    lines.extend(f"- [{value}]" for value in referenced)
    markdown = "\n".join(lines).rstrip() + "\n"
    private = forbidden_private_text(markdown)
    if private:
        raise ValueError(f"C-2 Markdown contains forbidden private text: {private}")
    return markdown


def analysis_payload(analysis: ValidatedAnalysis) -> Mapping[str, Any]:
    return to_external(analysis)

