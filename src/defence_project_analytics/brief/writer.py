"""Atomic writer for the four-file C-1 artifact contract."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable, Mapping

from defence_project_analytics.brief.loader import (
    FORBIDDEN_RAW_IDENTIFIERS,
    verify_source_bundle_unchanged,
)
from defence_project_analytics.brief.models import (
    ANALYSIS_BRIEF_VERSION,
    SELECTION_POLICY_VERSION,
    AnalysisBrief,
)
from defence_project_analytics.brief.renderers import evidence_payload, render_json
from defence_project_analytics.reporting.renderers import to_external
from defence_project_analytics.metric_registry import METRIC_REGISTRY_VERSION


_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.casefold()).strip("-") or "scope"


def canonical_scope_json(brief: AnalysisBrief) -> str:
    return json.dumps(
        to_external(brief.scope), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    )


def analysis_brief_scope_hash(brief: AnalysisBrief) -> str:
    return hashlib.sha256(canonical_scope_json(brief).encode("utf-8")).hexdigest()[:8]


def analysis_brief_scope_id(brief: AnalysisBrief) -> str:
    scope = brief.scope
    environment = re.sub(r"[^A-Za-z0-9]+", "-", scope.environment).strip("-") or "environment"
    stage = _slug(scope.stage_key) if scope.stage_key else "all-stages"
    digest = analysis_brief_scope_hash(brief)
    if scope.mode == "contentVersionCompare":
        return (
            f"compare__{environment}__{stage}__cv-{scope.baseline_content_version}"
            f"-vs-cv-{scope.candidate_content_version}__{digest}"
        )
    return f"single__{environment}__{stage}__cv-{scope.content_version}__{digest}"


def _manifest(
    brief: AnalysisBrief,
    generated_at: datetime,
) -> Mapping[str, Any]:
    return {
        "analysisBriefVersion": ANALYSIS_BRIEF_VERSION,
        "selectionPolicyVersion": SELECTION_POLICY_VERSION,
        "metricRegistryVersion": METRIC_REGISTRY_VERSION,
        "generatedAtUtc": generated_at,
        "mode": brief.scope.mode,
        "scope": brief.scope,
        "scopeHash": analysis_brief_scope_hash(brief),
        "sourceBundles": [
            {
                "sourceBundleId": f"{source.analysis_type}:{source.bundle_digest[:12]}",
                "analysisType": source.analysis_type,
                "domain": source.domain,
                "bundleName": source.bundle_name,
                "portablePath": source.portable_path,
                "portablePathAvailable": source.portable_path is not None,
                "bundleDigest": source.bundle_digest,
                "scope": source.metadata.get("scope", {}),
                "sample": source.metadata.get("sample", {}),
                "quality": source.metadata.get("quality", {}),
                "definitions": source.metadata.get("definitions", {}),
                "warnings": source.metadata.get("warnings", ()),
                "sourceAnalyses": source.metadata.get("sourceAnalyses", ()),
                "snapshot": source.snapshot,
                "artifacts": tuple(source.artifacts.values()),
            }
            for source in brief.sources
        ],
        "snapshotCompatibility": brief.snapshot_compatibility,
        "selectionSummary": brief.selection_summary,
        "semanticOutputDigest": brief.semantic_digest,
        "privacyScan": {"passed": True, "forbiddenIdentifiersFound": ()},
        "sourceMutationRecheck": {"passed": True},
        "bigQueryEstimatedBytes": 0,
        "cloudAccessPerformed": False,
        "sourceAnalyzersExecuted": False,
    }


def _assert_private_fields_absent(files: Mapping[str, str]) -> None:
    field_pattern = re.compile(
        r'"(' + "|".join(re.escape(item) for item in sorted(FORBIDDEN_RAW_IDENTIFIERS)) + r')"\s*:'
    )
    for filename, content in files.items():
        match = field_pattern.search(content)
        if match:
            raise ValueError(
                f"C-1 artifact {filename} contains forbidden raw identifier field {match.group(1)}"
            )


def _same_target(target: Path, brief: AnalysisBrief) -> bool:
    try:
        existing = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        existing.get("mode") == brief.scope.mode
        and existing.get("scope") == to_external(brief.scope)
    )


def write_analysis_brief(
    brief: AnalysisBrief,
    *,
    output_root: Path,
    overwrite: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(microsecond=0),
) -> Path:
    for source in brief.sources:
        verify_source_bundle_unchanged(source)
    target = output_root / "analysis-brief" / analysis_brief_scope_id(brief)
    empty_target = target.is_dir() and not any(target.iterdir())
    if target.exists() and (
        not overwrite or (not empty_target and not _same_target(target, brief))
    ):
        raise FileExistsError(f"Analysis Brief target already exists: {target}")
    generated_at = clock()
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("Analysis Brief writer clock must return a timezone-aware datetime")
    generated_at = generated_at.astimezone(timezone.utc).replace(microsecond=0)
    files = {
        "brief.md": brief.markdown.rstrip() + "\n",
        "brief.json": render_json(brief.brief_payload),
        "evidence.json": render_json(evidence_payload(brief.evidence)),
        "manifest.json": render_json(_manifest(brief, generated_at)),
    }
    _assert_private_fields_absent(files)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    backup: Path | None = None
    try:
        for filename, content in files.items():
            (temporary / filename).write_text(
                content, encoding="utf-8", newline="\n"
            )
        for source in brief.sources:
            verify_source_bundle_unchanged(source)
        if target.exists():
            backup = target.with_name(f".{target.name}.backup-{os.getpid()}")
            if backup.exists():
                raise FileExistsError(f"Atomic brief backup already exists: {backup}")
            target.rename(backup)
        temporary.rename(target)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return target
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise
