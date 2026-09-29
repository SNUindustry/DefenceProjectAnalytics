"""Atomic writers for C-2 request and validated analysis artifacts."""

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

from defence_project_analytics.brief.loader import FORBIDDEN_RAW_IDENTIFIERS
from defence_project_analytics.brief.registry import HISTORICAL_ONLY_WARNING_CODES
from defence_project_analytics.llm_analysis.loader import verify_source_brief_unchanged
from defence_project_analytics.llm_analysis.policy import forbidden_private_text
from defence_project_analytics.llm_analysis.models import (
    ANALYSIS_POLICY_VERSION,
    ANALYSIS_VERSION,
    PROMPT_TEMPLATE_VERSION,
    RESPONSE_CONTRACT_VERSION,
    AnalysisPromptPackage,
    ValidatedAnalysis,
)
from defence_project_analytics.llm_analysis.renderers import (
    render_analysis_json,
    render_analysis_markdown,
)
from defence_project_analytics.reporting.renderers import render_json, to_external
from defence_project_analytics.metric_registry import (
    METRIC_REGISTRY_VERSION,
    EvidenceUse,
    is_evidence_item_eligible,
)


_SLUG = re.compile(r"[^A-Za-z0-9-]+")
_SAFE_PROVIDER_METADATA = frozenset({
    "transportMode",
    "providerRequestDigest",
    "structuredOutputsUsed",
    "anthropicStrictToolsUsed",
    "structuredOutputSchemaMode",
    "responseContractSchemaDigest",
    "anthropicWireSchemaDigest",
    "anthropicPreflightWireSchemaDigest",
    "anthropicGenerationWireSchemaDigest",
    "anthropicInputProjectionVersion",
    "anthropicEvidenceAliasVersion",
    "evidenceAliasCount",
    "evidenceAliasDigest",
    "anthropicMetricAliasVersion",
    "metricAliasCount",
    "metricAliasDigest",
    "warningAuthorityDigest",
    "anthropicWarningAliasVersion",
    "warningAliasCount",
    "warningAliasDigest",
    "anthropicFlatResponseVersion",
    "anthropicSerializedEnvelopeVersion",
    "anthropicStrictToolTransportVersion",
    "anthropicThreeStageStrictToolTransportVersion",
    "anthropicThreeStageStrictToolVersion",
    "anthropicStageContextVersion",
    "anthropicStageContextVersions",
    "anthropicOutputRefVersion",
    "anthropicStageBIdentityVersion",
    "evidenceGapRefCount",
    "evidenceGapRefDigest",
    "observationRefCount",
    "observationRefDigest",
    "hypothesisRefCount",
    "hypothesisRefDigest",
    "changeCandidateRefCount",
    "changeCandidateRefDigest",
    "anthropicValidationPlanRefVersion",
    "validationPlanRefCount",
    "validationPlanRefDigest",
    "anthropicToolsDigest",
    "anthropicPreflightToolsDigest",
    "anthropicGenerationToolsDigest",
    "anthropicToolChoiceDigest",
    "anthropicPreflightRequestDigest",
    "anthropicGenerationRequestDigest",
    "anthropicStrictToolsProfile",
    "anthropicExpectedToolCount",
    "anthropicObservedToolCount",
    "anthropicIgnoredTextBlockCount",
    "compactPayloadDigest",
    "canonicalEvidenceDigest",
    "compactEvidenceCount",
    "inputTokenCount",
    "totalPreflightInputTokens",
    "actualInputTokens",
    "actualOutputTokens",
    "providerTokenCountCallCount",
    "providerGenerationCallCount",
    "providerErrorCount",
    "providerRetryCount",
    "configuredMaxTransportRetries",
    "stageCount",
    "stages",
    "canonicalValidationPassed",
})


def _slug(value: str | None, fallback: str) -> str:
    return _SLUG.sub("-", value or "").strip("-") or fallback


