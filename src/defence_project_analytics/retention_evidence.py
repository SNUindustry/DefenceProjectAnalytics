"""R4-B deterministic composition of restricted retention evidence facts.

The module consumes immutable normalized artifacts.  It never queries raw
telemetry or GA tables and never changes the B-7, B-8, or R3-D definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

import pandas as pd

from defence_project_analytics.retention_evidence_policy import (
    AuthorityContext,
    ComparisonPlan,
    HorizonContract,
    POLICY_VERSION,
    RetentionEvidenceFactKind,
    SourceCut,
    SourceFinalizationState,
    resolve_authority,
)
from defence_project_analytics.reporting.renderers import render_csv


ANALYSIS_TYPE = "retentionEvidence"
ANALYSIS_VERSION = "1.0.0"
REPORT_CONTRACT_VERSION = "1.0.0"
RESTRICTED_CONTRACT_VERSION = "1.0.0"

RETURN_SOURCE_COLUMNS = (
    "canonicalProfileId", "anchorId", "anchorEndUtc", "anchorEligible",
    "returnObservedAtUtc", "returnClassification", "telemetryBackend",
    "environment", "contentVersion", "releaseId", "completeSourceCoverage",
)
RESTRICTED_EPISODE_COLUMNS = (
    "canonicalProfileId", "episodeId", "anchorEndUtc", "naturalEndUtc",
    "gameplayEligible", "gameplayReturnObservedAtUtc", "gameplayState",
    "appEligible", "appReturnObservedAtUtc", "appState",
    "mappedUninstallCount", "sequenceFacets", "sameObservedTime",
)
RAW_IDENTIFIER_TOKENS = (
    "canonicalProfileId", "telemetryPlayerId", "retentionBridgeId",
    "userPseudoId", "user_pseudo_id", "lifecycleOccurrenceId", "attemptId",
    "runId", "appProcessSessionId", "anchorId", "episodeId",
)
NORMAL_FILES = (
    "metrics.json", "metadata.json", "evidence-summary.csv",
    "sequence-summary.csv", "maturity-summary.csv", "censoring-summary.csv",
    "uninstall-attribution-quality-summary.csv",
    "source-compatibility-summary.csv", "report.md",
)


class ReturnDomain(str, Enum):
    GAMEPLAY = "gameplayReturn"
    APP = "appReturn"


@dataclass(frozen=True, slots=True)
class RetentionEvidenceRequest:
    backend: str
    environment: str
    analysis_as_of_utc: datetime
    horizon_days: int | None = None
    source_upload_grace_hours: int | None = None
    content_version: int | None = None
    release_id: str | None = None
    comparison_plan: ComparisonPlan | None = None

    def __post_init__(self) -> None:
        if self.backend not in {"test", "production"}:
            raise ValueError("backend must be test or production")
        expected = "Test" if self.backend == "test" else "Production"
        if self.environment != expected:
            raise ValueError("physical backend and logical environment mismatch")
        value = _utc(self.analysis_as_of_utc, "analysis_as_of_utc")
        object.__setattr__(self, "analysis_as_of_utc", value)
        HorizonContract(self.horizon_days, self.source_upload_grace_hours)
        if (self.horizon_days is None) != (self.source_upload_grace_hours is None):
            raise ValueError("horizon and source upload grace must be provided together")
        if self.content_version is not None and (
            isinstance(self.content_version, bool) or not isinstance(self.content_version, int)
        ):
            raise ValueError("content_version must be an integer or null")
        if self.release_id is not None and not self.release_id.strip():
            raise ValueError("release_id must be non-empty or null")
        if self.comparison_plan is not None and not isinstance(
            self.comparison_plan, ComparisonPlan
        ):
            raise TypeError("comparison_plan must be ComparisonPlan or null")

    @property
    def horizon(self) -> HorizonContract:
        return HorizonContract(self.horizon_days, self.source_upload_grace_hours)

    def scope_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "environment": self.environment,
            "analysisAsOfUtc": _iso(self.analysis_as_of_utc),
            "horizonDays": self.horizon_days,
            "sourceUploadGraceHours": self.source_upload_grace_hours,
            "contentVersion": self.content_version,
            "releaseId": self.release_id,
        }


@dataclass(frozen=True, slots=True)
class LoadedArtifact:
    path: Path
    artifact_type: str
    frame: pd.DataFrame
    source_cut: SourceCut
    manifest_digest: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RetentionEvidenceAnalysis:
    metrics: Mapping[str, Any]
    metadata: Mapping[str, Any]
    tables: Mapping[str, pd.DataFrame]
    restricted_episodes: pd.DataFrame
    source_artifacts: tuple[LoadedArtifact, ...]


@dataclass(frozen=True, slots=True)
class RetentionEvidenceOutput:
    report_path: Path
    restricted_path: Path
    analysis: RetentionEvidenceAnalysis


def _utc(value: Any, label: str) -> datetime:
    parsed = value if isinstance(value, datetime) else pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{label} must be a valid timestamp")
    if isinstance(parsed, pd.Timestamp):
        parsed = parsed.to_pydatetime()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _optional_utc(value: Any) -> datetime | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return _utc(value, "event timestamp")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON artifact file: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"artifact JSON must be an object: {path}")
    return value


def _verify_manifest(path: Path) -> tuple[dict[str, Any], str]:
    manifest_path = path / "manifest.json"
    manifest = _read_json(manifest_path)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("artifact manifest files must be a non-empty object")
    for relative, expected in files.items():
        candidate = path / str(relative)
        if Path(str(relative)).is_absolute() or ".." in Path(str(relative)).parts:
            raise ValueError("artifact manifest contains an unsafe path")
        if not candidate.is_file() or _sha(candidate) != expected:
            raise ValueError(f"artifact manifest digest mismatch: {relative}")
    return manifest, _sha(manifest_path)


def _bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{label} must be boolean")


def write_return_source_artifact(
    frame: pd.DataFrame,
    *,
    domain: ReturnDomain,
    output_root: Path,
    backend: str,
    environment: str,
    analysis_as_of_utc: datetime,
    finalization_state: SourceFinalizationState = SourceFinalizationState.FINAL,
    overwrite: bool = False,
) -> Path:
    """Write the additive B-7/B-8 restricted normalized input contract."""
    missing = [item for item in RETURN_SOURCE_COLUMNS if item not in frame.columns]
    if missing:
        raise ValueError(f"restricted {domain.value} input is missing columns: {', '.join(missing)}")
    expected = "Test" if backend == "test" else "Production" if backend == "production" else None
    if expected is None or environment != expected:
        raise ValueError("physical backend and logical environment mismatch")
    as_of = _utc(analysis_as_of_utc, "analysis_as_of_utc")
    artifact_type = (
        "restrictedRunRetentionAnchors" if domain is ReturnDomain.GAMEPLAY
        else "restrictedObservedAppReturnAnchors"
    )
    normalized = frame.loc[:, RETURN_SOURCE_COLUMNS].copy()
    normalized["anchorEndUtc"] = normalized["anchorEndUtc"].map(lambda v: _iso(_utc(v, "anchorEndUtc")))
    normalized["returnObservedAtUtc"] = normalized["returnObservedAtUtc"].map(lambda v: _iso(_optional_utc(v)))
    normalized["anchorEligible"] = normalized["anchorEligible"].map(lambda v: _bool(v, "anchorEligible"))
    normalized["completeSourceCoverage"] = normalized["completeSourceCoverage"].map(lambda v: _bool(v, "completeSourceCoverage"))
    csv_text = render_csv(
        normalized, columns=RETURN_SOURCE_COLUMNS,
        sort_by=("anchorEndUtc", "canonicalProfileId", "anchorId"),
    )
    identity_payload = {
        "artifactType": artifact_type, "backend": backend, "environment": environment,
        "analysisAsOfUtc": _iso(as_of), "rows": len(frame),
        "contentDigest": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
    }
    target = output_root / artifact_type / _canonical_digest(identity_payload)[:16]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"Restricted artifact target already exists: {target}")
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    backup: Path | None = None
    try:
        csv_name = "retention-anchors.csv"
        (temp / csv_name).write_text(csv_text, encoding="utf-8", newline="\n")
        metadata = {
            **identity_payload,
            "contractVersion": RESTRICTED_CONTRACT_VERSION,
            "classification": "RestrictedRawIdentity",
            "sourceDomain": domain.value,
            "sourceFinalizationState": finalization_state.value,
            "permittedUse": "R4 normalized factual input",
            "forbiddenUse": "normal report, C-1 evidence, C-2 context, public analysis",
        }
        (temp / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        manifest = {"artifactType": artifact_type, "files": {name: _sha(temp / name) for name in (csv_name, "metadata.json")}}
        (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        for item in temp.iterdir():
            if item.is_file():
                try: os.chmod(item, 0o600)
                except OSError: pass
        if target.exists():
            backup = target.with_name(f".{target.name}.backup-{os.getpid()}")
            target.rename(backup)
        temp.rename(target)
        if backup is not None: shutil.rmtree(backup, ignore_errors=True)
        return target
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists(): backup.rename(target)
        raise


def load_restricted_artifact(path: Path) -> LoadedArtifact:
    path = path.resolve()
    manifest, manifest_digest = _verify_manifest(path)
    metadata = _read_json(path / "metadata.json")
    artifact_type = str(metadata.get("artifactType") or manifest.get("artifactType") or "")
    if artifact_type in {"restrictedRunRetentionAnchors", "restrictedObservedAppReturnAnchors"}:
        csv_path = path / "retention-anchors.csv"
        frame = pd.read_csv(csv_path, keep_default_na=False)
        missing = [item for item in RETURN_SOURCE_COLUMNS if item not in frame.columns]
        if missing: raise ValueError(f"restricted return artifact missing columns: {', '.join(missing)}")
    elif artifact_type == "restrictedObservedUninstallEvents":
        frame = pd.read_csv(path / "observed-uninstall-events.csv", keep_default_na=False)
        required = {"eventTimestampUtc", "attributionStatus", "telemetryPlayerId", "sourceFinalizationState", "telemetryBackend", "environment"}
        missing = sorted(required - set(frame.columns))
        if missing: raise ValueError(f"restricted uninstall artifact missing columns: {', '.join(missing)}")
    else:
        raise ValueError(f"unsupported restricted artifact type: {artifact_type}")
    scope = metadata.get("scope") if isinstance(metadata.get("scope"), dict) else metadata
    as_of_raw = scope.get("analysisAsOfUtc")
    state_raw = metadata.get("sourceFinalizationState")
    if not state_raw and artifact_type == "restrictedObservedUninstallEvents":
        states = set(frame["sourceFinalizationState"].astype(str))
        state_raw = "Final" if states <= {"Final"} else "Provisional"
    try: state = SourceFinalizationState(state_raw)
    except (ValueError, TypeError): state = None
    source_cut = SourceCut(str(path), manifest_digest, state, _optional_utc(as_of_raw))
    return LoadedArtifact(path, artifact_type, frame, source_cut, manifest_digest, metadata)


def _validate_scope(artifact: LoadedArtifact, request: RetentionEvidenceRequest) -> None:
    frame = artifact.frame
    for column, expected in (("telemetryBackend", request.backend), ("environment", request.environment)):
        if column in frame.columns and set(frame[column].astype(str)) - {expected}:
            raise ValueError(f"{artifact.artifact_type} has incompatible {column}")
    if request.content_version is not None and "contentVersion" in frame.columns:
        values = {int(v) for v in frame["contentVersion"] if str(v).strip()}
        if values - {request.content_version}:
            raise ValueError("source contentVersion is incompatible with request scope")
    if request.release_id is not None and "releaseId" in frame.columns:
        if set(frame["releaseId"].astype(str)) - {request.release_id}:
            raise ValueError("source releaseId is incompatible with request scope")
    if artifact.source_cut.analysis_as_of_utc is None or artifact.source_cut.analysis_as_of_utc > request.analysis_as_of_utc:
        raise ValueError("source cut as-of is missing or later than analysisAsOfUtc")


def _prepare_returns(artifact: LoadedArtifact, request: RetentionEvidenceRequest, domain: ReturnDomain) -> list[dict[str, Any]]:
    _validate_scope(artifact, request)
    expected = "restrictedRunRetentionAnchors" if domain is ReturnDomain.GAMEPLAY else "restrictedObservedAppReturnAnchors"
    if artifact.artifact_type != expected:
        raise ValueError(f"expected {expected}, got {artifact.artifact_type}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in artifact.frame.to_dict("records"):
        anchor_id = str(record["anchorId"])
        if not anchor_id or anchor_id in seen: raise ValueError(f"duplicate or empty anchorId in {domain.value}")
        seen.add(anchor_id)
        anchor = _utc(record["anchorEndUtc"], "anchorEndUtc")
        returned = _optional_utc(record["returnObservedAtUtc"])
        if anchor > request.analysis_as_of_utc: continue
        if returned is not None and returned > request.analysis_as_of_utc: returned = None
        if returned is not None and returned <= anchor: raise ValueError("return timestamp must be after anchor")
        rows.append({
            "profile": str(record["canonicalProfileId"]), "anchorId": anchor_id,
            "anchor": anchor, "eligible": _bool(record["anchorEligible"], "anchorEligible"),
            "returned": returned, "classification": str(record["returnClassification"]),
            "coverage": _bool(record["completeSourceCoverage"], "completeSourceCoverage"),
        })
    return rows


def _episode_key(profile: str, anchor: datetime) -> tuple[str, datetime]:
    return profile, anchor


def _domain_state(row: dict[str, Any] | None, request: RetentionEvidenceRequest) -> tuple[str, bool, bool]:
    if row is None or not row["eligible"]: return "Ineligible", False, False
    returned = row["returned"]
    if not request.horizon.enabled:
        return ("ObservedReturn" if returned is not None else "RightCensored"), False, returned is None
    assert request.horizon_days is not None and request.source_upload_grace_hours is not None
    comparison_end = row["anchor"] + timedelta(days=request.horizon_days)
    mature = row["coverage"] and comparison_end + timedelta(hours=request.source_upload_grace_hours) <= request.analysis_as_of_utc
    if returned is not None and returned <= comparison_end: return "ReturnedWithinHorizon", mature, False
    if mature: return "BoundedAbsenceObservation", True, False
    return "RightCensored", False, True


def _uninstall_rows(artifact: LoadedArtifact, request: RetentionEvidenceRequest) -> tuple[list[dict[str, Any]], dict[str, int]]:
    _validate_scope(artifact, request)
    if artifact.artifact_type != "restrictedObservedUninstallEvents": raise ValueError("expected restrictedObservedUninstallEvents")
    events: list[dict[str, Any]] = []
    quality = {"mappedObservedCount": 0, "unmappedObservedCount": 0, "ambiguousObservedCount": 0, "provisionalObservedCount": 0}
    for record in artifact.frame.to_dict("records"):
        timestamp = _optional_utc(record.get("eventTimestampUtc"))
        if timestamp is None or timestamp > request.analysis_as_of_utc: continue
        status = str(record.get("attributionStatus") or "")
        finalization = str(record.get("sourceFinalizationState") or "")
        if finalization != "Final": quality["provisionalObservedCount"] += 1
        if status == "Mapped" and str(record.get("telemetryPlayerId") or ""):
            quality["mappedObservedCount"] += 1
            events.append({"profile": str(record["telemetryPlayerId"]), "timestamp": timestamp, "finalization": finalization})
        elif status == "Ambiguous": quality["ambiguousObservedCount"] += 1
        else: quality["unmappedObservedCount"] += 1
    return events, quality


def _authority(fact: RetentionEvidenceFactKind, row: dict[str, Any], request: RetentionEvidenceRequest) -> dict[str, Any]:
    return resolve_authority(fact, AuthorityContext(
        derived_fixed_horizon=request.horizon.enabled,
        anchor_end_utc=row["anchor"], analysis_as_of_utc=request.analysis_as_of_utc,
        complete_source_coverage=row.get("coverage"), comparison_plan=request.comparison_plan,
    )).to_dict()


def analyze_retention_evidence(
    request: RetentionEvidenceRequest,
    *,
    gameplay_artifact: Path,
    app_artifact: Path,
    uninstall_artifact: Path,
) -> RetentionEvidenceAnalysis:
    gameplay_source = load_restricted_artifact(gameplay_artifact)
    app_source = load_restricted_artifact(app_artifact)
    uninstall_source = load_restricted_artifact(uninstall_artifact)
    artifacts = (gameplay_source, app_source, uninstall_source)
    gameplay = _prepare_returns(gameplay_source, request, ReturnDomain.GAMEPLAY)
    app = _prepare_returns(app_source, request, ReturnDomain.APP)
    uninstall, uninstall_quality = _uninstall_rows(uninstall_source, request)
    by_domain = {ReturnDomain.GAMEPLAY: {_episode_key(r["profile"], r["anchor"]): r for r in gameplay}, ReturnDomain.APP: {_episode_key(r["profile"], r["anchor"]): r for r in app}}
    keys = sorted(set(by_domain[ReturnDomain.GAMEPLAY]) | set(by_domain[ReturnDomain.APP]), key=lambda k: (k[0], k[1]))
    next_anchor: dict[tuple[str, datetime], datetime] = {}
    for index, key in enumerate(keys):
        for later in keys[index + 1:]:
            if later[0] == key[0] and later[1] > key[1]: next_anchor[key] = later[1]; break
    episodes: list[dict[str, Any]] = []
    for key in keys:
        profile, anchor = key
        natural_end = min(next_anchor.get(key, request.analysis_as_of_utc), request.analysis_as_of_utc)
        g, a = by_domain[ReturnDomain.GAMEPLAY].get(key), by_domain[ReturnDomain.APP].get(key)
        g_state, g_mature, g_censored = _domain_state(g, request)
        a_state, a_mature, a_censored = _domain_state(a, request)
        episodes.append({
            "profile": profile, "episodeId": _canonical_digest([profile, _iso(anchor)])[:32],
            "anchor": anchor, "naturalEnd": natural_end,
            "gameplay": g, "app": a, "gameplayState": g_state, "appState": a_state,
            "gameplayMature": g_mature, "appMature": a_mature,
            "gameplayCensored": g_censored, "appCensored": a_censored,
            "uninstalls": [], "facets": set(), "sameTime": False,
        })
    for event in uninstall:
        applicable = [e for e in episodes if e["profile"] == event["profile"] and e["anchor"] < event["timestamp"] and event["timestamp"] <= e["naturalEnd"]]
        if applicable: max(applicable, key=lambda e: e["anchor"])["uninstalls"].append(event)
    sequence_counts: dict[str, int] = {}
    for episode in episodes:
        g_time = episode["gameplay"]["returned"] if episode["gameplay"] else None
        a_time = episode["app"]["returned"] if episode["app"] else None
        uninstall_times = [item["timestamp"] for item in episode["uninstalls"]]
        facets = episode["facets"]
        if a_time and g_time:
            if a_time < g_time: facets.add("AppReturnThenNewAttempt")
            elif a_time == g_time: facets.add("SameObservedTime")
        elif a_time: facets.add("AppReturnOnly")
        elif g_time: facets.add("NewAttemptObservedWithoutAppReturn")
        if uninstall_times:
            facets.add("UninstallObserved")
            for return_time, later_name, earlier_name in ((a_time, "UninstallThenLaterAppReturn", "ReturnThenUninstall"), (g_time, "UninstallThenLaterNewAttempt", "ReturnThenUninstall")):
                if return_time is None: continue
                if any(t == return_time for t in uninstall_times): facets.add("SameObservedTime"); episode["sameTime"] = True
                if any(t < return_time for t in uninstall_times): facets.add(later_name)
                if any(t > return_time for t in uninstall_times): facets.add(earlier_name)
        if episode["gameplayState"] == "BoundedAbsenceObservation": facets.add("BoundedNoGameplayReturn")
        if episode["appState"] == "BoundedAbsenceObservation": facets.add("BoundedNoAppReturn")
        if episode["gameplayCensored"] or episode["appCensored"]: facets.add("RightCensored")
        for facet in facets: sequence_counts[facet] = sequence_counts.get(facet, 0) + 1

    def domain_metrics(domain: ReturnDomain) -> dict[str, Any]:
        prefix = "gameplay" if domain is ReturnDomain.GAMEPLAY else "app"
        rows = [e for e in episodes if e[prefix] is not None]
        eligible = [e for e in rows if e[prefix]["eligible"]]
        mature = [e for e in eligible if e[f"{prefix}Mature"]]
        within = [e for e in eligible if e[f"{prefix}State"] == "ReturnedWithinHorizon"]
        within_mature = [e for e in mature if e[f"{prefix}State"] == "ReturnedWithinHorizon"]
        bounded = [e for e in mature if e[f"{prefix}State"] == "BoundedAbsenceObservation"]
        censored = [e for e in eligible if e[f"{prefix}Censored"]]
        denominator = len(mature) if request.horizon.enabled else None
        fact = RetentionEvidenceFactKind.B7_RETURNED if domain is ReturnDomain.GAMEPLAY else RetentionEvidenceFactKind.B8_RETURNED
        authority_rows = [_authority(fact, e[prefix], request) for e in mature]
        comparison_allowed = bool(authority_rows) and all(v["comparison"]["decision"] == "Allowed" for v in authority_rows)
        reasons = sorted({r for v in authority_rows for r in v["comparison"]["reasons"]})
        if not authority_rows:
            sample = rows[0][prefix] if rows else {"anchor": request.analysis_as_of_utc, "coverage": False}
            resolved = _authority(fact, sample, request)
            reasons = resolved["comparison"]["reasons"]
        return {
            "eligibleAnchorCount": len(eligible), "matureAnchorCount": denominator,
            "returnedWithinHorizonCount": len(within_mature) if request.horizon.enabled else None,
            "returnedWithinHorizonRate": (len(within_mature) / denominator if denominator else None),
            "earlyPositiveObservationCount": len(within) - len(within_mature),
            "boundedAbsenceCount": len(bounded) if request.horizon.enabled else None,
            "rightCensoredCount": len(censored),
            "censoringRate": len(censored) / len(eligible) if eligible else None,
            "ineligibleAnchorCount": len(rows) - len(eligible),
            "authorityResolution": {"comparison": {"decision": "Allowed" if comparison_allowed else "Denied", "reasons": reasons}, "decision": {"decision": "Denied", "reasons": ["DecisionAuthorityDenied"]}, "target": {"decision": "Denied", "reasons": ["TargetAuthorityDenied"]}, "guardrail": {"decision": "Denied", "reasons": ["GuardrailAuthorityDenied"]}, "rollback": {"decision": "Denied", "reasons": ["RollbackAuthorityDenied"]}},
        }
    gameplay_metrics, app_metrics = domain_metrics(ReturnDomain.GAMEPLAY), domain_metrics(ReturnDomain.APP)
    metrics = {
        "gameplayReturn": gameplay_metrics, "appReturn": app_metrics,
        "retentionEvidence": {"episodeCount": len(episodes), "matureAnchorCount": (gameplay_metrics["matureAnchorCount"] or 0) + (app_metrics["matureAnchorCount"] or 0) if request.horizon.enabled else None, "rightCensoredCount": gameplay_metrics["rightCensoredCount"] + app_metrics["rightCensoredCount"]},
        "observedUninstall": uninstall_quality,
    }
    evidence_rows = []
    for name, values in (("gameplayReturn", gameplay_metrics), ("appReturn", app_metrics)):
        for metric in ("returnedWithinHorizonCount", "matureAnchorCount", "returnedWithinHorizonRate"):
            evidence_rows.append({"evidenceDomain": name, "metric": metric, "value": values[metric], "unit": "ratio" if metric.endswith("Rate") else "anchors"})
    restricted_rows = [{
        "canonicalProfileId": e["profile"], "episodeId": e["episodeId"], "anchorEndUtc": _iso(e["anchor"]), "naturalEndUtc": _iso(e["naturalEnd"]),
        "gameplayEligible": None if e["gameplay"] is None else e["gameplay"]["eligible"], "gameplayReturnObservedAtUtc": _iso(e["gameplay"]["returned"]) if e["gameplay"] else None, "gameplayState": e["gameplayState"],
        "appEligible": None if e["app"] is None else e["app"]["eligible"], "appReturnObservedAtUtc": _iso(e["app"]["returned"]) if e["app"] else None, "appState": e["appState"],
        "mappedUninstallCount": len(e["uninstalls"]), "sequenceFacets": "|".join(sorted(e["facets"])), "sameObservedTime": e["sameTime"],
    } for e in episodes]
    tables = {
        "evidence-summary.csv": pd.DataFrame(evidence_rows),
        "sequence-summary.csv": pd.DataFrame([{"sequenceFacet": key, "episodeCount": value} for key, value in sorted(sequence_counts.items())], columns=("sequenceFacet", "episodeCount")),
        "maturity-summary.csv": pd.DataFrame([{"evidenceDomain": name, "eligibleAnchorCount": values["eligibleAnchorCount"], "matureAnchorCount": values["matureAnchorCount"], "boundedAbsenceCount": values["boundedAbsenceCount"]} for name, values in (("gameplayReturn", gameplay_metrics), ("appReturn", app_metrics))]),
        "censoring-summary.csv": pd.DataFrame([{"evidenceDomain": name, "rightCensoredCount": values["rightCensoredCount"], "censoringRate": values["censoringRate"]} for name, values in (("gameplayReturn", gameplay_metrics), ("appReturn", app_metrics))]),
        "uninstall-attribution-quality-summary.csv": pd.DataFrame([{"metric": key, "count": value} for key, value in uninstall_quality.items()]),
        "source-compatibility-summary.csv": pd.DataFrame([{"artifactType": a.artifact_type, "sourceFinalizationState": None if a.source_cut.finalization_state is None else a.source_cut.finalization_state.value, "compatible": True} for a in artifacts]),
    }
    warnings = []
    if not (gameplay_metrics["matureAnchorCount"] or app_metrics["matureAnchorCount"]):
        warnings.append({
            "code": "NO_MATURE_RETURN_ANCHORS",
            "message": "No fixed-horizon mature return anchors are available.",
        })
    if gameplay_metrics["rightCensoredCount"] or app_metrics["rightCensoredCount"]:
        warnings.append({
            "code": "RIGHT_CENSORED_OBSERVATIONS",
            "message": "Right-censored observations limit bounded absence interpretation.",
        })
    if uninstall_quality["provisionalObservedCount"]:
        warnings.append({
            "code": "PROVISIONAL_UNINSTALL_SOURCE",
            "message": "Provisional uninstall observations are factual-only source context.",
        })
    if not sum(
        uninstall_quality[key]
        for key in ("mappedObservedCount", "unmappedObservedCount", "ambiguousObservedCount")
    ):
        warnings.append({
            "code": "NO_LIVE_UNINSTALL_SAMPLE",
            "message": "No finalized app_remove observation is present in this source cut.",
        })
    metadata = {
        "reportContractVersion": REPORT_CONTRACT_VERSION, "analysisType": ANALYSIS_TYPE,
        "analysisVersion": ANALYSIS_VERSION, "policyVersion": POLICY_VERSION,
        "generatedAtUtc": _iso(request.analysis_as_of_utc),
        "scope": request.scope_dict(),
        "sample": {
            "episodes": len(episodes),
            "gameplayEligibleAnchors": gameplay_metrics["eligibleAnchorCount"],
            "appEligibleAnchors": app_metrics["eligibleAnchorCount"],
        },
        "quality": {
            "gameplayRightCensoredCount": gameplay_metrics["rightCensoredCount"],
            "appRightCensoredCount": app_metrics["rightCensoredCount"],
            "mappedObservedUninstallCount": uninstall_quality["mappedObservedCount"],
            "unmappedObservedUninstallCount": uninstall_quality["unmappedObservedCount"],
            "ambiguousObservedUninstallCount": uninstall_quality["ambiguousObservedCount"],
        },
        "warnings": warnings,
        "sourceCuts": [{**a.source_cut.to_dict(), "artifactType": a.artifact_type} for a in artifacts],
        "definitions": {"anchor": "canonical final-run anchor", "comparisonWindowDeletesFactualHistory": False, "rightCensoredMeansNonReturn": False, "observedUninstallMeansPermanentLoss": False, "atRiskUninstallDenominatorDefined": False},
        "c1C2Integrated": True,
    }
    return RetentionEvidenceAnalysis(metrics, metadata, tables, pd.DataFrame(restricted_rows, columns=RESTRICTED_EPISODE_COLUMNS), artifacts)


def _normal_text(analysis: RetentionEvidenceAnalysis) -> dict[str, str]:
    scope = analysis.metadata["scope"]
    horizon = scope["horizonDays"]
    report = (
        "# Retention Evidence\n\n"
        "This report composes factual return and app_remove observations. It does not define churn, retention state, or an uninstall rate.\n\n"
        f"- Horizon: {horizon if horizon is not None else 'not configured'}\n"
        f"- Analysis as of: {scope['analysisAsOfUtc']}\n"
        f"- Episodes: {analysis.metrics['retentionEvidence']['episodeCount']}\n"
        "- Right-censored observations are excluded from bounded-absence denominators.\n"
    )
    result = {"metrics.json": json.dumps(analysis.metrics, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", "metadata.json": json.dumps(analysis.metadata, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", "report.md": report}
    for name, frame in analysis.tables.items(): result[name] = render_csv(frame, columns=tuple(frame.columns), sort_by=tuple(frame.columns[:1]))
    return result


def _assert_normal_privacy(files: Mapping[str, str], analysis: RetentionEvidenceAnalysis) -> None:
    text = "\n".join(files.values())
    for token in RAW_IDENTIFIER_TOKENS:
        if token in text: raise ValueError(f"raw identifier field escaped into normal bundle: {token}")
    for artifact in analysis.source_artifacts:
        for column in ("canonicalProfileId", "telemetryPlayerId", "retentionBridgeId", "userPseudoId", "anchorId"):
            if column in artifact.frame.columns:
                for value in artifact.frame[column].astype(str):
                    if value and len(value) >= 8 and value in text: raise ValueError("raw identifier value escaped into normal bundle")


def _atomic_install(target: Path, files: Mapping[str, str], *, overwrite: bool, restricted: bool = False) -> Path:
    if target.exists() and not overwrite: raise FileExistsError(f"target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent)); backup = None
    try:
        for name, value in files.items(): (temp / name).write_text(value, encoding="utf-8", newline="\n")
        manifest = {"analysisType": ANALYSIS_TYPE, "analysisVersion": ANALYSIS_VERSION, "policyVersion": POLICY_VERSION, "classification": "RestrictedRawIdentity" if restricted else "Aggregate", "files": {name: _sha(temp / name) for name in sorted(files)}}
        (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        if restricted:
            for item in temp.iterdir():
                try: os.chmod(item, 0o600)
                except OSError: pass
        if target.exists(): backup = target.with_name(f".{target.name}.backup-{os.getpid()}"); target.rename(backup)
        temp.rename(target)
        if backup is not None: shutil.rmtree(backup, ignore_errors=True)
        return target
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists(): backup.rename(target)
        raise


def generate_retention_evidence_report(
    request: RetentionEvidenceRequest, *, gameplay_artifact: Path, app_artifact: Path,
    uninstall_artifact: Path, output_root: Path = Path("reports/generated"),
    restricted_output_root: Path = Path("reports/restricted"), overwrite: bool = False,
) -> RetentionEvidenceOutput:
    analysis = analyze_retention_evidence(request, gameplay_artifact=gameplay_artifact, app_artifact=app_artifact, uninstall_artifact=uninstall_artifact)
    normal = _normal_text(analysis); _assert_normal_privacy(normal, analysis)
    snapshot_identity = {
        "scope": request.scope_dict(),
        "sourceCutDigests": [item.manifest_digest for item in analysis.source_artifacts],
    }
    scope_hash = _canonical_digest(snapshot_identity)[:16]
    report_target = output_root / "retention-evidence" / scope_hash
    restricted_target = restricted_output_root / "retention-evidence-episodes" / scope_hash
    restricted_files = {
        "retention-episodes.csv": render_csv(analysis.restricted_episodes, columns=RESTRICTED_EPISODE_COLUMNS, sort_by=("anchorEndUtc", "canonicalProfileId")),
        "metadata.json": json.dumps({"artifactType": "restrictedRetentionEpisodes", "contractVersion": RESTRICTED_CONTRACT_VERSION, "analysisType": ANALYSIS_TYPE, "analysisVersion": ANALYSIS_VERSION, "policyVersion": POLICY_VERSION, "classification": "RestrictedRawIdentity", "scope": request.scope_dict(), "sourceCuts": analysis.metadata["sourceCuts"]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    # Recheck immutable source manifests immediately before installation.
    for artifact in analysis.source_artifacts:
        if _sha(artifact.path / "manifest.json") != artifact.manifest_digest: raise ValueError("source cut changed during execution")
    restricted_path = _atomic_install(restricted_target, restricted_files, overwrite=overwrite, restricted=True)
    try: report_path = _atomic_install(report_target, normal, overwrite=overwrite)
    except Exception:
        shutil.rmtree(restricted_path, ignore_errors=True); raise
    return RetentionEvidenceOutput(report_path, restricted_path, analysis)


def validate_retention_evidence_bundle(path: Path) -> dict[str, Any]:
    manifest, _ = _verify_manifest(path)
    metrics = _read_json(path / "metrics.json"); metadata = _read_json(path / "metadata.json")
    serialized = "\n".join(p.read_text(encoding="utf-8") for p in path.iterdir() if p.is_file())
    leaks = [token for token in RAW_IDENTIFIER_TOKENS if token in serialized]
    forbidden = [token for token in ("churnRate", "retentionHealthScore", "isChurned", "isRetained", "currentUninstallState", "uninstallRate") if token in serialized]
    decisions = []
    for domain in ("gameplayReturn", "appReturn"):
        auth = metrics.get(domain, {}).get("authorityResolution", {})
        decisions.extend(auth.get(name, {}).get("decision") for name in ("decision", "target", "guardrail", "rollback"))
    findings = []
    if metadata.get("analysisType") != ANALYSIS_TYPE or metadata.get("analysisVersion") != ANALYSIS_VERSION: findings.append("analysis identity mismatch")
    if leaks: findings.append("normal raw identifier leak")
    if forbidden: findings.append("forbidden semantic metric or state")
    if any(item == "Allowed" for item in decisions): findings.append("forbidden authority allowed")
    return {"analysisType": ANALYSIS_TYPE, "analysisVersion": ANALYSIS_VERSION, "manifestFiles": len(manifest["files"]), "normalRawIdentifierLeaks": len(leaks), "forbiddenSemanticFields": len(forbidden), "forbiddenAuthorityAllowed": sum(item == "Allowed" for item in decisions), "ready": not findings, "findings": findings}


def load_comparison_plan(path: Path) -> ComparisonPlan:
    """Load the explicit caller-supplied R4-A comparison constraints."""
    value = _read_json(path)
    horizon = value.get("horizon")
    if not isinstance(horizon, dict):
        raise ValueError("comparison plan requires horizon")

    def cut(name: str) -> SourceCut | None:
        raw = value.get(name)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError(f"{name} must be an object or null")
        state = raw.get("finalizationState")
        return SourceCut(
            raw.get("artifactIdentity"), raw.get("sha256Digest"),
            None if state is None else SourceFinalizationState(state),
            _optional_utc(raw.get("analysisAsOfUtc")),
        )

    return ComparisonPlan(
        horizon=HorizonContract(horizon.get("horizonDays"), horizon.get("sourceUploadGraceHours")),
        minimum_mature_anchors_per_cohort=value.get("minimumMatureAnchorsPerCohort"),
        maximum_censoring_rate=value.get("maximumCensoringRate"),
        baseline_mature_anchors=value.get("baselineMatureAnchors"),
        candidate_mature_anchors=value.get("candidateMatureAnchors"),
        baseline_censoring_rate=value.get("baselineCensoringRate"),
        candidate_censoring_rate=value.get("candidateCensoringRate"),
        backend_compatible=value.get("backendCompatible"),
        environment_compatible=value.get("environmentCompatible"),
        content_release_scope_compatible=value.get("contentReleaseScopeCompatible"),
        horizon_compatible=value.get("horizonCompatible"),
        upload_grace_compatible=value.get("uploadGraceCompatible"),
        source_cut_compatible=value.get("sourceCutCompatible"),
        baseline_source_cut=cut("baselineSourceCut"),
        candidate_source_cut=cut("candidateSourceCut"),
    )


__all__ = [
    "ANALYSIS_TYPE", "ANALYSIS_VERSION", "RetentionEvidenceRequest",
    "RetentionEvidenceAnalysis", "RetentionEvidenceOutput", "ReturnDomain",
    "write_return_source_artifact", "load_restricted_artifact",
    "analyze_retention_evidence", "generate_retention_evidence_report",
    "validate_retention_evidence_bundle",
    "load_comparison_plan",
]
