from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from defence_project_analytics.brief.loader import load_source_bundle
from defence_project_analytics.analysis_brief import build_analysis_brief
from defence_project_analytics.brief.models import AnalysisBriefRequest
from defence_project_analytics.cli import main
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.metric_registry import (
    OBSERVED_APP_RETURN, metric_authority, is_decision_evidence_item,
)
from defence_project_analytics.observed_app_return import (
    ObservedAppReturnRequest, assemble_bundle, build_queries, TABLE_SPECS,
)
from defence_project_analytics.reporting.writer import write_report_bundle
from defence_project_analytics.sql_loader import load_sql


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _frames() -> dict[str, pd.DataFrame]:
    cohort = {
        "cohortType": "overall", "cohortValue": "All",
        "anchorFinalAttempts": 4, "eligibleAnchors": 3, "ineligibleAnchors": 1,
        "observedReturnCount": 2, "observedReturnRate": 2 / 3,
        "coldStartReturnCount": 1, "foregroundResumeReturnCount": 1,
        "rightCensoredCount": 1, "rightCensoredRate": 1 / 3,
        "returnedWithinThresholdCount": 1, "returnedAfterThresholdCount": 1,
        "noObservedReturnBeyondThresholdCount": 1, "thresholdRightCensoredCount": 0,
        "thresholdIneligibleCount": 1,
    }
    quality = {
        "physicalRowsRead": 8, "logicalOccurrences": 7,
        "exactDuplicateRowsRemoved": 1, "conflictingOccurrenceKeys": 0,
        "missingPlayerId": 0, "invalidId": 0, "invalidTimestamp": 0,
        "timingQualityExcluded": 0, "attributionExcluded": 0,
        "developmentExcluded": 0, "coldStartProcessReuse": 0,
        "anchorsTotal": 4, "anchorsEligible": 3, "anchorsIneligible": 1,
        "missingLifecycleBaseline": 1, "productionDevelopmentExcluded": 0,
        "runLifecycleLinkageMissing": 4, "runLifecycleLinkageMismatch": 0,
        "candidateTimestampTies": 0, "invalidAnchorPlayerId": 0,
        "conflictingAnchorIdentity": 0, "invalidAnchorEnd": 0,
    }
    return {
        "cohorts": pd.DataFrame([cohort, {
            **cohort, "cohortType": "outcome", "cohortValue": "Dead",
        }, {
            **cohort, "cohortType": "stage", "cohortValue": "stage1",
        }]),
        "latency": pd.DataFrame([{
            "observedCount": 2, "p50Seconds": 50.0, "p75Seconds": 75.0,
            "p90Seconds": 90.0,
        }]),
        "quality": pd.DataFrame([quality]),
    }


def _request(**overrides: object) -> ObservedAppReturnRequest:
    values = dict(environment="Test", content_version=129, analysis_as_of_utc=NOW,
                  threshold_days=1, source_upload_grace_hours=24)
    values.update(overrides)
    return ObservedAppReturnRequest(**values)


def test_threshold_pair_and_backend_guard() -> None:
    with pytest.raises(ValueError, match="provided together"):
        _request(source_upload_grace_hours=None)
    with pytest.raises(ValueError, match="positive integer"):
        _request(threshold_days=0)
    with pytest.raises(ValueError, match="nonnegative integer"):
        _request(source_upload_grace_hours=-1)
    with pytest.raises(ValueError, match="logical environment"):
        build_queries(_request(), config=AnalyticsConfig.for_backend("production"))


def test_cli_requires_explicit_backend_and_rejects_environment_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = [
        "observed-app-return", "--environment", "Test", "--content-version", "129",
        "--as-of", "2026-09-30T00:00:00Z", "--dry-run",
    ]
    with pytest.raises(SystemExit) as error:
        main(tokens)
    assert error.value.code == 2
    monkeypatch.setattr(
        "defence_project_analytics.cli.get_client",
        lambda *args, **kwargs: pytest.fail("must fail before BigQuery client creation"),
    )
    assert main(["--backend", "production", *tokens]) == 1


