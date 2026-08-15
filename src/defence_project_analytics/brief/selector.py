"""Deterministic, quota-aware evidence selection."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Callable, Iterable

from defence_project_analytics.brief.errors import AnalysisBriefError
from defence_project_analytics.brief.models import (
    DOMAIN_ORDER,
    EvidenceItem,
    EvidenceSelectionPolicy,
    EvidenceSelectionSummary,
)


_DOMAIN_INDEX = {domain: index for index, domain in enumerate(DOMAIN_ORDER)}
_STATUS_INDEX = {"Comparable": 0, "Limited": 1, "Unavailable": 2, "Incompatible": 3}


def _denominator(item: EvidenceItem) -> float:
    for key in ("denominator", "candidateDenominator", "baselineDenominator"):
        value = item.value.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def evidence_sort_key(item: EvidenceItem) -> tuple[object, ...]:
    return (
        item.priority,
        _DOMAIN_INDEX.get(item.domain, len(_DOMAIN_INDEX)),
        _STATUS_INDEX.get(item.status, len(_STATUS_INDEX)),
        item.metric_family,
        item.metric,
        -_denominator(item),
        item.entity_key or "",
        item.dimension_value or "",
        item.evidence_id,
    )


def select_evidence(
    items: Iterable[EvidenceItem],
    policy: EvidenceSelectionPolicy,
    *,
    mandatory_characters: int,
    rendered_item_length: Callable[[EvidenceItem], int],
    character_reserve: int = 2_000,
) -> tuple[tuple[EvidenceItem, ...], EvidenceSelectionSummary]:
    all_items = tuple(items)
    observed = sorted(
        (
            item for item in all_items
            if item.status != "Unavailable"
            and any(value is not None for value in item.value.values())
        ),
        key=evidence_sort_key,
    )
    unavailable = len(all_items) - len(observed)
    by_domain = {
        domain: [item for item in observed if item.domain == domain]
        for domain in DOMAIN_ORDER
    }
    selected: dict[str, EvidenceItem] = {}
    guaranteed: set[str] = set()
    counts: Counter[str] = Counter()

    # Reserve core quota before optional source-designated and entity rows can fill caps.
    core_available: dict[str, int] = {}
    for domain, domain_items in by_domain.items():
        core = [item for item in domain_items if item.core]
        core_available[domain] = len(core)
        target = min(policy.core_evidence_minimum_per_domain, len(core))
        for item in core[:target]:
            selected[item.evidence_id] = item
            guaranteed.add(item.evidence_id)
            counts[domain] += 1

    if len(selected) > policy.max_evidence_items:
        raise AnalysisBriefError(
            "max_evidence_items cannot contain the observed per-domain core quota"
        )
    if any(count > policy.max_evidence_items_per_domain for count in counts.values()):
        raise AnalysisBriefError(
            "max_evidence_items_per_domain cannot contain the observed core quota"
        )
    guaranteed_characters = sum(
        rendered_item_length(item) for item in selected.values()
    )
    if mandatory_characters + guaranteed_characters + character_reserve > policy.max_brief_characters:
        raise AnalysisBriefError(
            "max_brief_characters cannot contain mandatory sections and core evidence quota"
        )

    used_characters = mandatory_characters + guaranteed_characters + character_reserve
    for item in observed:
        if item.evidence_id in selected:
            continue
        if len(selected) >= policy.max_evidence_items:
            continue
        if counts[item.domain] >= policy.max_evidence_items_per_domain:
            continue
        length = rendered_item_length(item)
        if used_characters + length > policy.max_brief_characters:
            continue
        selected[item.evidence_id] = item
        counts[item.domain] += 1
        used_characters += length

    ordered = tuple(sorted(selected.values(), key=evidence_sort_key))
    selected_by_domain = {
        domain: sum(item.domain == domain for item in ordered)
        for domain in DOMAIN_ORDER if by_domain[domain]
    }
    omitted_by_domain = {
        domain: max(0, len(by_domain[domain]) - selected_by_domain.get(domain, 0))
        for domain in DOMAIN_ORDER if by_domain[domain]
    }
    core_selected = {
        domain: sum(item.domain == domain and item.core for item in ordered)
        for domain in DOMAIN_ORDER if core_available.get(domain, 0)
    }
    omitted = len(observed) - len(ordered)
    summary = EvidenceSelectionSummary(
        eligible_evidence_count=len(observed),
        selected_evidence_count=len(ordered),
        omitted_evidence_count=omitted,
        omitted_by_budget=omitted,
        unavailable_in_source=unavailable,
        selected_by_domain=selected_by_domain,
        omitted_by_domain=omitted_by_domain,
        core_selected_by_domain=core_selected,
        core_available_by_domain={
            domain: value for domain, value in core_available.items() if value
        },
        truncated=omitted > 0,
    )
    return ordered, summary


def with_rendered_size(
    summary: EvidenceSelectionSummary,
    markdown: str,
) -> EvidenceSelectionSummary:
    return replace(
        summary,
        brief_character_count=len(markdown),
        brief_byte_count=len(markdown.encode("utf-8")),
    )
