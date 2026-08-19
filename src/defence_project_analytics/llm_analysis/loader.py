"""Strict immutable C-1 bundle consumer used by C-2."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from defence_project_analytics.brief.loader import FORBIDDEN_RAW_IDENTIFIERS
from defence_project_analytics.brief.models import ANALYSIS_BRIEF_VERSION, SELECTION_POLICY_VERSION
from defence_project_analytics.llm_analysis.errors import (
    SourceBriefMutationError,
    SourceBriefValidationError,
)
from defence_project_analytics.llm_analysis.models import (
    LoadedAnalysisBrief,
    SourceArtifactDigest,
    SourceBriefIdentity,
)


REQUIRED_FILES = frozenset({"brief.md", "brief.json", "evidence.json", "manifest.json"})
VALID_MODES = frozenset({"singleVersion", "contentVersionCompare"})
VALID_STATUSES = frozenset({"Ready", "Limited", "Insufficient"})
_SLUG = re.compile(r"[^a-z0-9]+")
_RAW_VALUE = re.compile(
    r"\b(" + "|".join(re.escape(item) for item in sorted(FORBIDDEN_RAW_IDENTIFIERS)) + r")\b"
)


def _reject_constant(value: str) -> None:
    raise SourceBriefValidationError(f"Non-finite JSON constant is forbidden: {value}")


def _json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    except FileNotFoundError as exc:
        raise SourceBriefValidationError(f"Missing C-1 artifact: {path.name}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBriefValidationError(f"Invalid C-1 JSON {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise SourceBriefValidationError(f"{path.name} must contain a JSON object")
    _finite_and_private(value, path.name)
    return value


def _finite_and_private(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise SourceBriefValidationError(f"Non-finite value at {path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in FORBIDDEN_RAW_IDENTIFIERS:
                raise SourceBriefValidationError(
                    f"Raw telemetry identifier field is forbidden: {key}"
                )
            _finite_and_private(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite_and_private(item, f"{path}[{index}]")
    elif isinstance(value, str) and _RAW_VALUE.search(value):
        raise SourceBriefValidationError(
            f"Raw telemetry identifier text is forbidden at {path}"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _artifact(path: Path, root: Path) -> SourceArtifactDigest:
    return SourceArtifactDigest(
        relative_path=path.relative_to(root).as_posix(),
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
    )


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.casefold()).strip("-") or "metric"


def _validate_evidence(items: Any) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(items, list):
        raise SourceBriefValidationError("evidenceItems must be an array")
    by_id: dict[str, Mapping[str, Any]] = {}
    identities: set[tuple[str, ...]] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SourceBriefValidationError(f"evidenceItems[{index}] must be an object")
        evidence_id = item.get("evidenceId")
        identity = item.get("canonicalIdentity")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise SourceBriefValidationError(f"evidenceItems[{index}].evidenceId is invalid")
        if evidence_id in by_id:
            raise SourceBriefValidationError(f"Duplicate Evidence ID: {evidence_id}")
        if not isinstance(identity, list) or len(identity) != 9 or not all(
            isinstance(part, str) for part in identity
        ):
            raise SourceBriefValidationError(
                f"evidenceItems[{index}].canonicalIdentity must have nine strings"
            )
        identity_tuple = tuple(identity)
        if identity_tuple in identities:
            raise SourceBriefValidationError(
                "Duplicate canonical evidence identity in C-1 bundle"
            )
        identities.add(identity_tuple)
        canonical = json.dumps(identity_tuple, ensure_ascii=False, separators=(",", ":"))
        short_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
        expected = f"EV-{_slug(str(item.get('domain', '')))}-{_slug(str(item.get('metric', '')))}-{short_hash}"
        if evidence_id != expected:
            raise SourceBriefValidationError(
                f"Evidence ID does not match canonical identity: {evidence_id}"
            )
        by_id[evidence_id] = item
    return by_id


def load_analysis_brief(
    path: Path,
    *,
    workspace_root: Path | None = None,
) -> LoadedAnalysisBrief:
    root = Path(path).resolve()
    if not root.is_dir():
        raise SourceBriefValidationError(f"C-1 bundle directory does not exist: {path}")
    actual_files = {item.name for item in root.iterdir() if item.is_file()}
    missing = REQUIRED_FILES - actual_files
    if missing:
        raise SourceBriefValidationError(
            f"Missing C-1 artifacts: {', '.join(sorted(missing))}"
        )
    brief = _json(root / "brief.json")
    evidence = _json(root / "evidence.json")
    manifest = _json(root / "manifest.json")
    try:
        (root / "brief.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SourceBriefValidationError(f"Invalid brief.md: {exc}") from exc

    if brief.get("analysisBriefVersion") != ANALYSIS_BRIEF_VERSION:
        raise SourceBriefValidationError("Unsupported analysisBriefVersion")
    if evidence.get("analysisBriefVersion") != ANALYSIS_BRIEF_VERSION:
        raise SourceBriefValidationError("evidence.json analysisBriefVersion mismatch")
    if manifest.get("analysisBriefVersion") != ANALYSIS_BRIEF_VERSION:
        raise SourceBriefValidationError("manifest analysisBriefVersion mismatch")
    if manifest.get("selectionPolicyVersion") not in {"1.0.0", SELECTION_POLICY_VERSION}:
        raise SourceBriefValidationError("Unsupported selectionPolicyVersion")
    mode = brief.get("mode")
    if mode not in VALID_MODES or manifest.get("mode") != mode:
        raise SourceBriefValidationError("Invalid or inconsistent C-1 mode")
    if brief.get("overallStatus") not in VALID_STATUSES:
        raise SourceBriefValidationError("Invalid C-1 overallStatus")
    scope = brief.get("scope")
    if not isinstance(scope, dict) or manifest.get("scope") != scope:
        raise SourceBriefValidationError("C-1 scope is missing or inconsistent")
    scope_hash = canonical_digest(scope)[:8]
    if manifest.get("scopeHash") != scope_hash:
        raise SourceBriefValidationError("C-1 scopeHash is invalid")
    semantic = canonical_digest({"brief": brief, "evidence": evidence})
    if manifest.get("semanticOutputDigest") != semantic:
        raise SourceBriefValidationError("C-1 semanticOutputDigest is invalid")
    evidence_by_id = _validate_evidence(evidence.get("evidenceItems"))
    selection = brief.get("selectionSummary")
    if not isinstance(selection, dict):
        raise SourceBriefValidationError("C-1 selectionSummary is missing")
    if selection != manifest.get("selectionSummary"):
        raise SourceBriefValidationError("C-1 selection summaries are inconsistent")
    if selection.get("selectedEvidenceCount") != len(evidence_by_id):
        raise SourceBriefValidationError("C-1 selected evidence count is inconsistent")

    artifacts = tuple(
        _artifact(root / name, root) for name in sorted(REQUIRED_FILES)
    )
    workspace = (workspace_root or Path.cwd()).resolve()
    try:
        portable = root.relative_to(workspace).as_posix()
    except ValueError:
        portable = None
    identity = SourceBriefIdentity(
        bundle_name=root.name,
        semantic_output_digest=semantic,
        scope_hash=scope_hash,
        mode=mode,
        environment=str(scope.get("environment")),
        content_version=scope.get("contentVersion"),
        baseline_content_version=scope.get("baselineContentVersion"),
        candidate_content_version=scope.get("candidateContentVersion"),
        stage_key=scope.get("stageKey"),
    )
    return LoadedAnalysisBrief(
        path=root,
        identity=identity,
        brief=brief,
        evidence=evidence,
        manifest=manifest,
        evidence_by_id=evidence_by_id,
        artifacts=artifacts,
        portable_path=portable,
    )


def verify_source_brief_unchanged(
    source: LoadedAnalysisBrief,
    *,
    path: Path | None = None,
) -> None:
    root = (path or source.path).resolve()
    for artifact in source.artifacts:
        current = root / artifact.relative_path
        try:
            digest = sha256_file(current)
            size = current.stat().st_size
        except OSError as exc:
            raise SourceBriefMutationError(
                f"SOURCE_BRIEF_MUTATED: {artifact.relative_path} is unavailable"
            ) from exc
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise SourceBriefMutationError(
                f"SOURCE_BRIEF_MUTATED: {artifact.relative_path} changed after prompt creation"
            )
