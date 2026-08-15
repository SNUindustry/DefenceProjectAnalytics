from __future__ import annotations

import json
from pathlib import Path

import pytest

from defence_project_analytics import cli
from defence_project_analytics.llm_analysis import AnalysisPromptRequest, build_analysis_prompt
from llm_analysis_fixtures import make_c1_bundle, valid_response


def test_c2_cli_never_creates_bigquery_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = make_c1_bundle(tmp_path)
    monkeypatch.setattr(
        cli, "get_client", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("get_client must not be called")
        )
    )
    output = tmp_path / "output"
    assert cli.main([
        "analysis-prompt", "--source-brief", str(source),
        "--output-root", str(output),
    ]) == 0
    request_path = next((output / "analysis-requests").iterdir())
    package = build_analysis_prompt(
        AnalysisPromptRequest(source), workspace_root=tmp_path
    )
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(valid_response(package)), encoding="utf-8")
    assert cli.main([
        "analysis-validate", "--analysis-request", str(request_path),
        "--source-brief", str(source), "--response", str(response_path),
        "--output-root", str(output),
    ]) == 0