def test_bundle_invariants_manifest_privacy_and_c1_loading(tmp_path: Path) -> None:
    bundle = assemble_bundle(_request(), _frames(), estimated_bytes=0, generated_at_utc=NOW)
    path = write_report_bundle(
        bundle, output_root=tmp_path, table_specs=TABLE_SPECS, include_manifest=True
    )
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((path / name).read_bytes()).hexdigest() == digest
    loaded = load_source_bundle(path)
    assert loaded.analysis_type == "observedAppReturn"
    brief = build_analysis_brief(
        AnalysisBriefRequest("single-version", (path,)), workspace_root=tmp_path
    )
    assert brief.scope.domains == ("observedAppReturn",)
    assert any(
        item.metric == "observedReturnRate" and item.domain == "observedAppReturn"
        for item in brief.evidence
    )
    assert not is_decision_evidence_item({
        "domain": "observedAppReturn", "metricFamily": "return",
        "metric": "observedReturnRate",
    })
    forbidden = ("telemetryPlayerId", "runId", "attemptId",
                 "appProcessSessionId", "lifecycleOccurrenceId")
    for file in path.rglob("*"):
        if file.is_file():
            assert all(token not in file.read_text(encoding="utf-8") for token in forbidden)
    authority = metric_authority((OBSERVED_APP_RETURN, "return", "observedReturnRate"))
    assert authority.known_readable and authority.evidence_eligible
    assert not authority.comparison_eligible
    assert not authority.decision_eligible
    assert not authority.target_eligible
    (path / "metrics.json").write_text("{}", encoding="utf-8")
    with pytest.raises(Exception, match="missing required key|manifest digest"):
        load_source_bundle(path)


def test_cross_query_and_threshold_invariants() -> None:
    frames = _frames()
    frames["quality"].loc[0, "anchorsEligible"] = 2
    with pytest.raises(ValueError, match="quality/cohort"):
        assemble_bundle(_request(), frames, estimated_bytes=0, generated_at_utc=NOW)
    frames = _frames()
    frames["cohorts"].loc[0, "returnedAfterThresholdCount"] = 0
    with pytest.raises(ValueError, match="threshold invariant"):
        assemble_bundle(_request(), frames, estimated_bytes=0, generated_at_utc=NOW)


def test_b7_anchor_normalization_parity() -> None:
    b7 = load_sql("sql/analysis/_run_retention_population_ctes_v1.sql")
    b8 = load_sql("sql/analysis/_observed_app_return_ctes_v1.sql")
    for sql in (b7, b8):
        assert "PARTITION BY environment, telemetryPlayerId, attemptId" in sql
        assert ("ORDER BY segmentIndex DESC, segmentEndedAtUtc DESC, "
                "uploadedAtUtc DESC, runId DESC") in sql
        assert "isAttemptFinal IS TRUE" in sql


