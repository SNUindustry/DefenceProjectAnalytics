"""Scope, snapshot, and warning compatibility for local source bundles."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from defence_project_analytics.brief.errors import AnalysisBriefError
from defence_project_analytics.brief.models import (
    COMPARISON_MODE,
    DOMAIN_ORDER,
    SINGLE_MODE,
    AnalysisBriefRequest,
    AnalysisBriefScope,
    BriefWarning,
    LoadedSourceBundle,
    SnapshotCompatibility,
)
from defence_project_analytics.brief.registry import (
    SINGLE_ANALYSIS_TYPES,
    STAGE_ANALYSIS_TYPES,
    active_source_warning_codes,
)


WARNING_PRIORITY = (
    "NO_OBSERVED_EVIDENCE",
    "PARTIAL_DOMAIN_COVERAGE",
    "SOURCE_SNAPSHOT_CUTOFF_MISSING",
    "SOURCE_SNAPSHOT_CUTOFF_MISMATCH",
    "SOURCE_SNAPSHOT_MODE_DIFFERENCE",
    "SOURCE_LIMITED_COMPARABILITY",
    "SOURCE_WARNING_PRESENT",
    "BRIEF_EVIDENCE_TRUNCATED",
)
LIMITING_WARNING_CODES = frozenset(WARNING_PRIORITY) - {
    "SOURCE_SNAPSHOT_MODE_DIFFERENCE"
}


def _common_scope(scope: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        scope.get(key)
        for key in (
            "environment", "contentVersion", "appVersion", "releaseId",
            "releaseChannel", "releaseType", "isDevelopmentBuild",
        )
    )


def validate_mode_and_scope(
    request: AnalysisBriefRequest,
    sources: tuple[LoadedSourceBundle, ...],
) -> None:
    analysis_types = [item.analysis_type for item in sources]
    if len(analysis_types) != len(set(analysis_types)):
        raise AnalysisBriefError("Duplicate analysisType source in one brief")
    if request.mode == COMPARISON_MODE:
        if analysis_types != ["contentVersionCompare"]:
            raise AnalysisBriefError(
                "contentVersionCompare mode requires exactly one contentVersionCompare bundle"
            )
        return
    if request.mode != SINGLE_MODE or not set(analysis_types).issubset(SINGLE_ANALYSIS_TYPES):
        raise AnalysisBriefError(
            "singleVersion mode accepts only B-1 through B-5 source bundles"
        )
    expected = _common_scope(sources[0].metadata["scope"])
    for source in sources[1:]:
        if _common_scope(source.metadata["scope"]) != expected:
            raise AnalysisBriefError(
                "Single-version source environment/contentVersion/common filters do not match"
            )
    stages = {
        item.metadata["scope"].get("stageKey")
        for item in sources
        if item.analysis_type in STAGE_ANALYSIS_TYPES
    }
    if len(stages) > 1:
        raise AnalysisBriefError(
            "Stage-dependent single-version source stageKey values differ"
        )


def snapshot_compatibility(
    sources: Iterable[LoadedSourceBundle],
    *,
    tolerance_seconds: float = 60.0,
) -> tuple[SnapshotCompatibility, tuple[BriefWarning, ...]]:
    descriptors = [item.snapshot for item in sources if item.snapshot is not None]
    modes = tuple(dict.fromkeys(item.mode for item in descriptors))
    cutoffs: list[tuple[str, datetime]] = [
        (item.domain, item.cutoff_utc)
        for item in descriptors
        if item.cutoff_utc is not None
    ]
    missing = tuple(item.domain for item in descriptors if item.cutoff_utc is None)
    difference = None
    if len(cutoffs) >= 2:
        timestamps = [value.timestamp() for _, value in cutoffs]
        difference = max(timestamps) - min(timestamps)
    compatibility = SnapshotCompatibility(
        source_modes=modes,
        mode_difference=len(modes) > 1,
        cutoff_difference_seconds=difference,
        missing_cutoff_domains=missing,
    )
    details = {
        "sourceModes": modes,
        "modeDifference": len(modes) > 1,
        "cutoffDifferenceSeconds": difference,
        "missingCutoffDomains": missing,
    }
    warnings: list[BriefWarning] = []
    if missing and cutoffs:
        warnings.append(BriefWarning(
            "SOURCE_SNAPSHOT_CUTOFF_MISSING",
            "At least one source has no explicit telemetry cutoff while another source is bounded.",
            details,
        ))
    if difference is not None and difference > tolerance_seconds:
        warnings.append(BriefWarning(
            "SOURCE_SNAPSHOT_CUTOFF_MISMATCH",
            "Explicit source telemetry cutoffs differ by more than 60 seconds.",
            details,
        ))
    if len(modes) > 1:
        warnings.append(BriefWarning(
            "SOURCE_SNAPSHOT_MODE_DIFFERENCE",
            "Source snapshot guarantee modes differ; this alone is not a cutoff mismatch.",
            details,
        ))
    return compatibility, sort_brief_warnings(warnings)


def initial_brief_warnings(
    request: AnalysisBriefRequest,
    sources: tuple[LoadedSourceBundle, ...],
    snapshot_warnings: tuple[BriefWarning, ...],
) -> tuple[BriefWarning, ...]:
    warnings = list(snapshot_warnings)
    if request.mode == SINGLE_MODE:
        present = {item.domain for item in sources}
        missing = tuple(domain for domain in DOMAIN_ORDER if domain not in present)
        if missing:
            warnings.append(BriefWarning(
                "PARTIAL_DOMAIN_COVERAGE",
                "The single-version brief contains only the explicitly supplied domains.",
                {"missingDomains": missing},
            ))
    source_warning_domains = tuple(
        item.domain
        for item in sources
        if active_source_warning_codes(item.metadata.get("warnings", ()))
    )
    if source_warning_domains:
        warnings.append(BriefWarning(
            "SOURCE_WARNING_PRESENT",
            "One or more source reports contain analysis warnings.",
            {"domains": source_warning_domains},
        ))
    if request.mode == COMPARISON_MODE:
        metrics = sources[0].metrics
        limited = tuple(
            domain
            for domain in DOMAIN_ORDER
            if isinstance(metrics.get(domain), Mapping)
            and metrics[domain].get("status") != "Comparable"
        )
        if limited:
            warnings.append(BriefWarning(
                "SOURCE_LIMITED_COMPARABILITY",
                "One or more B-6 source domains are not fully comparable.",
                {"domains": limited},
            ))
    return sort_brief_warnings(warnings)


def sort_brief_warnings(warnings: Iterable[BriefWarning]) -> tuple[BriefWarning, ...]:
    priority = {code: index for index, code in enumerate(WARNING_PRIORITY)}
    unique: dict[str, BriefWarning] = {}
    for warning in warnings:
        unique.setdefault(warning.code, warning)
    return tuple(sorted(
        unique.values(),
        key=lambda item: (priority.get(item.code, len(priority)), item.code),
    ))


def build_scope(
    request: AnalysisBriefRequest,
    sources: tuple[LoadedSourceBundle, ...],
) -> AnalysisBriefScope:
    scope = sources[0].metadata["scope"]
    policy = request.selection_policy
    if request.mode == COMPARISON_MODE:
        content_version = None
        baseline = int(scope["baselineContentVersion"])
        candidate = int(scope["candidateContentVersion"])
        stage = scope.get("stageKey")
        domains = tuple(scope.get("domains") or ())
    else:
        content_version = int(scope["contentVersion"])
        baseline = candidate = None
        stages = [
            item.metadata["scope"].get("stageKey")
            for item in sources
            if item.analysis_type in STAGE_ANALYSIS_TYPES
        ]
        stage = next((item for item in stages if item is not None), None)
        domains = tuple(
            domain for domain in DOMAIN_ORDER
            if any(source.domain == domain for source in sources)
        )
    return AnalysisBriefScope(
        mode=request.mode,
        environment=str(scope["environment"]),
        content_version=content_version,
        baseline_content_version=baseline,
        candidate_content_version=candidate,
        stage_key=stage,
        domains=domains,
        source_bundle_digests=tuple(item.bundle_digest for item in sources),
        max_brief_characters=policy.max_brief_characters,
        max_evidence_items=policy.max_evidence_items,
        max_evidence_items_per_domain=policy.max_evidence_items_per_domain,
        core_evidence_minimum_per_domain=policy.core_evidence_minimum_per_domain,
    )
