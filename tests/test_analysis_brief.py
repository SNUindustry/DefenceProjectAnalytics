from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from defence_project_analytics.analysis_brief import (
    AnalysisBriefRequest,
    build_analysis_brief,
)
from defence_project_analytics.brief.adapters import assign_evidence_ids
from defence_project_analytics.brief.compatibility import snapshot_compatibility
from defence_project_analytics.brief.compatibility import LIMITING_WARNING_CODES
from defence_project_analytics.brief.errors import (
    DuplicateCanonicalEvidenceIdentityError,
    EvidenceHashCollisionError,
)
from defence_project_analytics.brief.models import (
    EvidenceCandidate,
    EvidenceProvenance,
    LoadedSourceBundle,
    SnapshotDescriptor,
    SourceArtifact,
)
from defence_project_analytics.content_version_comparison import (
    SUMMARY_SPECS as LEGACY_SUMMARY_SPECS,
    TABLE_COMPARE_SPECS as LEGACY_TABLE_COMPARE_SPECS,
)
from defence_project_analytics.metric_registry import (
    COMPARISON_SUMMARY_SPECS,
    COMPARISON_TABLE_SPECS,
)


def candidate(*, metric: str = "clearRate", value: float = 0.5) -> EvidenceCandidate:
    return EvidenceCandidate(
        mode="singleVersion",
        source_analysis_type="stageDifficulty",
        domain="stageDifficulty",
        metric_family="outcome",
        metric=metric,
        entity_type=None,
        entity_key=None,
        dimension=None,
        dimension_value=None,
        value_type="scalar",
        unit="ratio",
        observation_unit="finalAttempts",
        value={"value": value},
        observed=True,
        status="Comparable",
        warning_codes=(),
        sample={"finalAttempts": 100},
        provenance=EvidenceProvenance("source", "digest", "metrics.json", "hash", {}),
        priority=2,
        core=True,
    )


def source(domain: str, mode: str, cutoff) -> LoadedSourceBundle:
    return LoadedSourceBundle(
        path=Path("unused"),
        analysis_type=domain,
        domain=domain,
        bundle_name=domain,
        bundle_digest=domain,
        portable_path=None,
        metadata={"warnings": []},
        metrics={},
        tables={},
        artifacts={"metrics.json": SourceArtifact("metrics.json", "hash", 1)},
        snapshot=SnapshotDescriptor(domain, mode, cutoff, "test"),
    )


def test_canonical_identity_is_checked_before_short_hash() -> None:
    first = candidate(value=0.1)
    second = candidate(value=0.9)
    with pytest.raises(DuplicateCanonicalEvidenceIdentityError):
        assign_evidence_ids((first, second))


def test_shared_registry_preserves_b6_metric_contract() -> None:
    assert COMPARISON_SUMMARY_SPECS == LEGACY_SUMMARY_SPECS
    for domain, shared_specs in COMPARISON_TABLE_SPECS.items():
        legacy_specs = LEGACY_TABLE_COMPARE_SPECS[domain]
        assert [
            (
                item.filename, item.metric_family, item.keys, item.metrics,
                item.entity_type, item.observation_unit,
            )
            for item in shared_specs
        ] == [
            (
                item.filename, item.metric_family, item.keys, item.metrics,
                item.entity_type, item.observation_unit,
            )
            for item in legacy_specs
        ]


def test_distinct_identity_short_hash_collision_is_separate() -> None:
    class FakeHash:
        def hexdigest(self) -> str:
            return "a" * 64

    with pytest.raises(EvidenceHashCollisionError):
        assign_evidence_ids(
            (candidate(metric="clearRate"), candidate(metric="deathRate")),
            hash_function=lambda _: FakeHash(),
        )


def test_value_change_does_not_change_evidence_id() -> None:
    left = assign_evidence_ids((candidate(value=0.1),))[0]
    right = assign_evidence_ids((candidate(value=0.9),))[0]
    assert left.evidence_id == right.evidence_id


def test_snapshot_mode_and_cutoff_limitations_are_separate() -> None:
    now = pd.Timestamp("2026-08-15T00:00:00Z").to_pydatetime()
    compatibility, warnings = snapshot_compatibility((
        source("stageDifficulty", "uploadedAtUtcUpperBound", now),
        source("weaponPerformance", "analysisAsOfUtcParameter", now),
    ))
    assert compatibility.mode_difference is True
    assert compatibility.cutoff_difference_seconds == 0
    assert [item.code for item in warnings] == ["SOURCE_SNAPSHOT_MODE_DIFFERENCE"]
    assert "SOURCE_SNAPSHOT_MODE_DIFFERENCE" not in LIMITING_WARNING_CODES

    _, warnings = snapshot_compatibility((
        source("stageDifficulty", "uploadedAtUtcUpperBound", now),
        source(
            "weaponPerformance",
            "analysisAsOfUtcParameter",
            now + pd.Timedelta(seconds=61),
        ),
    ))
    assert {item.code for item in warnings} == {
        "SOURCE_SNAPSHOT_CUTOFF_MISMATCH",
        "SOURCE_SNAPSHOT_MODE_DIFFERENCE",
    }

    _, warnings = snapshot_compatibility((
        source("stageDifficulty", "unboundedIngestionAtGeneration", None),
        source("weaponPerformance", "analysisAsOfUtcParameter", now),
    ))
    assert "SOURCE_SNAPSHOT_CUTOFF_MISSING" in {item.code for item in warnings}


def test_existing_single_live_bundle_keeps_limited_core_evidence() -> None:
    root = Path("reports/generated")
    paths = (
        root / "progression-next-run/Test__all-stages__cv-4__dc0533d9",
        root / "post-run-behavior/Test__all-stages__cv-4__25b214bc",
    )
    if not all(path.exists() for path in paths):
        pytest.skip("local acceptance artifacts are not present")
    brief = build_analysis_brief(AnalysisBriefRequest(
        mode="singleVersion", source_report_paths=paths,
    ))
    assert brief.overall_status == "Limited"
    for domain in ("progressionNextRun", "postRunBehavior"):
        core = [item for item in brief.evidence if item.domain == domain and item.core]
        assert len(core) >= 3
        assert all(item.warning_codes for item in core)
    assert brief.selection_summary.brief_character_count <= 48_000
    assert brief.selection_summary.truncated is True