@pytest.mark.skipif(os.getenv("DPA_LIVE_B8") != "1", reason="explicit read-only BigQuery fixture run")
def test_bigquery_inline_lifecycle_fixture() -> None:
    """Run B-8's actual population SQL over inline synthetic rows; upload nothing."""
    from defence_project_analytics.bigquery_client import get_client, query_dataframe
    from defence_project_analytics.models import QueryParameterValue, QuerySpec

    config = AnalyticsConfig.for_backend("test")
    request = _request(threshold_days=None, source_upload_grace_hours=None)
    query = build_queries(request, config=config)["cohorts"]
    fragment = load_sql("sql/analysis/_observed_app_return_ctes_v1.sql", config=config)
    refs = {
        "`bald-ops-test.game_telemetry.telemetry_run_summary`": "fixture_runs",
        "`bald-ops-test.game_telemetry.telemetry_app_lifecycle_events`": "fixture_lifecycle",
        "`bald-ops-test.game_telemetry.telemetry_run_start_snapshot`": "fixture_starts",
    }
    for source, target in refs.items():
        fragment = fragment.replace(source, target)
    fixture = """
fixture_runs AS (
  SELECT @environment AS environment, FORMAT('%032x', player) AS telemetryPlayerId,
    CONCAT('attempt-', CAST(player AS STRING)) AS attemptId,
    CONCAT('run-', CAST(player AS STRING)) AS runId,
    'stage1' AS stageKey, 'Dead' AS gameplayOutcome, 'Gameplay' AS segmentKind,
    1 AS segmentIndex, TIMESTAMP('2026-09-22T12:00:00Z') AS segmentEndedAtUtc,
    '1' AS appVersion, 129 AS contentVersion, 'r' AS releaseId,
    'QA' AS releaseChannel, 'Test' AS releaseType,
    player = 16 AS isDevelopmentBuild,
    TIMESTAMP('2026-09-23T00:00:00Z') AS uploadedAtUtc,
    TRUE AS isAttemptFinal
  FROM UNNEST([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]) AS player
),
fixture_lifecycle AS (
  SELECT 1 AS contractVersion, @environment AS environment,
    IF(x.player = 0, NULL, FORMAT('%032x', x.player)) AS telemetryPlayerId,
    x.kind AS returnKind,
    TIMESTAMP(x.occurred) AS occurredAtUtc,
    IF(x.background IS NULL, NULL, TIMESTAMP(x.background)) AS previousBackgroundAtUtc,
    x.duration AS backgroundDurationSeconds, x.quality AS timingQuality,
    FORMAT('%032x', x.process) AS appProcessSessionId,
    IF(x.occurrence = 1502, 'INVALID', FORMAT('%032x', x.occurrence))
      AS lifecycleOccurrenceId,
    x.occurrence = 1101 AS capturedBeforeProfileAdmission,
    IF(x.player = 0, 'UnavailableDuringTransition', 'CanonicalProfile')
      AS profileAttributionStatus,
    'Android' AS platform, '1' AS appVersion, 129 AS contentVersion,
    'r' AS releaseId, 'folder' AS releaseFolder, 'QA' AS releaseChannel,
    'Test' AS releaseType, x.player = 16 AS isDevelopmentBuild,
    'hash' AS catalogHash,
    'source' AS catalogSource, TRUE AS releaseIdentityResolved,
    FALSE AS analyticsMeasurementAvailable, FALSE AS bridgeApplied,
    TIMESTAMP(x.uploaded) AS uploadedAtUtc
  FROM UNNEST([
    STRUCT(1 AS player, 'ColdStart' AS kind, '2026-09-22T11:00:00Z' AS occurred,
      CAST(NULL AS STRING) AS background, CAST(NULL AS FLOAT64) AS duration,
      'NotApplicable' AS quality, 101 AS process, 101 AS occurrence,
      '2026-09-23T00:00:00Z' AS uploaded),
    (1,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',102,102,'2026-09-23T00:00:00Z'),
    (1,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',102,102,'2026-09-24T00:00:00Z'),
    (2,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',201,201,'2026-09-23T00:00:00Z'),
    (2,'ForegroundResume','2026-09-22T13:00:00Z','2026-09-22T12:30:00Z',1800.0,'Valid',201,202,'2026-09-23T00:00:00Z'),
    (2,'ColdStart','2026-09-22T14:00:00Z',NULL,NULL,'NotApplicable',203,203,'2026-09-23T00:00:00Z'),
    (3,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',301,301,'2026-09-23T00:00:00Z'),
    (3,'ForegroundResume','2026-09-22T13:00:00Z','2026-09-22T12:30:00Z',1800.0,'Valid',301,302,'2026-09-23T00:00:00Z'),
    (3,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',303,303,'2026-09-23T00:00:00Z'),
    (4,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',401,401,'2026-09-23T00:00:00Z'),
    (5,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',501,501,'2026-09-23T00:00:00Z'),
    (5,'ForegroundResume','2026-09-22T13:00:00Z','2026-09-22T11:59:00Z',3660.0,'Valid',501,502,'2026-09-23T00:00:00Z'),
    (6,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',601,601,'2026-09-23T00:00:00Z'),
    (6,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',602,602,'2026-10-01T00:00:00Z'),
    (7,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',701,701,'2026-09-23T00:00:00Z'),
    (7,'ForegroundResume','2026-09-22T13:00:00Z','2026-09-22T12:30:00Z',NULL,'Invalid',701,702,'2026-09-23T00:00:00Z'),
    (8,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',801,801,'2026-09-23T00:00:00Z'),
    (8,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',802,802,'2026-09-23T00:00:00Z'),
    (8,'ColdStart','2026-09-22T14:00:00Z',NULL,NULL,'NotApplicable',803,802,'2026-09-23T00:00:00Z'),
    (8,'ColdStart','2026-09-24T13:00:00Z',NULL,NULL,'NotApplicable',804,804,'2026-09-25T00:00:00Z'),
    (9,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',901,901,'2026-09-23T00:00:00Z'),
    (9,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',901,902,'2026-09-23T00:00:00Z'),
    (9,'ForegroundResume','2026-09-22T14:00:00Z','2026-09-22T13:30:00Z',1800.0,'Valid',901,903,'2026-09-23T00:00:00Z'),
    (10,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',1001,1001,'2026-09-23T00:00:00Z'),
    (99,'ForegroundResume','2026-09-22T13:00:00Z','2026-09-22T12:30:00Z',1800.0,'Valid',1001,9901,'2026-09-23T00:00:00Z'),
    (10,'ForegroundResume','2026-09-22T14:00:00Z','2026-09-22T13:30:00Z',1800.0,'Valid',1001,1002,'2026-09-23T00:00:00Z'),
    (11,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',1101,1101,'2026-09-23T00:00:00Z'),
    (11,'ForegroundResume','2026-09-22T14:00:00Z','2026-09-22T13:30:00Z',1800.0,'Valid',1101,1102,'2026-09-23T00:00:00Z'),
    (0,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',2001,2001,'2026-09-23T00:00:00Z'),
    (12,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',1201,1201,'2026-09-23T00:00:00Z'),
    (12,'ForegroundResume','2026-09-22T13:00:00Z','2026-09-22T13:00:00Z',0.0,'Valid',1201,1202,'2026-09-23T00:00:00Z'),
    (13,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',1301,1301,'2026-09-23T00:00:00Z'),
    (13,'ColdStart','2026-09-22T11:59:00Z',NULL,NULL,'NotApplicable',1302,1302,'2026-09-23T00:00:00Z'),
    (14,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',1401,1401,'2026-09-23T00:00:00Z'),
    (15,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',1501,1501,'2026-09-23T00:00:00Z'),
    (15,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',1502,1502,'2026-09-23T00:00:00Z'),
    (16,'ColdStart','2026-09-22T11:00:00Z',NULL,NULL,'NotApplicable',1601,1601,'2026-09-23T00:00:00Z'),
    (16,'ColdStart','2026-09-22T13:00:00Z',NULL,NULL,'NotApplicable',1602,1602,'2026-09-23T00:00:00Z')
  ]) AS x
),
fixture_starts AS (
  SELECT @environment AS environment, 'run-1' AS runId,
    CAST(NULL AS STRING) AS appProcessSessionId,
    CAST(NULL AS STRING) AS latestForegroundOccurrenceId,
    TIMESTAMP('2026-09-23T00:00:00Z') AS uploadedAtUtc, 0 AS rowIndex
)
"""
    sql = (
        f"WITH {fixture},\n{fragment}\n"
        "SELECT *, (SELECT COUNT(*) FROM occurrence_groups WHERE variantCount > 1) "
        "AS conflictKeys FROM classified ORDER BY telemetryPlayerId"
    )
    frame = query_dataframe(QuerySpec(sql, query.parameters), client=get_client(config),
                            config=config, maximum_bytes_billed=10_000_000)
    assert list(frame["outcome"]) == [
        "ObservedAppReturn", "ObservedAppReturn", "ObservedAppReturn",
        "Ineligible", "NoObservedReturnAsOf", "NoObservedReturnAsOf",
        "NoObservedReturnAsOf", "ObservedAppReturn", "ObservedAppReturn",
        "ObservedAppReturn", "ObservedAppReturn", "ObservedAppReturn",
        "NoObservedReturnAsOf", "Ineligible", "NoObservedReturnAsOf",
        "ObservedAppReturn",
    ]
    assert list(frame["returnKind"].iloc[:3]) == [
        "ColdStart", "ForegroundResume", "ColdStart",
    ]
    assert frame.iloc[3]["ineligibleReason"] == "MissingLifecycleBaseline"
    assert frame.iloc[2]["sameTimeCount"] == 2
    assert frame.iloc[7]["delaySeconds"] == 176400.0
    assert frame.iloc[8]["returnKind"] == "ForegroundResume"
    assert frame.iloc[9]["returnKind"] == "ForegroundResume"
    assert frame.iloc[10]["returnKind"] == "ForegroundResume"
    assert frame.iloc[0]["conflictKeys"] == 1
    assert frame.iloc[11]["returnKind"] == "ForegroundResume"
    assert frame.iloc[13]["ineligibleReason"] == "MissingLifecycleBaseline"
    threshold_params = dict(query.parameters)
    threshold_params["threshold_days"] = QueryParameterValue(1, "INT64")
    threshold_params["source_upload_grace_hours"] = QueryParameterValue(24, "INT64")
    classified = query_dataframe(
        QuerySpec(sql, threshold_params), client=get_client(config),
        config=config, maximum_bytes_billed=10_000_000,
    )
    assert list(classified["thresholdState"]) == [
        "ReturnedWithinThreshold", "ReturnedWithinThreshold",
        "ReturnedWithinThreshold", "Ineligible",
        "NoObservedReturnBeyondThreshold", "NoObservedReturnBeyondThreshold",
        "NoObservedReturnBeyondThreshold", "ReturnedAfterThreshold",
        "ReturnedWithinThreshold", "ReturnedWithinThreshold",
        "ReturnedWithinThreshold", "ReturnedWithinThreshold",
        "NoObservedReturnBeyondThreshold", "Ineligible",
        "NoObservedReturnBeyondThreshold", "ReturnedWithinThreshold",
    ]
    production_params = dict(query.parameters)
    production_params["environment"] = "Production"
    production = query_dataframe(
        QuerySpec(sql, production_params), client=get_client(config),
        config=config, maximum_bytes_billed=10_000_000,
    )
    assert production.iloc[15]["ineligibleReason"] == "ProductionDevelopmentExcluded"
