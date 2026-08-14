import json

import pytest

from defence_project_analytics.stage_overview import StageOverviewRequest, generate_stage_overview_report
from defence_project_analytics.cli import main


METRICS = {
    "final_attempts": 4, "unique_telemetry_players": 2, "clears": 1, "deaths": 1,
    "abandons": 2, "clear_rate": 0.5, "telemetry_complete_assessed_attempts": 2,
    "telemetry_complete_rate": 0.5,
}


def test_optional_overview_bundle_has_no_tables_and_preserves_metrics(tmp_path) -> None:
    path = generate_stage_overview_report(StageOverviewRequest("stage1", "Test", 2), METRICS, output_root=tmp_path)
    assert not (path / "tables").exists()
    assert json.loads((path / "metrics.json").read_text(encoding="utf-8"))["clearRate"] == 0.5
    assert json.loads((path / "metadata.json").read_text(encoding="utf-8"))["analysisType"] == "stageOverview"


def test_overview_bundle_requires_content_version(tmp_path) -> None:
    with pytest.raises(ValueError, match="content_version"):
        generate_stage_overview_report(StageOverviewRequest("stage1", "Test"), METRICS, output_root=tmp_path)


def test_default_cli_stdout_remains_snake_case(monkeypatch, capsys) -> None:
    monkeypatch.setattr("defence_project_analytics.cli.get_client", lambda config: object())
    monkeypatch.setattr("defence_project_analytics.cli.get_stage_overview", lambda *args, **kwargs: METRICS)
    assert main(["stage-overview", "--stage-key", "stage1", "--environment", "Test"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {"stage_key": "stage1", "environment": "Test", "content_version": None}
    assert payload["metrics"]["clear_rate"] == 0.5
    assert "clearRate" not in payload["metrics"]
