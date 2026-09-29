from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from defence_project_analytics import cli
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.metric_registry import (
    OBSERVED_APP_RETURN,
    OBSERVED_UNINSTALL,
    RETENTION_EVIDENCE,
    RUN_RETENTION,
    known_metric_keys,
    metric_authority,
)
from defence_project_analytics.observed_uninstall import ATTRIBUTED_EVENT_COLUMNS
from defence_project_analytics.retention_evidence import (
    RetentionEvidenceRequest,
    ReturnDomain,
    analyze_retention_evidence,
    generate_retention_evidence_report,
    validate_retention_evidence_bundle,
    write_return_source_artifact,
)
from defence_project_analytics.retention_evidence_policy import (
    ComparisonPlan,
    HorizonContract,
    SourceCut,
    SourceFinalizationState,
)
from defence_project_analytics.run_retention import (
    RunRetentionRequest, build_run_retention_restricted_query,
)
from defence_project_analytics.observed_app_return import (
    ObservedAppReturnRequest, build_restricted_query as build_app_restricted_query,
)


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=30)
P1 = "1" * 32
P2 = "2" * 32


def returns(domain: ReturnDomain) -> pd.DataFrame:
    rows = [
        # P1 episode A: app at d3, gameplay at d4, uninstall later at d20.
        dict(canonicalProfileId=P1, anchorId="a", anchorEndUtc=T0,
             anchorEligible=True,
             returnObservedAtUtc=T0 + timedelta(days=4 if domain is ReturnDomain.GAMEPLAY else 3),
             returnClassification="NextRunObserved" if domain is ReturnDomain.GAMEPLAY else "ObservedAppReturn",
             telemetryBackend="test", environment="Test", contentVersion=7,
             releaseId="r1", completeSourceCoverage=True),
        # P1 episode B: gameplay at d10 relative to anchor, app absent. Mature bounded app absence.
        dict(canonicalProfileId=P1, anchorId="b", anchorEndUtc=T0 + timedelta(days=10),
             anchorEligible=True,
             returnObservedAtUtc=(T0 + timedelta(days=20) if domain is ReturnDomain.GAMEPLAY else None),
             returnClassification="NextRunObserved" if domain is ReturnDomain.GAMEPLAY else "NoObservedReturnAsOf",
             telemetryBackend="test", environment="Test", contentVersion=7,
             releaseId="r1", completeSourceCoverage=True),
        # P2 differs by source eligibility, proving denominator independence.
        dict(canonicalProfileId=P2, anchorId="c", anchorEndUtc=T0 + timedelta(days=25),
             anchorEligible=domain is ReturnDomain.GAMEPLAY,
             returnObservedAtUtc=(T0 + timedelta(days=26) if domain is ReturnDomain.GAMEPLAY else None),
             returnClassification="NextRunObserved" if domain is ReturnDomain.GAMEPLAY else "Ineligible",
             telemetryBackend="test", environment="Test", contentVersion=7,
             releaseId="r1", completeSourceCoverage=True),
    ]
    return pd.DataFrame(rows)


def uninstall_frame() -> pd.DataFrame:
    def row(at: datetime, profile: str, *, status: str = "Mapped", state: str = "Final", pseudo: str = "pseudo") -> dict[str, object]:
        return {
            "gaProject": "bald-ops", "gaPropertyId": "1", "gaStreamId": "2",
            "telemetryBackend": "test", "environment": "Test",
            "eventTimestampUtc": at, "userPseudoId": pseudo, "gaUserId": "",
            "rawEventStatus": "Valid", "sourceTableDate": "2026-09-01",
            "sourceTableKind": "Daily" if state == "Final" else "Intraday",
            "sourceFinalizationState": state, "eventBundleSequenceId": 1,
            "batchEventIndex": 0, "batchOrderingId": 0, "physicalCount": 1,
            "variantCount": 1, "exactDuplicateRowsRemoved": 0,
            "attributionStatus": status, "attributionReason": status,
            "telemetryPlayerId": profile if status == "Mapped" else "",
            "retentionBridgeId": "b" * 32 if status == "Mapped" else "",
            "bridgeObservedAtUtc": T0, "mappingAgeSeconds": 10.0,
        }
    return pd.DataFrame([
        row(T0 + timedelta(days=2), P1),  # uninstall then both returns, episode A
        row(T0 + timedelta(days=3), P1),  # same time as app return
        row(T0 + timedelta(days=20), P1), # episode B only, no duplicate attribution
        row(T0 + timedelta(days=21), P1), # repeated uninstall preserved
        row(T0 + timedelta(days=5), "", status="Unmapped", pseudo="u"),
        row(T0 + timedelta(days=6), "", status="Ambiguous", pseudo="a"),
        row(T0 + timedelta(days=7), P1, state="Provisional", pseudo="p"),
    ], columns=ATTRIBUTED_EVENT_COLUMNS)


