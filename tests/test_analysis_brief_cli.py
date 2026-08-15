import json
from pathlib import Path

import pytest

from defence_project_analytics import cli
from defence_project_analytics.brief.registry import required_tables


def make_stage_bundle(root: Path) -> Path:
    bundle = root / "source-stage"
    (bundle / "tables").mkdir(parents=True)
    metadata = {
        "reportContractVersion": "1.0.0",
        "analysisType": "stageDifficulty",
        "analysisVersion": "1.0.0",
        "generatedAtUtc": "2026-08-15T00:00:00Z",
        "scope": {
            "environment": "Test", "stageKey": "stage1", "contentVersion": 4,
            "appVersion": None, "releaseId": None, "releaseChannel": None,
            "releaseType": None, "isDevelopmentBuild": None,
            "uploadedAtUtcStart": None, "uploadedAtUtcEnd": "2026-08-15T00:00:00Z",
        },
        "sample": {"finalAttempts": 10, "uniquePlayers": 2, "deaths": 5},
        "quality": {
            "telemetryCompleteRate": {"count": 9, "denominator": 10, "ratio": 0.9},
            "detailCoverageRate": {"count": 8, "denominator": 10, "ratio": 0.8},
        },
        "definitions": {"population": "final attempts"},
        "warnings": [{"code": "LOW_SAMPLE_ATTEMPTS", "message": "Small sample."}],
        "dryRunEstimatedBytes": 1,
    }
    metrics = {
        "outcome": {
            "finalAttempts": 10, "uniquePlayers": 2, "clears": 2, "deaths": 5,
            "abandons": 3, "unrecognizedOutcomes": 0,
            "clearRate": {"count": 2, "denominator": 7, "ratio": 2 / 7},
        },
        "survival": {"observedCount": 10, "missingCount": 0, "p25": 20, "median": 40, "p75": 70},
        "deathTiming": {}, "deathConcentration": {}, "deathCauses": {},
        "incomingDamage": {}, "threat": {}, "playerStateAtDeath": {},
        "dataQuality": metadata["quality"],
    }
    (bundle / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (bundle / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (bundle / "report.md").write_text("# Source report\n", encoding="utf-8")
    for filename, columns in required_tables("stageDifficulty").items():
        (bundle / "tables" / filename).write_text(
            ",".join(columns) + "\n", encoding="utf-8"
        )
    return bundle


def test_analysis_brief_cli_does_not_create_bigquery_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = make_stage_bundle(tmp_path)
    monkeypatch.setattr(
        cli, "get_client", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("get_client must not be called")
        )
    )
    assert cli.main([
        "analysis-brief",
        "--mode", "single-version",
        "--source-report", str(source),
        "--output-root", str(tmp_path / "output"),
    ]) == 0
