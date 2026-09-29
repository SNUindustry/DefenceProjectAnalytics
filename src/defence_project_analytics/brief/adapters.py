"""Explicit source adapters and stable Evidence ID assignment."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from defence_project_analytics.brief.errors import (
    DuplicateCanonicalEvidenceIdentityError,
    EvidenceHashCollisionError,
)
from defence_project_analytics.brief.models import (
    COMPARISON_MODE,
    EvidenceCandidate,
    EvidenceItem,
    EvidenceProvenance,
    LoadedSourceBundle,
)
from defence_project_analytics.brief.registry import (
    COMPARISON_TABLES,
    active_source_warning_codes,
    is_evidence_metric_eligible,
    required_tables,
    SUMMARY_METRICS,
    TABLE_EVIDENCE,
)
from defence_project_analytics.metric_registry import metric_authority


_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.casefold()).strip("-") or "metric"


def _value(mapping: Mapping[str, Any], path: str) -> Any:
    current: Any = mapping
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _clean(value: Any) -> Any:
    if value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _warnings(bundle: LoadedSourceBundle) -> tuple[str, ...]:
    return active_source_warning_codes(bundle.metadata.get("warnings", ()))


def _artifact_hash(bundle: LoadedSourceBundle, relative_path: str) -> str:
    return bundle.artifacts[relative_path].sha256


def _bundle_id(bundle: LoadedSourceBundle) -> str:
    return f"{bundle.analysis_type}:{bundle.bundle_digest[:12]}"


def _provenance(
    bundle: LoadedSourceBundle,
    artifact: str,
    row_key: Mapping[str, Any],
) -> EvidenceProvenance:
    return EvidenceProvenance(
        source_bundle_id=_bundle_id(bundle),
        source_bundle_digest=bundle.bundle_digest,
        source_artifact=artifact,
        source_artifact_sha256=_artifact_hash(bundle, artifact),
        source_row_key={key: _clean(value) for key, value in row_key.items()},
    )


def _single_value(value_type: str, raw: Any) -> tuple[Mapping[str, Any], bool]:
    if value_type == "ratio":
        if not isinstance(raw, Mapping):
            return {"count": None, "denominator": None, "ratio": None}, False
        count = _clean(raw.get("count"))
        denominator = _clean(raw.get("denominator"))
        ratio = _clean(raw.get("ratio"))
        return {"count": count, "denominator": denominator, "ratio": ratio}, (
            count is not None and denominator is not None
        )
    if value_type == "distribution":
        if not isinstance(raw, Mapping):
            return {"observedCount": None, "median": None}, False
        value = {key: _clean(item) for key, item in raw.items()}
        return value, bool(value.get("observedCount"))
    clean = _clean(raw)
    return {"value": clean}, clean is not None


def _retention_authority(
    bundle: LoadedSourceBundle,
    family: str,
    metric: str,
) -> Mapping[str, Any]:
    if bundle.analysis_type != "retentionEvidence":
        return {}
    static = metric_authority((bundle.domain, family, metric))
    resolution = bundle.metrics.get(family, {}).get("authorityResolution", {})
    runtime_raw = resolution.get("comparison", {}) if isinstance(resolution, Mapping) else {}
    runtime_decision = runtime_raw.get("decision")
    if runtime_decision not in {"Allowed", "Denied"}:
        runtime_decision = "Denied"
    reasons = runtime_raw.get("reasons")
    if not isinstance(reasons, list):
        reasons = ["MissingRuntimeComparisonAuthority"]
    evidence_class = (
        "DirectBehaviorObservation"
        if family == "gameplayReturn" and metric != "matureAnchorCount"
        else "DirectLifecycleObservation"
        if family == "appReturn" and metric != "matureAnchorCount"
        else "BoundedAbsenceObservation"
        if family in {"gameplayReturn", "appReturn"}
        else "CensoredObservation"
        if family == "retentionEvidence" and metric == "rightCensoredCount"
        else "DirectRemovalObservation"
        if family == "observedUninstall" and metric == "mappedObservedCount"
        else "SourceQualityObservation"
        if family == "observedUninstall"
        else "MaturityObservation"
    )
    scope = bundle.metadata.get("scope", {})
    source_cuts = []
    for raw in bundle.metadata.get("sourceCuts", ()):
        if not isinstance(raw, Mapping):
            continue
        source_cuts.append({
            "artifactType": raw.get("artifactType"),
            "sha256Digest": raw.get("sha256Digest"),
            "finalizationState": raw.get("finalizationState"),
            "analysisAsOfUtc": raw.get("analysisAsOfUtc"),
        })
    finalization_states = tuple(sorted({
        str(item.get("finalizationState"))
        for item in source_cuts if item.get("finalizationState") is not None
    }))
    comparison_allowed = bool(
        static.comparison_eligible and runtime_decision == "Allowed"
    )
    return {
        "evidenceClass": evidence_class,
        "subjectLevel": (
            "Anchor" if family in {"gameplayReturn", "appReturn", "retentionEvidence"}
            else "ObservedEvent"
        ),
        "factualEligible": bool(static.evidence_eligible),
        "comparisonEligible": bool(static.comparison_eligible),
        "runtimeComparison": {
            "decision": runtime_decision,
            "reasons": tuple(str(item) for item in reasons),
        },
        "decisionEligible": False,
        "targetEligible": False,
        "guardrailEligible": False,
        "rollbackEligible": False,
        "monitorOnlyEligible": comparison_allowed,
        "analysisAsOfUtc": scope.get("analysisAsOfUtc"),
        "horizon": {
            "horizonDays": scope.get("horizonDays"),
            "sourceUploadGraceHours": scope.get("sourceUploadGraceHours"),
        },
        "sourceFinalizationStates": finalization_states,
        "sourceFinalizationState": (
            finalization_states[0]
            if len(finalization_states) == 1
            else "Mixed" if finalization_states else "Unknown"
        ),
        "sourceCuts": tuple(source_cuts),
        "denominatorSemantics": (
            "mature anchors within the fixed horizon"
            if family in {"gameplayReturn", "appReturn"}
            else "source-defined factual observation count"
        ),
        "maturityState": (
            "NotApplicable"
            if family == "observedUninstall"
            else "FixedHorizonMatured"
            if (
                scope.get("horizonDays") is not None
                and bool(bundle.metrics.get(family, {}).get("matureAnchorCount"))
            )
            else "NoMatureAnchors"
            if scope.get("horizonDays") is not None
            else "NoFixedHorizon"
        ),
        "sourceQuality": (
            {
                "provisionalObservedCount": bundle.metrics
                .get("observedUninstall", {})
                .get("provisionalObservedCount"),
            }
            if family == "observedUninstall"
            else {}
        ),
    }


def _table_value(metric: Any, row: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    value = _clean(row.get(metric.value_column))
    if metric.value_type in {"ratio", "derivedClearRatio"}:
        count = _clean(row.get(metric.count_column or ""))
        denominator_component = _clean(row.get(metric.denominator_column or ""))
        if metric.value_type == "derivedClearRatio":
            denominator = (
                None
                if count is None or denominator_component is None
                else int(count) + int(denominator_component)
            )
        else:
            denominator = denominator_component
        return {
            "count": count,
            "denominator": denominator,
            "ratio": value,
        }, count is not None and denominator is not None
    return {"value": value}, value is not None


def adapt_single_bundle(bundle: LoadedSourceBundle, mode: str) -> list[EvidenceCandidate]:
    warnings = _warnings(bundle)
    sample = dict(bundle.metadata.get("sample", {}))
    candidates: list[EvidenceCandidate] = []
    for registry_index, spec in enumerate(SUMMARY_METRICS[bundle.analysis_type]):
        if not is_evidence_metric_eligible(bundle.domain, spec.family, spec.metric):
            continue
        value, observed = _single_value(spec.value_type, _value(bundle.metrics, spec.path))
        if (
            bundle.analysis_type == "retentionEvidence"
            and spec.family in {"gameplayReturn", "appReturn"}
            and spec.metric == "matureAnchorCount"
        ):
            value = {
                **value,
                "boundedAbsenceCount": bundle.metrics
                .get(spec.family, {})
                .get("boundedAbsenceCount"),
            }
        status = "Unavailable" if not observed else ("Limited" if warnings else "Comparable")
        candidates.append(EvidenceCandidate(
            mode=mode,
            source_analysis_type=bundle.analysis_type,
            domain=bundle.domain,
            metric_family=spec.family,
            metric=spec.metric,
            entity_type=None,
            entity_key=None,
            dimension=None,
            dimension_value=None,
            value_type=spec.value_type,
            unit=spec.unit,
            observation_unit=spec.observation_unit,
            value=value,
            observed=observed,
            status=status,
            warning_codes=warnings,
            sample=sample,
            provenance=_provenance(
                bundle, "metrics.json", {"metricsPath": spec.path}
            ),
            priority=(20 + registry_index if spec.core else 30 + registry_index),
            core=spec.core,
            authority=_retention_authority(bundle, spec.family, spec.metric),
        ))
    source_tables = required_tables(bundle.analysis_type, bundle.metadata["analysisVersion"])
    for filename, table_spec in TABLE_EVIDENCE[bundle.analysis_type].items():
        if filename not in source_tables:
            continue
        artifact = f"tables/{filename}"
        frame = bundle.tables[filename]
        for raw_row in frame.to_dict(orient="records"):
            row = {key: _clean(value) for key, value in raw_row.items()}
            row_key = {key: row.get(key) for key in table_spec.keys}
            entity_key = "|".join(
                "" if row.get(key) is None else str(row.get(key))
                for key in table_spec.keys
            )
            dimension = table_spec.keys[-1] if len(table_spec.keys) > 1 else None
            dimension_value = (
                None if dimension is None else str(row.get(dimension) or "")
            )
            for metric in table_spec.metrics:
                if not is_evidence_metric_eligible(
                    bundle.domain, table_spec.family, metric.metric
                ):
                    continue
                value, observed = _table_value(metric, row)
                status = "Unavailable" if not observed else ("Limited" if warnings else "Comparable")
                candidates.append(EvidenceCandidate(
                    mode=mode,
                    source_analysis_type=bundle.analysis_type,
                    domain=bundle.domain,
                    metric_family=table_spec.family,
                    metric=metric.metric,
                    entity_type=table_spec.entity_type,
                    entity_key=entity_key,
                    dimension=dimension,
                    dimension_value=dimension_value,
                    value_type=("ratio" if metric.value_type == "derivedClearRatio" else metric.value_type),
                    unit=metric.unit,
                    observation_unit=table_spec.observation_unit,
                    value=value,
                    observed=observed,
                    status=status,
                    warning_codes=warnings,
                    sample=sample,
                    provenance=_provenance(bundle, artifact, row_key),
                    priority=40 if metric.core or not warnings else 50,
                    core=metric.core,
                ))
    return candidates


def _comparison_warning_codes(value: Any) -> tuple[str, ...]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ()
    if isinstance(value, str):
        return tuple(item for item in value.split("|") if item)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.casefold() == "true"
    return bool(value)


def adapt_comparison_bundle(bundle: LoadedSourceBundle) -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []
    for domain, filename in COMPARISON_TABLES.items():
        payload = bundle.metrics.get(domain)
        if not isinstance(payload, Mapping):
            continue
        sample = {
            "baseline": payload.get("baselineSample"),
            "candidate": payload.get("candidateSample"),
        }
        top_keys = {
            (str(item.get("metric") or ""), str(item.get("entityKey") or ""))
            for item in payload.get("topChanges", ())
            if isinstance(item, Mapping)
        }
        frame = bundle.tables[filename]
        artifact = f"tables/{filename}"
        for raw_row in frame.to_dict(orient="records"):
            row = {key: _clean(value) for key, value in raw_row.items()}
            metric = str(row.get("metric") or "")
            metric_family = str(row.get("metricFamily") or "")
            if not is_evidence_metric_eligible(domain, metric_family, metric):
                continue
            entity_key = None if row.get("entityKey") is None else str(row["entityKey"])
            top = (metric, entity_key or "") in top_keys
            value = {
                key: row.get(key)
                for key in (
                    "baselineObserved", "candidateObserved", "baselineCount",
                    "baselineDenominator", "baselineValue", "candidateCount",
                    "candidateDenominator", "candidateValue", "absoluteDelta",
                    "percentagePointDelta", "relativeDelta", "direction",
                )
            }
            observed = _as_bool(row.get("baselineObserved")) or _as_bool(row.get("candidateObserved"))
            warnings = _comparison_warning_codes(row.get("warningCodes"))
            status = str(row.get("status") or "Unavailable")
            core_order = {
                item.metric: index
                for index, item in enumerate(SUMMARY_METRICS.get(domain, ()))
                if item.core
            }
            core = row.get("entityType") is None and metric in core_order
            candidates.append(EvidenceCandidate(
                mode=COMPARISON_MODE,
                source_analysis_type=bundle.analysis_type,
                domain=domain,
                metric_family=metric_family,
                metric=metric,
                entity_type=(None if row.get("entityType") is None else str(row.get("entityType"))),
                entity_key=entity_key,
                dimension=(None if row.get("dimension") is None else str(row.get("dimension"))),
                dimension_value=(None if row.get("dimensionValue") is None else str(row.get("dimensionValue"))),
                value_type=str(row.get("valueType") or "scalar"),
                unit=str(row.get("unit") or "value"),
                observation_unit=str(row.get("observationUnit") or "sourceDefined"),
                value=value,
                observed=observed,
                status=status,
                warning_codes=warnings,
                sample=sample,
                provenance=_provenance(bundle, artifact, {
                    "domain": domain,
                    "metricFamily": row.get("metricFamily"),
                    "metric": metric,
                    "entityKey": entity_key,
                    "dimensionValue": row.get("dimensionValue"),
                }),
                priority=(
                    10 if top else
                    (20 + core_order[metric] if core else (50 if status != "Comparable" else 40))
                ),
                core=core,
                source_designated=top,
            ))
    return candidates


def assign_evidence_ids(
    candidates: Iterable[EvidenceCandidate],
    *,
    hash_function: Callable[[bytes], Any] = hashlib.sha256,
) -> tuple[EvidenceItem, ...]:
    ordered = list(candidates)
    identities: dict[tuple[str, ...], EvidenceCandidate] = {}
    for candidate in ordered:
        identity = candidate.canonical_identity
        if identity in identities:
            raise DuplicateCanonicalEvidenceIdentityError(
                "Duplicate canonical evidence identity emitted by adapter: "
                + " | ".join(identity)
            )
        identities[identity] = candidate

    short_hashes: dict[str, tuple[str, ...]] = {}
    result: list[EvidenceItem] = []
    for candidate in ordered:
        identity = candidate.canonical_identity
        canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        short_hash = hash_function(canonical.encode("utf-8")).hexdigest()[:12]
        previous = short_hashes.get(short_hash)
        if previous is not None and previous != identity:
            raise EvidenceHashCollisionError(
                f"Evidence short-hash collision {short_hash} for distinct identities"
            )
        short_hashes[short_hash] = identity
        evidence_id = (
            f"EV-{_slug(candidate.domain)}-{_slug(candidate.metric)}-{short_hash}"
        )
        result.append(EvidenceItem(
            evidence_id=evidence_id,
            canonical_identity=identity,
            source_analysis_type=candidate.source_analysis_type,
            domain=candidate.domain,
            metric_family=candidate.metric_family,
            metric=candidate.metric,
            entity_type=candidate.entity_type,
            entity_key=candidate.entity_key,
            dimension=candidate.dimension,
            dimension_value=candidate.dimension_value,
            value_type=candidate.value_type,
            unit=candidate.unit,
            observation_unit=candidate.observation_unit,
            value=candidate.value,
            status=candidate.status,
            warning_codes=candidate.warning_codes,
            sample=candidate.sample,
            priority=candidate.priority,
            core=candidate.core,
            source_designated=candidate.source_designated,
            authority=candidate.authority,
            provenance=candidate.provenance,
        ))
    return tuple(result)