def artifacts(tmp_path: Path) -> tuple[Path, Path, Path]:
    gameplay = write_return_source_artifact(
        returns(ReturnDomain.GAMEPLAY), domain=ReturnDomain.GAMEPLAY,
        output_root=tmp_path / "restricted", backend="test", environment="Test",
        analysis_as_of_utc=AS_OF,
    )
    app = write_return_source_artifact(
        returns(ReturnDomain.APP), domain=ReturnDomain.APP,
        output_root=tmp_path / "restricted", backend="test", environment="Test",
        analysis_as_of_utc=AS_OF,
    )
    uninstall = tmp_path / "restricted" / "restrictedObservedUninstallEvents" / "fixture"
    uninstall.mkdir(parents=True)
    uninstall_frame().to_csv(uninstall / "observed-uninstall-events.csv", index=False)
    metadata = {
        "artifactType": "restrictedObservedUninstallEvents",
        "contractVersion": "1.0.0",
        "scope": {"telemetryBackend": "test", "environment": "Test",
                  "analysisAsOfUtc": AS_OF.isoformat()},
    }
    (uninstall / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "artifactType": "restrictedObservedUninstallEvents",
        "files": {
            name: hashlib.sha256((uninstall / name).read_bytes()).hexdigest()
            for name in ("metadata.json", "observed-uninstall-events.csv")
        },
    }
    (uninstall / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return gameplay, app, uninstall


def request(**values: object) -> RetentionEvidenceRequest:
    payload = dict(backend="test", environment="Test", analysis_as_of_utc=AS_OF,
                   horizon_days=7, source_upload_grace_hours=24,
                   content_version=7, release_id="r1")
    payload.update(values)
    return RetentionEvidenceRequest(**payload)


def test_episode_horizon_maturity_sequence_and_independent_denominators(tmp_path: Path) -> None:
    gameplay, app, uninstall = artifacts(tmp_path)
    analysis = analyze_retention_evidence(
        request(), gameplay_artifact=gameplay, app_artifact=app,
        uninstall_artifact=uninstall,
    )
    g = analysis.metrics["gameplayReturn"]
    a = analysis.metrics["appReturn"]
    assert g["eligibleAnchorCount"] == 3
    assert a["eligibleAnchorCount"] == 2
    assert g["matureAnchorCount"] == 2
    assert a["matureAnchorCount"] == 2
    assert g["returnedWithinHorizonCount"] == 1
    assert a["returnedWithinHorizonCount"] == 1
    assert g["boundedAbsenceCount"] == 1  # d10 return remains factual, outside horizon
    assert a["boundedAbsenceCount"] == 1
    # An already observed early positive remains factual but is not a mature denominator.
    assert g["rightCensoredCount"] == 0
    assert a["rightCensoredCount"] == 0
    rows = analysis.restricted_episodes.set_index("anchorEndUtc")
    first = rows.loc["2026-09-01T00:00:00Z"]
    second = rows.loc["2026-09-11T00:00:00Z"]
    assert first["mappedUninstallCount"] == 3
    assert second["mappedUninstallCount"] == 2
    assert "UninstallThenLaterAppReturn" in first["sequenceFacets"]
    assert "SameObservedTime" in first["sequenceFacets"]
    assert second["gameplayReturnObservedAtUtc"] == "2026-09-21T00:00:00Z"
    assert second["gameplayState"] == "BoundedAbsenceObservation"


def test_no_horizon_has_factual_summary_without_fixed_horizon_metrics(tmp_path: Path) -> None:
    paths = artifacts(tmp_path)
    analysis = analyze_retention_evidence(
        request(horizon_days=None, source_upload_grace_hours=None),
        gameplay_artifact=paths[0], app_artifact=paths[1], uninstall_artifact=paths[2],
    )
    assert analysis.metrics["gameplayReturn"]["matureAnchorCount"] is None
    assert analysis.metrics["gameplayReturn"]["returnedWithinHorizonRate"] is None
    assert analysis.metrics["gameplayReturn"]["authorityResolution"]["comparison"]["decision"] == "Denied"
    assert len(analysis.restricted_episodes) == 3


def test_incomplete_source_coverage_stays_censored_and_out_of_absence(tmp_path: Path) -> None:
    gameplay, app, uninstall = artifacts(tmp_path)
    source = pd.read_csv(gameplay / "retention-anchors.csv", keep_default_na=False)
    source.loc[source["anchorId"] == "a", "completeSourceCoverage"] = False
    replacement = write_return_source_artifact(
        source, domain=ReturnDomain.GAMEPLAY, output_root=tmp_path / "replacement",
        backend="test", environment="Test", analysis_as_of_utc=AS_OF,
    )
    analysis = analyze_retention_evidence(
        request(), gameplay_artifact=replacement, app_artifact=app,
        uninstall_artifact=uninstall,
    )
    # The positive fact remains, but incomplete coverage keeps it out of maturity.
    assert analysis.metrics["gameplayReturn"]["earlyPositiveObservationCount"] == 2
    assert analysis.metrics["gameplayReturn"]["matureAnchorCount"] == 1
    assert analysis.metrics["gameplayReturn"]["boundedAbsenceCount"] == 1


def test_profile_switch_sequence_never_cross_links_profiles(tmp_path: Path) -> None:
    gameplay, app, uninstall = artifacts(tmp_path)
    events = pd.read_csv(uninstall / "observed-uninstall-events.csv", keep_default_na=False)
    extra = events.iloc[0].copy()
    extra["eventTimestampUtc"] = (T0 + timedelta(days=26)).isoformat()
    extra["telemetryPlayerId"] = P2
    events = pd.concat([events, pd.DataFrame([extra])], ignore_index=True)
    events.to_csv(uninstall / "observed-uninstall-events.csv", index=False)
    metadata_digest = hashlib.sha256((uninstall / "metadata.json").read_bytes()).hexdigest()
    event_digest = hashlib.sha256((uninstall / "observed-uninstall-events.csv").read_bytes()).hexdigest()
    (uninstall / "manifest.json").write_text(json.dumps({
        "artifactType": "restrictedObservedUninstallEvents",
        "files": {"metadata.json": metadata_digest,
                  "observed-uninstall-events.csv": event_digest},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    analysis = analyze_retention_evidence(
        request(), gameplay_artifact=gameplay, app_artifact=app,
        uninstall_artifact=uninstall,
    )
    episodes = analysis.restricted_episodes
    p2 = episodes[episodes["canonicalProfileId"] == P2].iloc[0]
    assert p2["mappedUninstallCount"] == 1
    assert episodes[episodes["canonicalProfileId"] == P1]["mappedUninstallCount"].sum() == 5


def test_normal_bundle_manifest_privacy_and_restricted_identity(tmp_path: Path) -> None:
    paths = artifacts(tmp_path)
    output = generate_retention_evidence_report(
        request(), gameplay_artifact=paths[0], app_artifact=paths[1],
        uninstall_artifact=paths[2], output_root=tmp_path / "generated",
        restricted_output_root=tmp_path / "r4-restricted",
    )
    assert validate_retention_evidence_bundle(output.report_path)["ready"]
    assert sorted(p.name for p in output.report_path.iterdir()) == [
        "censoring-summary.csv", "evidence-summary.csv", "manifest.json",
        "maturity-summary.csv", "metadata.json", "metrics.json", "report.md",
        "sequence-summary.csv", "source-compatibility-summary.csv",
        "uninstall-attribution-quality-summary.csv",
    ]
    normal = "\n".join(p.read_text(encoding="utf-8") for p in output.report_path.iterdir())
    assert P1 not in normal and P2 not in normal
    assert "telemetryPlayerId" not in normal and "canonicalProfileId" not in normal
    restricted = (output.restricted_path / "retention-episodes.csv").read_text(encoding="utf-8")
    assert P1 in restricted and "canonicalProfileId" in restricted
    for root in (output.report_path, output.restricted_path):
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest


def test_source_cut_and_scope_fail_closed(tmp_path: Path) -> None:
    gameplay, app, uninstall = artifacts(tmp_path)
    metadata_path = gameplay / "metadata.json"
    metadata_path.write_text(metadata_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        analyze_retention_evidence(request(), gameplay_artifact=gameplay,
                                   app_artifact=app, uninstall_artifact=uninstall)
    gameplay, app, uninstall = artifacts(tmp_path / "again")
    with pytest.raises(ValueError, match="backend.*environment mismatch"):
        RetentionEvidenceRequest("production", "Test", AS_OF)
    frame = returns(ReturnDomain.APP)
    frame["environment"] = "Production"
    bad = write_return_source_artifact(
        frame, domain=ReturnDomain.APP, output_root=tmp_path / "bad",
        backend="production", environment="Production", analysis_as_of_utc=AS_OF,
    )
    with pytest.raises(ValueError, match="incompatible environment"):
        analyze_retention_evidence(request(), gameplay_artifact=gameplay,
                                   app_artifact=bad, uninstall_artifact=uninstall)


def test_same_as_of_different_source_digest_creates_distinct_snapshot(tmp_path: Path) -> None:
    first_frame = returns(ReturnDomain.GAMEPLAY)
    first = write_return_source_artifact(
        first_frame, domain=ReturnDomain.GAMEPLAY, output_root=tmp_path,
        backend="test", environment="Test", analysis_as_of_utc=AS_OF,
    )
    second_frame = first_frame.copy()
    second_frame.loc[0, "returnObservedAtUtc"] = T0 + timedelta(days=5)
    second = write_return_source_artifact(
        second_frame, domain=ReturnDomain.GAMEPLAY, output_root=tmp_path,
        backend="test", environment="Test", analysis_as_of_utc=AS_OF,
    )
    assert first != second
    assert first.is_dir() and second.is_dir()


def test_runtime_authority_requires_complete_comparison_plan(tmp_path: Path) -> None:
    paths = artifacts(tmp_path)
    cut = SourceCut("cohort", "a" * 64, SourceFinalizationState.FINAL, AS_OF)
    plan = ComparisonPlan(
        horizon=HorizonContract(7, 24), minimum_mature_anchors_per_cohort=1,
        maximum_censoring_rate=1.0, baseline_mature_anchors=2,
        candidate_mature_anchors=2, baseline_censoring_rate=0.0,
        candidate_censoring_rate=0.0, backend_compatible=True,
        environment_compatible=True, content_release_scope_compatible=True,
        horizon_compatible=True, upload_grace_compatible=True,
        source_cut_compatible=True, baseline_source_cut=cut, candidate_source_cut=cut,
    )
    analysis = analyze_retention_evidence(
        request(comparison_plan=plan), gameplay_artifact=paths[0],
        app_artifact=paths[1], uninstall_artifact=paths[2],
    )
    assert analysis.metrics["gameplayReturn"]["authorityResolution"]["comparison"]["decision"] == "Allowed"
    assert analysis.metrics["appReturn"]["authorityResolution"]["comparison"]["decision"] == "Allowed"
    for domain in ("gameplayReturn", "appReturn"):
        auth = analysis.metrics[domain]["authorityResolution"]
        assert all(auth[name]["decision"] == "Denied" for name in ("decision", "target", "guardrail", "rollback"))


def test_metric_registry_adds_only_r4_derived_comparison_capability() -> None:
    r4 = {key for key in known_metric_keys() if key[0] == RETENTION_EVIDENCE}
    assert r4
    for key in r4:
        authority = metric_authority(key)
        assert authority.known_readable and authority.evidence_eligible
        assert authority.comparison_eligible == (key[1] in {"gameplayReturn", "appReturn"})
        assert not authority.decision_eligible and not authority.target_eligible
    for domain in (RUN_RETENTION, OBSERVED_APP_RETURN, OBSERVED_UNINSTALL):
        assert all(not metric_authority(key).comparison_eligible for key in known_metric_keys() if key[0] == domain)


def test_restricted_sql_adapters_reuse_source_ctes_without_redefining_semantics() -> None:
    config = AnalyticsConfig.for_backend("test")
    b7 = build_run_retention_restricted_query(
        RunRetentionRequest("Test", 7, analysis_as_of_utc=AS_OF), config=config,
    )
    b8 = build_app_restricted_query(
        ObservedAppReturnRequest("Test", 7, AS_OF), config=config,
    )
    for query in (b7, b8):
        assert "canonicalProfileId" in query.sql
        assert "completeSourceCoverage" in query.sql
        assert "telemetry_backend" in query.parameters
        assert query.parameters["telemetry_backend"].value == "test"
    assert "classified_retention" in b7.sql
    assert "eligible_lifecycle" in b8.sql


def test_cli_is_local_and_does_not_create_bigquery_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    paths = artifacts(tmp_path)
    monkeypatch.setattr("defence_project_analytics.cli.get_client", lambda *_a, **_k: pytest.fail("must stay local"))
    result = cli.main([
        "--backend", "test", "retention-evidence", "--environment", "Test",
        "--as-of", AS_OF.isoformat(), "--horizon-days", "7",
        "--source-upload-grace-hours", "24", "--content-version", "7",
        "--release-id", "r1", "--gameplay-artifact", str(paths[0]),
        "--app-artifact", str(paths[1]), "--uninstall-artifact", str(paths[2]),
        "--output-root", str(tmp_path / "generated"),
        "--restricted-output-root", str(tmp_path / "r4-restricted"),
    ])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["validation"]["ready"]
