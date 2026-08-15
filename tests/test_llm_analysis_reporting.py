from __future__ import annotations

import json
from pathlib import Path

from defence_project_analytics.llm_analysis import (
    ScriptedAnalysisProvider,
    run_analysis_with_provider,
)
from defence_project_analytics.llm_analysis.api import generate_validated_analysis
from defence_project_analytics.llm_analysis.writer import write_analysis_prompt
from llm_analysis_fixtures import make_package, valid_response


def test_renderer_uses_evidence_values_and_writes_four_safe_files(tmp_path: Path) -> None:
    source, package = make_package(tmp_path)
    request_path = write_analysis_prompt(package, output_root=tmp_path / "out")
    response = valid_response(package)
    path = generate_validated_analysis(
        analysis_request_path=request_path,
        source_brief_path=source,
        response=response,
        output_root=tmp_path / "out",
    )
    assert {item.name for item in path.iterdir()} == {
        "analysis.md", "analysis.json", "prompt.md", "manifest.json"
    }
    markdown = (path / "analysis.md").read_text(encoding="utf-8")
    evidence_id = next(iter(package.source.evidence_by_id))
    assert evidence_id in markdown
    assert "finalAttempts: 10 attempts." in markdown
    assert "ReadyForControlledChange" not in markdown
    assert "NotIdentifiedInSuppliedBrief는 실제 상충 증거의 부재를 의미하지 않습니다" in markdown
    assert "production 변경이나 자동 수정·배포를 승인하지 않습니다" in markdown
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["providerCallCount"] == 0
    assert manifest["automaticDeploymentAuthorized"] is False


def test_scripted_provider_is_provider_neutral_and_calls_no_network(tmp_path: Path) -> None:
    source, package = make_package(tmp_path)
    provider = ScriptedAnalysisProvider(
        valid_response(package), expected_prompt_digest=package.prompt_digest
    )
    path = run_analysis_with_provider(
        package.request,
        provider,
        output_root=tmp_path / "provider-out",
        workspace_root=tmp_path,
    )
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["providerMode"] == "programmaticProtocol"
    assert manifest["providerCallCount"] == 1