def source_scope_id(package: AnalysisPromptPackage) -> str:
    identity = package.source.identity
    environment = _slug(identity.environment, "environment")
    stage = _slug(identity.stage_key, "all-stages")
    if identity.mode == "contentVersionCompare":
        return (
            f"compare__{environment}__{stage}__cv-{identity.baseline_content_version}"
            f"-vs-cv-{identity.candidate_content_version}"
        )
    return f"single__{environment}__{stage}__cv-{identity.content_version}"


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("C-2 writer clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _assert_private_fields_absent(files: Mapping[str, str]) -> None:
    pattern = re.compile(
        r'"(' + "|".join(re.escape(item) for item in sorted(FORBIDDEN_RAW_IDENTIFIERS)) + r')"\s*:'
    )
    secret = re.compile(r"\b(api[_-]?key|authorization|bearer\s+[A-Za-z0-9._-]+)\b", re.IGNORECASE)
    for filename, content in files.items():
        match = pattern.search(content) or secret.search(content)
        if match:
            raise ValueError(f"C-2 artifact {filename} contains forbidden private content")
        if forbidden_private_text(content):
            raise ValueError(f"C-2 artifact {filename} contains forbidden private text")


def _install(
    target: Path,
    files: Mapping[str, str],
    *,
    overwrite: bool,
    same_target: Callable[[Path], bool],
    final_check: Callable[[], None] | None = None,
) -> Path:
    if target.exists() and (not overwrite or not same_target(target)):
        raise FileExistsError(f"C-2 artifact target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    backup: Path | None = None
    installed = False
    try:
        for filename, content in files.items():
            (temporary / filename).write_text(content, encoding="utf-8", newline="\n")
        if target.exists():
            backup = target.with_name(f".{target.name}.backup-{os.getpid()}")
            if backup.exists():
                raise FileExistsError(f"Atomic backup already exists: {backup}")
            target.rename(backup)
        temporary.rename(target)
        installed = True
        if final_check is not None:
            final_check()
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return target
    except Exception:
        if installed and target.exists():
            target.rename(temporary)
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise


def write_analysis_prompt(
    package: AnalysisPromptPackage,
    *,
    output_root: Path,
    overwrite: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(microsecond=0),
) -> Path:
    verify_source_brief_unchanged(package.source)
    target = output_root / "analysis-requests" / f"{source_scope_id(package)}__{package.request_id}"
    generated_at = _clock_value(clock)
    manifest = {
        "analysisVersion": ANALYSIS_VERSION,
        "analysisPolicyVersion": ANALYSIS_POLICY_VERSION,
        "promptTemplateVersion": PROMPT_TEMPLATE_VERSION,
        "responseContractVersion": RESPONSE_CONTRACT_VERSION,
        "metricRegistryVersion": METRIC_REGISTRY_VERSION,
        "generatedAtUtc": generated_at,
        "requestId": package.request_id,
        "requestDigest": package.request_digest,
        "promptDigest": package.prompt_digest,
        "sourceBriefIdentity": package.source.identity,
        "sourceBriefPortablePath": package.source.portable_path,
        "sourceBriefPortablePathAvailable": package.source.portable_path is not None,
        "sourceArtifacts": package.source.artifacts,
        "analysisObjective": package.request.analysis_objective,
        "outputLanguage": package.request.output_language,
        "promptCharacterCount": len(package.prompt),
        "evidenceCount": sum(
            is_evidence_item_eligible(item, EvidenceUse.FACTUAL_REFERENCE)
            for item in package.source.evidence_by_id.values()
        ),
        "providerCalls": 0,
        "cloudAccessPerformed": False,
        "sourceAnalyzersExecuted": False,
        "sourceMutationRecheck": {"passed": True},
    }
    files = {
        "prompt.md": package.prompt.rstrip() + "\n",
        "request.json": render_json(package.request_payload),
        "manifest.json": render_json(manifest),
    }
    _assert_private_fields_absent(files)
    verify_source_brief_unchanged(package.source)
    result = _install(
        target,
        files,
        overwrite=overwrite,
        same_target=lambda path: _same_manifest(
            path, "requestDigest", package.request_digest
        ),
        final_check=lambda: verify_source_brief_unchanged(package.source),
    )
    return result


def _same_manifest(path: Path, key: str, expected: str) -> bool:
    try:
        value = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return value.get(key) == expected


def analysis_execution_id(
    package: AnalysisPromptPackage,
    analysis: ValidatedAnalysis,
    *,
    provider_mode: str,
    provider_name: str | None,
    model_name: str | None,
) -> str:
    value = json.dumps(
        {
            "promptDigest": package.prompt_digest,
            "providerMode": provider_mode,
            "providerName": provider_name,
            "modelName": model_name,
            "normalizedAnalysisDigest": analysis.normalized_analysis_digest,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "AX-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def write_validated_analysis(
    package: AnalysisPromptPackage,
    analysis: ValidatedAnalysis,
    *,
    response_input_digest: str,
    output_root: Path,
    provider_mode: str = "manualExternal",
    provider_name: str | None = None,
    model_name: str | None = None,
    provider_call_count: int = 0,
    provider_metadata: Mapping[str, Any] | None = None,
    overwrite: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(microsecond=0),
) -> Path:
    verify_source_brief_unchanged(package.source)
    execution_id = analysis_execution_id(
        package,
        analysis,
        provider_mode=provider_mode,
        provider_name=provider_name,
        model_name=model_name,
    )
    target = output_root / "llm-analysis" / f"{source_scope_id(package)}__{execution_id}"
    warning_codes = tuple(
        str(item.get("code"))
        for item in package.source.brief.get("criticalWarnings", [])
        if isinstance(item, dict) and item.get("code")
        and item.get("code") not in HISTORICAL_ONLY_WARNING_CODES
    )
    markdown = render_analysis_markdown(
        analysis,
        package.source.evidence_by_id,
        source_overall_status=str(package.source.brief["overallStatus"]),
        source_warning_codes=warning_codes,
        analysis_objective=package.request.analysis_objective,
        output_language=package.request.output_language,
    )
    generated_at = _clock_value(clock)
    source_bundles = [
        {
            key: item.get(key)
            for key in (
                "sourceBundleId", "analysisType", "domain", "bundleName",
                "portablePath", "portablePathAvailable", "bundleDigest",
            )
        }
        for item in package.source.manifest.get("sourceBundles", ())
        if isinstance(item, Mapping)
    ]
    manifest = {
        "analysisVersion": ANALYSIS_VERSION,
        "analysisPolicyVersion": ANALYSIS_POLICY_VERSION,
        "promptTemplateVersion": PROMPT_TEMPLATE_VERSION,
        "responseContractVersion": RESPONSE_CONTRACT_VERSION,
        "metricRegistryVersion": METRIC_REGISTRY_VERSION,
        "generatedAtUtc": generated_at,
        "analysisExecutionId": execution_id,
        "sourceBriefIdentity": package.source.identity,
        "sourceBriefPortablePath": package.source.portable_path,
        "sourceBundles": source_bundles,
        "sourceArtifacts": package.source.artifacts,
        "requestId": package.request_id,
        "requestDigest": package.request_digest,
        "promptDigest": package.prompt_digest,
        "responseInputDigest": response_input_digest,
        "normalizedAnalysisDigest": analysis.normalized_analysis_digest,
        "providerMode": provider_mode,
        "providerName": provider_name,
        "modelName": model_name,
        "providerCallCount": provider_call_count,
        "validationStatus": "Valid",
        "observationCitationCoverage": analysis.observation_citation_coverage,
        "hypothesisSupportCoverage": analysis.hypothesis_support_coverage,
        "changeCandidateEvidenceCoverage": analysis.change_candidate_evidence_coverage,
        "actionableCandidateValidationCoverage": analysis.actionable_candidate_validation_coverage,
        "invalidEvidenceReferenceCount": analysis.invalid_evidence_reference_count,
        "privacyScan": {"passed": True, "forbiddenIdentifiersFound": ()},
        "languageScan": {"passed": True, "unsupportedCausalPhrases": ()},
        "sourceMutationRecheck": {"passed": True},
        "humanReviewRequired": True,
        "automaticModificationAuthorized": False,
        "automaticDeploymentAuthorized": False,
        "bigQueryEstimatedBytes": 0,
        "cloudAccessPerformed": provider_mode == "anthropicApi",
        "sourceAnalyzersExecuted": False,
    }
    if provider_metadata:
        manifest.update({
            key: provider_metadata[key]
            for key in sorted(_SAFE_PROVIDER_METADATA)
            if key in provider_metadata
        })
    files = {
        "analysis.md": markdown,
        "analysis.json": render_analysis_json(analysis),
        "prompt.md": package.prompt.rstrip() + "\n",
        "manifest.json": render_json(manifest),
    }
    _assert_private_fields_absent(files)
    verify_source_brief_unchanged(package.source)
    result = _install(
        target,
        files,
        overwrite=overwrite,
        same_target=lambda path: _same_manifest(
            path, "normalizedAnalysisDigest", analysis.normalized_analysis_digest
        ),
        final_check=lambda: verify_source_brief_unchanged(package.source),
    )
    return result
