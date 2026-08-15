"""Public orchestration APIs for Phase C-2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from defence_project_analytics.llm_analysis.models import (
    AnalysisPromptPackage,
    AnalysisPromptRequest,
    AnalysisProvider,
    ValidatedAnalysis,
)
from defence_project_analytics.llm_analysis.prompting import (
    build_analysis_prompt,
    load_analysis_prompt_package,
)
from defence_project_analytics.llm_analysis.validator import validate_response
from defence_project_analytics.llm_analysis.writer import (
    write_analysis_prompt,
    write_validated_analysis,
)


def _response_digest(response: Mapping[str, Any] | Path | str) -> str:
    if isinstance(response, Path):
        return hashlib.sha256(response.read_bytes()).hexdigest()
    if isinstance(response, str):
        return hashlib.sha256(response.encode("utf-8")).hexdigest()
    canonical = json.dumps(
        response,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_analysis_prompt(
    request: AnalysisPromptRequest,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    workspace_root: Path | None = None,
) -> Path:
    package = build_analysis_prompt(request, workspace_root=workspace_root)
    return write_analysis_prompt(package, output_root=output_root, overwrite=overwrite)


def validate_analysis_response(
    *,
    analysis_request_path: Path,
    response: Mapping[str, Any] | Path | str,
    source_brief_path: Path | None = None,
    workspace_root: Path | None = None,
) -> ValidatedAnalysis:
    package = load_analysis_prompt_package(
        analysis_request_path,
        source_brief_path=source_brief_path,
        workspace_root=workspace_root,
    )
    return validate_response(package, response)


def generate_validated_analysis(
    *,
    analysis_request_path: Path,
    response: Mapping[str, Any] | Path | str,
    source_brief_path: Path | None = None,
    output_root: Path = Path("reports/generated"),
    provider_name: str | None = "external",
    model_name: str | None = None,
    overwrite: bool = False,
    workspace_root: Path | None = None,
) -> Path:
    package = load_analysis_prompt_package(
        analysis_request_path,
        source_brief_path=source_brief_path,
        workspace_root=workspace_root,
    )
    analysis = validate_response(package, response)
    return write_validated_analysis(
        package,
        analysis,
        response_input_digest=_response_digest(response),
        output_root=output_root,
        provider_mode="manualExternal",
        provider_name=provider_name,
        model_name=model_name,
        provider_call_count=0,
        overwrite=overwrite,
    )


def run_analysis_with_provider(
    request: AnalysisPromptRequest,
    provider: AnalysisProvider,
    *,
    output_root: Path = Path("reports/generated"),
    overwrite: bool = False,
    workspace_root: Path | None = None,
) -> Path:
    package = build_analysis_prompt(request, workspace_root=workspace_root)
    response = provider.generate(package)
    analysis = validate_response(package, response)
    provider_metadata = getattr(provider, "last_run_metadata", None)
    if not isinstance(provider_metadata, Mapping):
        provider_metadata = {}
    provider_mode = str(provider_metadata.get("providerMode", "programmaticProtocol"))
    provider_call_count = int(provider_metadata.get("providerCallCount", 1))
    return write_validated_analysis(
        package,
        analysis,
        response_input_digest=_response_digest(response),
        output_root=output_root,
        provider_mode=provider_mode,
        provider_name=provider.provider_name,
        model_name=provider.model_name,
        provider_call_count=provider_call_count,
        provider_metadata=provider_metadata,
        overwrite=overwrite,
    )


__all__ = [
    "AnalysisPromptPackage",
    "AnalysisPromptRequest",
    "ValidatedAnalysis",
    "build_analysis_prompt",
    "generate_analysis_prompt",
    "validate_analysis_response",
    "generate_validated_analysis",
    "run_analysis_with_provider",
]
