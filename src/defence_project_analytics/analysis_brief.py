"""Phase C-1 local-only Analysis Brief / Evidence Bundle compiler."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from defence_project_analytics.brief.adapters import (
    adapt_comparison_bundle,
    adapt_single_bundle,
    assign_evidence_ids,
)
from defence_project_analytics.brief.compatibility import (
    LIMITING_WARNING_CODES,
    build_scope,
    initial_brief_warnings,
    snapshot_compatibility,
    sort_brief_warnings,
    validate_mode_and_scope,
)
from defence_project_analytics.brief.loader import load_source_bundle
from defence_project_analytics.brief.models import (
    COMPARISON_MODE,
    AnalysisBrief,
    AnalysisBriefRequest,
    BriefWarning,
)
from defence_project_analytics.brief.renderers import (
    brief_payload,
    evidence_markdown,
    evidence_payload,
    mandatory_markdown,
    render_brief_markdown,
)
from defence_project_analytics.brief.selector import (
    select_evidence,
    with_rendered_size,
)
from defence_project_analytics.brief.writer import write_analysis_brief
from defence_project_analytics.reporting.renderers import to_external


def _source_order(source: Any) -> tuple[int, str]:
    order = {
        "stageDifficulty": 0,
        "weaponPerformance": 1,
        "upgradeChoice": 2,
        "progressionNextRun": 3,
        "postRunBehavior": 4,
        "contentVersionCompare": 5,
    }
    return order.get(source.analysis_type, len(order)), source.analysis_type


def _overall_status(warnings: tuple[BriefWarning, ...], has_evidence: bool) -> str:
    if not has_evidence:
        return "Insufficient"
    if any(item.code in LIMITING_WARNING_CODES for item in warnings):
        return "Limited"
    return "Ready"


def _semantic_digest(payload: Mapping[str, Any], evidence: Any) -> str:
    canonical = json.dumps(
        {"brief": to_external(payload), "evidence": to_external(evidence_payload(evidence))},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_analysis_brief(
    request: AnalysisBriefRequest,
    *,
    workspace_root: Path | None = None,
    evidence_hash_function: Callable[[bytes], Any] = hashlib.sha256,
) -> AnalysisBrief:
    workspace = (workspace_root or Path.cwd()).resolve()
    sources = tuple(sorted(
        (
            load_source_bundle(path, workspace_root=workspace)
            for path in request.source_report_paths
        ),
        key=_source_order,
    ))
    validate_mode_and_scope(request, sources)
    scope = build_scope(request, sources)
    snapshot, snapshot_warnings = snapshot_compatibility(sources)
    warnings = initial_brief_warnings(request, sources, snapshot_warnings)

    candidates = []
    if request.mode == COMPARISON_MODE:
        candidates.extend(adapt_comparison_bundle(sources[0]))
    else:
        for source in sources:
            candidates.extend(adapt_single_bundle(source, request.mode))
    # Full identity validation and short-hash assignment happen before selection.
    all_evidence = assign_evidence_ids(
        candidates, hash_function=evidence_hash_function
    )
    observed_count = sum(
        item.status != "Unavailable"
        and any(value is not None for value in item.value.values())
        for item in all_evidence
    )
    if observed_count == 0:
        warnings = sort_brief_warnings((*warnings, BriefWarning(
            "NO_OBSERVED_EVIDENCE",
            "The valid source bundles contain no observed registry evidence.",
            {},
        )))

    mandatory = mandatory_markdown(scope, sources, warnings, snapshot)
    selected, selection = select_evidence(
        all_evidence,
        request.selection_policy,
        mandatory_characters=len(mandatory),
        rendered_item_length=lambda item: len(evidence_markdown(item)),
    )
    if selection.truncated:
        warnings = sort_brief_warnings((*warnings, BriefWarning(
            "BRIEF_EVIDENCE_TRUNCATED",
            "Observed aggregate evidence was omitted by the configured selection budget.",
            {"omittedEvidenceCount": selection.omitted_evidence_count},
        )))
        mandatory = mandatory_markdown(scope, sources, warnings, snapshot)
        selected, selection = select_evidence(
            all_evidence,
            request.selection_policy,
            mandatory_characters=len(mandatory),
            rendered_item_length=lambda item: len(evidence_markdown(item)),
        )

    status = _overall_status(warnings, bool(selected))
    markdown = render_brief_markdown(mandatory, selected, selection)
    if len(markdown) > request.selection_policy.max_brief_characters:
        raise ValueError("Rendered brief exceeded max_brief_characters without item clipping")
    selection = with_rendered_size(selection, markdown)
    payload = brief_payload(
        scope, status, sources, warnings, snapshot, selected, selection
    )
    # Selection sizes are metadata, so semantic digest includes their stable values.
    semantic_digest = _semantic_digest(payload, selected)
    return AnalysisBrief(
        scope=scope,
        overall_status=status,
        sources=sources,
        warnings=warnings,
        snapshot_compatibility=snapshot,
        evidence=selected,
        selection_summary=selection,
        brief_payload=payload,
        markdown=markdown,
        semantic_digest=semantic_digest,
    )


def generate_analysis_brief(
    request: AnalysisBriefRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    workspace_root: Path | None = None,
) -> Path:
    brief = build_analysis_brief(request, workspace_root=workspace_root)
    return write_analysis_brief(
        brief, output_root=output_root, overwrite=overwrite
    )


__all__ = [
    "AnalysisBrief",
    "AnalysisBriefRequest",
    "build_analysis_brief",
    "generate_analysis_brief",
]
