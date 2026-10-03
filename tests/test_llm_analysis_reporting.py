from __future__ import annotations

import json
from pathlib import Path

import pytest

from defence_project_analytics.llm_analysis import (
    ScriptedAnalysisProvider,
    run_analysis_with_provider,
)
from defence_project_analytics.llm_analysis.api import generate_validated_analysis
from defence_project_analytics.llm_analysis import writer as analysis_writer
from defence_project_analytics.llm_analysis.validator import validate_response
from defence_project_analytics.llm_analysis.writer import (
    _install,
    write_analysis_prompt,
    write_validated_analysis,
)
from llm_analysis_fixtures import make_package, valid_response


def test_renderer_uses_evidence_values_and_writes_four_safe_files(tmp_path: Path) -> None:
    source, package = make_package(tmp_path)
    request_path = write_analysis_prompt(package, output_root=tmp_path / "out")
    response = valid_response(package)
    response["changeCandidates"][0]["risks"] = [
        "생존성이 악화될 수 있다.",
        "빌드 다양성이 줄어들 가능성이 있다.",
    ]
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
    assert "- Risks:" in markdown
    assert "  - 생존성이 악화될 수 있다." in markdown
    assert "  - 빌드 다양성이 줄어들 가능성이 있다." in markdown
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["analysisVersion"] == "1.0.0"
    assert manifest["analysisPolicyVersion"] == "1.6.0"
    assert manifest["promptTemplateVersion"] == "1.6.0"
    assert manifest["responseContractVersion"] == "1.0.0"
    assert manifest["providerCallCount"] == 0
    assert manifest["automaticDeploymentAuthorized"] is False


def test_renderer_omits_empty_risks_section(tmp_path: Path) -> None:
    source, package = make_package(tmp_path)
    request_path = write_analysis_prompt(package, output_root=tmp_path / "out")
    response = valid_response(package)
    response["changeCandidates"][0]["risks"] = []
    path = generate_validated_analysis(
        analysis_request_path=request_path,
        source_brief_path=source,
        response=response,
        output_root=tmp_path / "out",
    )
    markdown = (path / "analysis.md").read_text(encoding="utf-8")
    assert "- Risks:" not in markdown


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


def test_atomic_install_rolls_back_fresh_and_overwrite_targets_on_final_check_failure(
    tmp_path: Path,
) -> None:
    def fail_final_check() -> None:
        raise RuntimeError("source changed during install")

    fresh = tmp_path / "fresh"
    with pytest.raises(RuntimeError, match="source changed"):
        _install(
            fresh,
            {"analysis.json": "new"},
            overwrite=False,
            same_target=lambda _path: False,
            final_check=fail_final_check,
        )
    assert not fresh.exists()
    assert not tuple(tmp_path.glob(".fresh.*"))

    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "analysis.json").write_text("original", encoding="utf-8")
    with pytest.raises(RuntimeError, match="source changed"):
        _install(
            existing,
            {"analysis.json": "replacement"},
            overwrite=True,
            same_target=lambda _path: True,
            final_check=fail_final_check,
        )
    assert (existing / "analysis.json").read_text(encoding="utf-8") == "original"
    assert {item.name for item in existing.iterdir()} == {"analysis.json"}
    assert not tuple(tmp_path.glob(".existing.*"))


def test_validated_writer_rolls_back_when_final_source_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source, package = make_package(tmp_path)
    analysis = validate_response(package, valid_response(package))
    checks = 0
    original = analysis_writer.verify_source_brief_unchanged

    def fail_on_transaction_final_check(source) -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError("source changed before commit")
        original(source)

    monkeypatch.setattr(
        analysis_writer,
        "verify_source_brief_unchanged",
        fail_on_transaction_final_check,
    )
    output_root = tmp_path / "atomic-out"
    with pytest.raises(RuntimeError, match="source changed before commit"):
        write_validated_analysis(
            package,
            analysis,
            response_input_digest="0" * 64,
            output_root=output_root,
    )
    assert checks == 3
    llm_root = output_root / "llm-analysis"
    assert not llm_root.exists() or not tuple(llm_root.iterdir())
    assert not tuple(output_root.rglob(".*.backup-*"))
