"""Immutable reader and validator for generated aggregate report bundles."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from defence_project_analytics.brief.errors import AnalysisBriefError, SourceBundleMutationError
from defence_project_analytics.brief.models import (
    LoadedSourceBundle,
    SnapshotDescriptor,
    SourceArtifact,
)
from defence_project_analytics.brief.registry import (
    ANALYSIS_TO_DOMAIN,
    REQUIRED_METRIC_KEYS,
    required_tables,
)
from defence_project_analytics.reporting.models import REPORT_CONTRACT_VERSION


SUPPORTED_ANALYSIS_VERSION = "1.0.0"
FORBIDDEN_RAW_IDENTIFIERS = frozenset(
    {
        "telemetryPlayerId",
        "runId",
        "attemptId",
        "eventId",
        "operationId",
        "exposureId",
        "presentationId",
        "batchId",
        "uploadId",
        "instanceId",
    }
)


def _reject_constant(value: str) -> None:
    raise AnalysisBriefError(f"Non-finite JSON constant is not allowed: {value}")


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    except FileNotFoundError as exc:
        raise AnalysisBriefError(f"Required source artifact is missing: {path.name}") from exc
    except (json.JSONDecodeError, UnicodeError, OSError) as exc:
        raise AnalysisBriefError(f"Invalid source JSON: {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisBriefError(f"Source JSON must contain an object: {path.name}")
    _validate_finite(value, path.name)
    return value


def _validate_finite(value: Any, location: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise AnalysisBriefError(f"Non-finite value in {location}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in FORBIDDEN_RAW_IDENTIFIERS:
                raise AnalysisBriefError(
                    f"Raw identifier field is forbidden in aggregate bundle: {key}"
                )
            _validate_finite(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite(item, f"{location}[{index}]")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, root: Path) -> SourceArtifact:
    return SourceArtifact(
        relative_path=path.relative_to(root).as_posix(),
        sha256=_sha256(path),
        size_bytes=path.stat().st_size,
    )


def _bundle_digest(artifacts: Mapping[str, SourceArtifact]) -> str:
    canonical = "\n".join(
        f"{name}\0{item.sha256}\0{item.size_bytes}"
        for name, item in sorted(artifacts.items())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_utc(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnalysisBriefError(f"{field_name} must be an ISO-8601 string or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalysisBriefError(f"Invalid {field_name}: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnalysisBriefError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _snapshot(analysis_type: str, scope: Mapping[str, Any]) -> SnapshotDescriptor | None:
    domain = ANALYSIS_TO_DOMAIN[analysis_type]
    if analysis_type == "contentVersionCompare":
        return SnapshotDescriptor(
            domain=domain,
            mode="sourceManifestDefined",
            cutoff_utc=_parse_utc(scope.get("analysisAsOfUtc"), "analysisAsOfUtc"),
            guarantee="B-6 source manifests preserve each source snapshot mode.",
        )
    if analysis_type == "stageDifficulty":
        cutoff = _parse_utc(scope.get("uploadedAtUtcEnd"), "uploadedAtUtcEnd")
        return SnapshotDescriptor(
            domain=domain,
            mode=("uploadedAtUtcUpperBound" if cutoff else "unboundedIngestionAtGeneration"),
            cutoff_utc=cutoff,
            guarantee=(
                "An uploadedAtUtc ingestion cutoff; not a BigQuery historical system-time snapshot."
                if cutoff
                else "No explicit ingestion cutoff was recorded."
            ),
        )
    cutoff = _parse_utc(scope.get("analysisAsOfUtc"), "analysisAsOfUtc")
    return SnapshotDescriptor(
        domain=domain,
        mode="analysisAsOfUtcParameter",
        cutoff_utc=cutoff,
        guarantee=(
            "A telemetry-row cutoff parameter; not a BigQuery historical system-time snapshot."
        ),
    )


def _portable_path(path: Path, workspace_root: Path | None) -> str | None:
    if workspace_root is None:
        return None
    try:
        return path.relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return None


def load_source_bundle(
    source_path: Path,
    *,
    workspace_root: Path | None = None,
) -> LoadedSourceBundle:
    root = source_path.resolve()
    if not root.is_dir():
        raise AnalysisBriefError(f"Source report path is not a directory: {source_path}")
    metadata = _load_json(root / "metadata.json")
    metrics = _load_json(root / "metrics.json")
    report = root / "report.md"
    if not report.is_file():
        raise AnalysisBriefError("Required source artifact is missing: report.md")

    analysis_type = metadata.get("analysisType")
    if analysis_type not in ANALYSIS_TO_DOMAIN:
        raise AnalysisBriefError(f"Unknown source analysisType: {analysis_type!r}")
    if metadata.get("reportContractVersion") != REPORT_CONTRACT_VERSION:
        raise AnalysisBriefError(
            f"Unsupported reportContractVersion: {metadata.get('reportContractVersion')!r}"
        )
    if metadata.get("analysisVersion") != SUPPORTED_ANALYSIS_VERSION:
        raise AnalysisBriefError(
            f"Unsupported analysisVersion for {analysis_type}: "
            f"{metadata.get('analysisVersion')!r}"
        )
    for key in ("scope", "sample", "quality", "definitions"):
        if not isinstance(metadata.get(key), dict):
            raise AnalysisBriefError(f"metadata.{key} must be an object")
    if not isinstance(metadata.get("warnings"), list):
        raise AnalysisBriefError("metadata.warnings must be an array")
    for warning in metadata["warnings"]:
        if not isinstance(warning, dict) or not isinstance(warning.get("code"), str):
            raise AnalysisBriefError("Each source warning must contain a string code")
    for key in REQUIRED_METRIC_KEYS[analysis_type]:
        if key not in metrics:
            raise AnalysisBriefError(f"metrics.json is missing required key: {key}")

    artifacts: dict[str, SourceArtifact] = {}
    tables: dict[str, pd.DataFrame] = {}
    for path in (root / "metadata.json", root / "metrics.json", report):
        item = _artifact(path, root)
        artifacts[item.relative_path] = item
    table_root = root / "tables"
    for filename, required_columns in required_tables(analysis_type).items():
        path = table_root / filename
        if not path.is_file():
            raise AnalysisBriefError(f"Required source table is missing: {filename}")
        try:
            frame = pd.read_csv(path)
        except (pd.errors.ParserError, UnicodeError, OSError) as exc:
            raise AnalysisBriefError(f"Invalid source CSV {filename}: {exc}") from exc
        missing = [column for column in required_columns if column not in frame.columns]
        if missing:
            raise AnalysisBriefError(
                f"Source CSV {filename} is missing columns: {', '.join(missing)}"
            )
        forbidden = FORBIDDEN_RAW_IDENTIFIERS.intersection(map(str, frame.columns))
        if forbidden:
            raise AnalysisBriefError(
                f"Raw identifier columns are forbidden in {filename}: {sorted(forbidden)}"
            )
        for column in frame.select_dtypes(include="number").columns:
            finite = frame[column].dropna().map(lambda item: math.isfinite(float(item)))
            if not finite.all():
                raise AnalysisBriefError(f"Non-finite value in {filename}.{column}")
        tables[filename] = frame
        item = _artifact(path, root)
        artifacts[item.relative_path] = item

    scope = metadata["scope"]
    return LoadedSourceBundle(
        path=root,
        analysis_type=analysis_type,
        domain=ANALYSIS_TO_DOMAIN[analysis_type],
        bundle_name=root.name,
        bundle_digest=_bundle_digest(artifacts),
        portable_path=_portable_path(root, workspace_root),
        metadata=metadata,
        metrics=metrics,
        tables=tables,
        artifacts=artifacts,
        snapshot=_snapshot(analysis_type, scope),
    )


def verify_source_bundle_unchanged(bundle: LoadedSourceBundle) -> None:
    for relative_path, artifact in bundle.artifacts.items():
        path = bundle.path / relative_path
        try:
            digest = _sha256(path)
            size = path.stat().st_size
        except OSError as exc:
            raise SourceBundleMutationError(
                f"Source artifact changed or disappeared: {bundle.bundle_name}/{relative_path}"
            ) from exc
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise SourceBundleMutationError(
                f"Source artifact changed during compilation: "
                f"{bundle.bundle_name}/{relative_path}"
            )
