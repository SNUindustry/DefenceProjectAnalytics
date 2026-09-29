"""Safe deterministic report bundle writer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

import pandas as pd

from defence_project_analytics.reporting.models import (
    AnalysisScope,
    ContentVersionComparisonScope,
    PostRunAnalysisScope,
    ProgressionAnalysisScope,
    RunRetentionAnalysisScope,
    ObservedAppReturnScope,
    GaIdentityBridgeScope,
    ObservedUninstallScope,
    ReportBundle,
)
from defence_project_analytics.reporting.renderers import assert_factual_markdown, render_csv, render_json, to_external


_SLUG = re.compile(r"[^a-z0-9]+")


ReportScope = (
    AnalysisScope
    | ProgressionAnalysisScope
    | PostRunAnalysisScope
    | RunRetentionAnalysisScope
    | ObservedAppReturnScope
    | GaIdentityBridgeScope
    | ObservedUninstallScope
    | ContentVersionComparisonScope
)


def canonical_scope_json(scope: ReportScope) -> str:
    return json.dumps(to_external(scope), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def scope_hash(scope: ReportScope) -> str:
    return hashlib.sha256(canonical_scope_json(scope).encode("utf-8")).hexdigest()[:8]


def stage_slug(stage_key: str) -> str:
    value = _SLUG.sub("-", stage_key.casefold()).strip("-")
    return value or "stage"


def scope_id(scope: ReportScope) -> str:
    environment = re.sub(r"[^A-Za-z0-9]+", "-", scope.environment).strip("-")
    stage_key = getattr(scope, "stage_key", None)
    scope_stage = stage_slug(stage_key) if stage_key is not None else "all-stages"
    if isinstance(scope, ContentVersionComparisonScope):
        return (
            f"{environment}__{scope_stage}__cv-{scope.baseline_content_version}"
            f"-vs-cv-{scope.candidate_content_version}__{scope_hash(scope)}"
        )
    if isinstance(scope, (GaIdentityBridgeScope, ObservedUninstallScope)):
        return (
            f"{environment}__ga-{scope.ga_property_id}-{scope.ga_stream_id}"
            f"__{scope_hash(scope)}"
        )
    return f"{environment}__{scope_stage}__cv-{scope.content_version}__{scope_hash(scope)}"


def report_path(output_root: Path, analysis_type: str, scope: ReportScope) -> Path:
    kebab = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", analysis_type)
    safe_analysis = _SLUG.sub("-", kebab.casefold()).strip("-")
    if not safe_analysis:
        raise ValueError("analysis_type must contain a path-safe character")
    return output_root / safe_analysis / scope_id(scope)


def _same_target(target: Path, bundle: ReportBundle) -> bool:
    metadata_file = target / "metadata.json"
    try:
        existing = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return (
        existing.get("analysisType") == bundle.metadata.analysis_type
        and existing.get("scope") == to_external(bundle.metadata.scope)
    )


def write_report_bundle(
    bundle: ReportBundle,
    *,
    output_root: Path,
    overwrite: bool = False,
    table_specs: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] | None = None,
    include_manifest: bool = False,
) -> Path:
    """Write via a sibling temporary directory and atomically install the bundle."""

    target = report_path(output_root, bundle.metadata.analysis_type, bundle.metadata.scope)
    if target.exists() and (not overwrite or not _same_target(target, bundle)):
        raise FileExistsError(f"Report target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    backup: Path | None = None
    try:
        assert_factual_markdown(bundle.markdown)
        (temporary / "report.md").write_text(bundle.markdown.rstrip() + "\n", encoding="utf-8", newline="\n")
        (temporary / "metrics.json").write_text(render_json(bundle.metrics), encoding="utf-8", newline="\n")
        (temporary / "metadata.json").write_text(render_json(bundle.metadata), encoding="utf-8", newline="\n")
        specs = table_specs or {}
        if bundle.tables:
            table_dir = temporary / "tables"
            table_dir.mkdir()
            for filename, value in bundle.tables.items():
                if not filename.endswith(".csv") or Path(filename).name != filename:
                    raise ValueError(f"Invalid table filename: {filename}")
                frame = value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
                columns, sort_by = specs.get(filename, (tuple(frame.columns), ()))
                (table_dir / filename).write_text(
                    render_csv(frame, columns=columns, sort_by=sort_by), encoding="utf-8", newline="\n"
                )
        if include_manifest:
            files = sorted(
                path for path in temporary.rglob("*") if path.is_file()
            )
            manifest = {
                "analysisType": bundle.metadata.analysis_type,
                "analysisVersion": bundle.metadata.analysis_version,
                "files": {
                    path.relative_to(temporary).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in files
                },
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n",
            )
        if target.exists():
            backup = target.with_name(f".{target.name}.backup-{os.getpid()}")
            if backup.exists():
                raise FileExistsError(f"Atomic report backup already exists: {backup}")
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
