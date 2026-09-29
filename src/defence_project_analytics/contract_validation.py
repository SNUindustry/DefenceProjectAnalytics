"""Local schema parsing and non-destructive live BigQuery contract validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from google.api_core.exceptions import NotFound

from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.sql_loader import repository_root


CORE_TABLE_COLUMNS: dict[str, set[str]] = {
    "telemetry_run_summary": {
        "environment", "telemetryPlayerId", "attemptId", "runId", "uploadId",
        "stageKey", "gameplayOutcome", "isAttemptFinal", "segmentIndex",
        "segmentStartedAtUtc", "segmentEndedAtUtc", "attemptElapsedTimeSeconds",
        "appVersion", "contentVersion", "releaseId", "releaseChannel",
        "releaseType", "isDevelopmentBuild", "uploadedAtUtc",
    },
    "telemetry_run_feedback_events": {
        "environment", "runId", "eventKind", "response", "occurredAtUtc", "eventId",
    },
    "telemetry_upload_chunks": {
        "environment", "runId", "uploadId", "transportVersion", "chunkIndex",
        "chunkCount", "chunkKind", "archivedAtUtc", "mappedAtUtc",
    },
    "telemetry_lobby_activity_events": {
        "environment", "eventId", "occurredAtUtc", "eventKind", "telemetryPlayerId",
    },
    "telemetry_transaction_events": {
        "environment", "eventId", "occurredAtUtc", "eventKind", "resultCategory",
        "telemetryPlayerId",
    },
}

CORE_VIEW_COLUMNS: dict[str, set[str]] = {
    "telemetry_gameplay_segments_v1": {
        "environment", "telemetryPlayerId", "attemptId", "runId", "segmentKind",
    },
    "telemetry_completed_gameplay_segments_v1": {
        "environment", "telemetryPlayerId", "attemptId", "runId", "segmentKind",
        "segmentTermination",
    },
    "telemetry_attempt_outcomes_v1": CORE_TABLE_COLUMNS["telemetry_run_summary"],
    "telemetry_upload_status_v1": {
        "environment", "runId", "uploadId", "telemetryComplete",
    },
}

_TYPE_ALIASES = {
    "BOOLEAN": "BOOL",
    "FLOAT": "FLOAT64",
    "INTEGER": "INT64",
    "RECORD": "STRUCT",
}


@dataclass(frozen=True, slots=True)
class SchemaFieldContract:
    name: str
    field_type: str
    mode: str


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    severity: str
    object_name: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ContractValidationReport:
    parsed_contracts: int
    checked_objects: int
    findings: tuple[ValidationFinding, ...]

    @property
    def blockers(self) -> tuple[ValidationFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocker")

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed_contracts": self.parsed_contracts,
            "checked_objects": self.checked_objects,
            "ready": self.ready,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def load_schema_contracts(root: Path | None = None) -> dict[str, tuple[SchemaFieldContract, ...]]:
    contract_dir = (root or repository_root()) / "contracts" / "bigquery-schema"
    contracts: dict[str, tuple[SchemaFieldContract, ...]] = {}
    for path in sorted(contract_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"Schema contract must be a non-empty JSON array: {path}")
        fields: list[SchemaFieldContract] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or not {"name", "type", "mode"} <= item.keys():
                raise ValueError(f"Invalid schema field in {path}: {item!r}")
            field = SchemaFieldContract(
                name=str(item["name"]),
                field_type=str(item["type"]).upper(),
                mode=str(item["mode"]).upper(),
            )
            if field.name in seen:
                raise ValueError(f"Duplicate field {field.name!r} in {path}")
            seen.add(field.name)
            fields.append(field)
        contracts[path.stem] = tuple(fields)
    if not contracts:
        raise ValueError(f"No schema contracts found under {contract_dir}")
    return contracts


def _required_table_fields(
    table_name: str, fields: tuple[SchemaFieldContract, ...]
) -> set[str]:
    required = {field.name for field in fields if field.mode == "REQUIRED"}
    required.update(CORE_TABLE_COLUMNS.get(table_name, set()))
    return required


def _normalize_type(field_type: str) -> str:
    normalized = field_type.upper()
    return _TYPE_ALIASES.get(normalized, normalized)


def validate_contracts(
    *, client: Any, config: AnalyticsConfig | None = None, root: Path | None = None
) -> ContractValidationReport:
    settings = config or AnalyticsConfig.from_env()
    contracts = load_schema_contracts(root)
    findings: list[ValidationFinding] = []
    checked = 0

    for table_name, contract_fields in contracts.items():
        checked += 1
        severity = "blocker" if table_name in CORE_TABLE_COLUMNS else "warning"
        try:
            actual = client.get_table(settings.object_ref(table_name))
        except NotFound:
            findings.append(ValidationFinding(severity, table_name, "object is missing"))
            continue
        if actual.table_type in {"VIEW", "MATERIALIZED_VIEW"}:
            findings.append(ValidationFinding(severity, table_name, "expected a table, found a view"))
            continue

        actual_fields = {field.name: _normalize_type(field.field_type) for field in actual.schema}
        expected_fields = {
            field.name: _normalize_type(field.field_type) for field in contract_fields
        }
        for name in sorted(_required_table_fields(table_name, contract_fields)):
            if name not in actual_fields:
                findings.append(ValidationFinding(severity, table_name, f"required column missing: {name}"))
            elif actual_fields[name] != expected_fields[name]:
                findings.append(
                    ValidationFinding(
                        severity,
                        table_name,
                        f"required column type mismatch: {name} expected {expected_fields[name]}, got {actual_fields[name]}",
                    )
                )

    for view_name, required_fields in CORE_VIEW_COLUMNS.items():
        checked += 1
        try:
            actual = client.get_table(settings.object_ref(view_name))
        except NotFound:
            findings.append(ValidationFinding("blocker", view_name, "view is missing"))
            continue
        if actual.table_type not in {"VIEW", "MATERIALIZED_VIEW"}:
            findings.append(ValidationFinding("blocker", view_name, "expected a view"))
            continue
        actual_names = {field.name for field in actual.schema}
        for name in sorted(required_fields - actual_names):
            findings.append(ValidationFinding("blocker", view_name, f"required column missing: {name}"))

    return ContractValidationReport(len(contracts), checked, tuple(findings))


def validate_b8_source_contracts(
    *, client: Any, config: AnalyticsConfig, root: Path | None = None
) -> ContractValidationReport:
    """Validate every copied B-8 source field, including nullable linkage."""
    if config.backend_name not in {"test", "production"}:
        raise ValueError("B-8 schema validation requires an approved backend")
    contracts = load_schema_contracts(root)
    names = (
        "telemetry_run_summary", "telemetry_run_start_snapshot",
        "telemetry_app_lifecycle_events",
    )
    findings: list[ValidationFinding] = []
    for table_name in names:
        try:
            actual = client.get_table(config.object_ref(table_name))
        except NotFound:
            findings.append(ValidationFinding("blocker", table_name, "object is missing"))
            continue
        if actual.table_type in {"VIEW", "MATERIALIZED_VIEW"}:
            findings.append(ValidationFinding("blocker", table_name, "expected a table"))
            continue
        live = {field.name: field for field in actual.schema}
        for field in contracts[table_name]:
            observed = live.get(field.name)
            if observed is None:
                findings.append(ValidationFinding(
                    "blocker", table_name, f"column missing: {field.name}"
                ))
                continue
            if _normalize_type(observed.field_type) != _normalize_type(field.field_type):
                findings.append(ValidationFinding(
                    "blocker", table_name, f"column type mismatch: {field.name}"
                ))
            if (observed.mode or "NULLABLE").upper() != field.mode:
                findings.append(ValidationFinding(
                    "blocker", table_name, f"column mode mismatch: {field.name}"
                ))
    return ContractValidationReport(len(contracts), len(names), tuple(findings))


def _flatten_live_schema(fields: Any, prefix: str = "") -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for field in fields:
        path = f"{prefix}.{field.name}" if prefix else field.name
        result[path] = (
            _normalize_type(field.field_type),
            (field.mode or "NULLABLE").upper(),
        )
        result.update(_flatten_live_schema(field.fields, path))
    return result


def validate_r3b_source_contracts(
    *, client: Any, config: AnalyticsConfig, ga_project: str = "bald-ops",
    ga_property_id: str = "538301722", ga_table_id: str | None = None,
    root: Path | None = None,
) -> ContractValidationReport:
    """Validate the custom lifecycle table and the fields R3-B reads from GA."""
    if config.backend_name != "production" or config.backend_environment != "Production":
        raise ValueError("GA_SOURCE_UNAVAILABLE_FOR_TEST")
    repo = root or repository_root()
    contracts = load_schema_contracts(repo)
    findings: list[ValidationFinding] = []
    custom_name = "telemetry_app_lifecycle_events"
    try:
        custom = client.get_table(config.object_ref(custom_name))
    except NotFound:
        findings.append(ValidationFinding("blocker", custom_name, "object is missing"))
    else:
        live = {field.name: field for field in custom.schema}
        for field in contracts[custom_name]:
            observed = live.get(field.name)
            if observed is None:
                findings.append(ValidationFinding("blocker", custom_name, f"column missing: {field.name}"))
            elif _normalize_type(observed.field_type) != _normalize_type(field.field_type):
                findings.append(ValidationFinding("blocker", custom_name, f"column type mismatch: {field.name}"))
            elif (observed.mode or "NULLABLE").upper() != field.mode:
                findings.append(ValidationFinding("blocker", custom_name, f"column mode mismatch: {field.name}"))

    ga_dataset = f"analytics_{ga_property_id}"
    if ga_table_id is None:
        candidates = sorted(
            item.table_id for item in client.list_tables(f"{ga_project}.{ga_dataset}")
            if item.table_id.startswith("events_")
        )
        ga_table_id = candidates[-1] if candidates else None
    ga_object = f"{ga_project}.{ga_dataset}.{ga_table_id or 'events_*'}"
    if ga_table_id is None:
        findings.append(ValidationFinding("blocker", ga_object, "no GA export event table exists"))
    else:
        try:
            ga_table = client.get_table(ga_object)
        except NotFound:
            findings.append(ValidationFinding("blocker", ga_object, "object is missing"))
        else:
            actual = _flatten_live_schema(ga_table.schema)
            contract_path = repo / "contracts" / "ga-bigquery-schema" / "app_foreground_source.json"
            expected = json.loads(contract_path.read_text(encoding="utf-8"))
            for field in expected:
                path = str(field["path"])
                observed = actual.get(path)
                if observed is None:
                    findings.append(ValidationFinding("blocker", ga_object, f"field missing: {path}"))
                    continue
                expected_type = _normalize_type(str(field["type"]))
                expected_mode = str(field["mode"]).upper()
                if observed[0] != expected_type:
                    findings.append(ValidationFinding("blocker", ga_object, f"field type mismatch: {path}"))
                if observed[1] != expected_mode:
                    findings.append(ValidationFinding("blocker", ga_object, f"field mode mismatch: {path}"))
    return ContractValidationReport(2, 2, tuple(findings))


def validate_r3d_source_contracts(
    *, client: Any, config: AnalyticsConfig, ga_project: str = "bald-ops",
    ga_property_id: str = "538301722", ga_table_id: str | None = None,
    root: Path | None = None,
) -> ContractValidationReport:
    """Validate app_remove fields plus the complete R3-B bridge dependency."""
    if config.backend_name != "production" or config.backend_environment != "Production":
        raise ValueError("GA_SOURCE_UNAVAILABLE_FOR_TEST")
    repo = root or repository_root()
    ga_dataset = f"analytics_{ga_property_id}"
    if ga_table_id is None:
        candidates = sorted(
            item.table_id for item in client.list_tables(f"{ga_project}.{ga_dataset}")
            if item.table_id.startswith("events_")
        )
        ga_table_id = candidates[-1] if candidates else None
    bridge = validate_r3b_source_contracts(
        client=client,
        config=config,
        ga_project=ga_project,
        ga_property_id=ga_property_id,
        ga_table_id=ga_table_id,
        root=repo,
    )
    findings = list(bridge.findings)
    ga_object = f"{ga_project}.{ga_dataset}.{ga_table_id or 'events_*'}"
    if ga_table_id is not None:
        try:
            ga_table = client.get_table(ga_object)
        except NotFound:
            if not any(
                item.object_name == ga_object and item.message == "object is missing"
                for item in findings
            ):
                findings.append(ValidationFinding("blocker", ga_object, "object is missing"))
        else:
            actual = _flatten_live_schema(ga_table.schema)
            contract_path = (
                repo / "contracts" / "ga-bigquery-schema" / "app_remove_source.json"
            )
            expected = json.loads(contract_path.read_text(encoding="utf-8"))
            for field in expected:
                path = str(field["path"])
                observed = actual.get(path)
                if observed is None:
                    findings.append(
                        ValidationFinding("blocker", ga_object, f"field missing: {path}")
                    )
                    continue
                expected_type = _normalize_type(str(field["type"]))
                expected_mode = str(field["mode"]).upper()
                if observed[0] != expected_type:
                    findings.append(ValidationFinding(
                        "blocker", ga_object, f"field type mismatch: {path}"
                    ))
                if observed[1] != expected_mode:
                    findings.append(ValidationFinding(
                        "blocker", ga_object, f"field mode mismatch: {path}"
                    ))
    return ContractValidationReport(3, 3, tuple(findings))


def validate_r3d_contracts(
    *, client: Any, config: AnalyticsConfig, ga_project: str = "bald-ops",
    ga_property_id: str = "538301722", ga_table_id: str | None = None,
    normal_bundle: Path | None = None, restricted_artifact: Path | None = None,
    root: Path | None = None,
) -> ContractValidationReport:
    """Validate R3-D sources, registry authority, privacy, and optional artifacts."""
    if (normal_bundle is None) != (restricted_artifact is None):
        raise ValueError("normal_bundle and restricted_artifact must be supplied together")
    source = validate_r3d_source_contracts(
        client=client, config=config, ga_project=ga_project,
        ga_property_id=ga_property_id, ga_table_id=ga_table_id, root=root,
    )
    findings = list(source.findings)
    from defence_project_analytics.metric_registry import (
        OBSERVED_UNINSTALL,
        OBSERVED_UNINSTALL_METRIC_SPECS,
        metric_authority,
    )
    for family, metric, *_ in OBSERVED_UNINSTALL_METRIC_SPECS:
        authority = metric_authority((OBSERVED_UNINSTALL, family, metric))
        if not (
            authority.known_readable
            and authority.evidence_eligible
            and not authority.comparison_eligible
            and not authority.decision_eligible
            and not authority.target_eligible
        ):
            findings.append(ValidationFinding(
                "blocker", "metricRegistry",
                f"invalid R3-D authority: {family}.{metric}",
            ))
    checked = source.checked_objects + 1
    parsed = source.parsed_contracts + 1
    if normal_bundle is not None and restricted_artifact is not None:
        normal = normal_bundle.resolve()
        restricted = restricted_artifact.resolve()
        required_normal = {
            "metadata.json", "metrics.json", "report.md", "manifest.json",
            "tables/source-summary.csv", "tables/attribution-summary.csv",
            "tables/mapping-age-summary.csv",
        }
        required_restricted = {
            "metadata.json", "manifest.json", "observed-uninstall-events.csv",
        }
        for label, directory, required in (
            ("normalBundle", normal, required_normal),
            ("restrictedArtifact", restricted, required_restricted),
        ):
            if not directory.is_dir():
                findings.append(ValidationFinding(
                    "blocker", label, f"directory is missing: {directory}"
                ))
                continue
            missing = sorted(
                item for item in required if not (directory / item).is_file()
            )
            for item in missing:
                findings.append(ValidationFinding(
                    "blocker", label, f"required artifact is missing: {item}"
                ))
            manifest_path = directory / "manifest.json"
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    files = manifest["files"]
                    if not isinstance(files, dict):
                        raise TypeError("files must be an object")
                    for relative, expected in files.items():
                        path = directory / str(relative)
                        actual = hashlib.sha256(path.read_bytes()).hexdigest()
                        if actual != expected:
                            findings.append(ValidationFinding(
                                "blocker", label, f"manifest digest mismatch: {relative}"
                            ))
                except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    findings.append(ValidationFinding(
                        "blocker", label, f"invalid manifest: {exc}"
                    ))
        if normal.is_dir() and restricted.is_dir():
            from defence_project_analytics.observed_uninstall import RAW_IDENTIFIER_FIELDS
            normal_text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in normal.rglob("*") if path.is_file()
            )
            for field in sorted(RAW_IDENTIFIER_FIELDS):
                if field in normal_text:
                    findings.append(ValidationFinding(
                        "blocker", "normalBundle", f"raw identifier field leaked: {field}"
                    ))
            event_path = restricted / "observed-uninstall-events.csv"
            if event_path.is_file():
                with event_path.open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                raw_values = {
                    str(row[field])
                    for row in rows
                    for field in RAW_IDENTIFIER_FIELDS.intersection(row)
                    if row.get(field)
                }
                if any(value in normal_text for value in raw_values):
                    findings.append(ValidationFinding(
                        "blocker", "normalBundle", "raw identifier value leaked"
                    ))
        checked += 2
        parsed += 2
    return ContractValidationReport(parsed, checked, tuple(findings))


def validate_r4a_contracts():
    """Validate the local-only R4-A policy contract without cloud access."""

    from defence_project_analytics.retention_evidence_policy import (
        validate_policy_contract,
    )

    return validate_policy_contract()


def validate_r4c_contracts(
    normal_bundle: Path | None = None,
) -> ContractValidationReport:
    """Validate the local R4-C registry, C-1, C-2, and optional bundle boundary."""

    from defence_project_analytics.brief.adapters import (
        adapt_single_bundle,
        assign_evidence_ids,
    )
    from defence_project_analytics.brief.loader import load_source_bundle
    from defence_project_analytics.brief.models import DOMAIN_ORDER, SINGLE_MODE
    from defence_project_analytics.llm_analysis.models import KNOWN_ANALYSES
    from defence_project_analytics.metric_registry import (
        RETENTION_EVIDENCE,
        comparison_metric_keys,
        decision_metric_keys,
        evidence_metric_keys,
        known_metric_keys,
        monitor_metric_keys,
        target_metric_keys,
    )
    from defence_project_analytics.retention_evidence import (
        validate_retention_evidence_bundle,
    )

    findings: list[ValidationFinding] = []
    r4_known = {key for key in known_metric_keys() if key[0] == RETENTION_EVIDENCE}
    r4_evidence = {key for key in evidence_metric_keys() if key[0] == RETENTION_EVIDENCE}
    r4_comparison = {
        key for key in comparison_metric_keys() if key[0] == RETENTION_EVIDENCE
    }
    if len(r4_known) != 11 or r4_evidence != r4_known:
        findings.append(ValidationFinding(
            "blocker", "metricRegistry",
            "R4 must expose exactly 11 known/readable factual metrics",
        ))
    if len(r4_comparison) != 6 or monitor_metric_keys() != r4_comparison:
        findings.append(ValidationFinding(
            "blocker", "metricRegistry",
            "R4 must expose exactly six static MonitorOnly comparison metrics",
        ))
    if any(key[0] == RETENTION_EVIDENCE for key in decision_metric_keys()):
        findings.append(ValidationFinding(
            "blocker", "metricRegistry", "R4 decision authority must remain denied",
        ))
    if any(key[0] == RETENTION_EVIDENCE for key in target_metric_keys()):
        findings.append(ValidationFinding(
            "blocker", "metricRegistry", "R4 target authority must remain denied",
        ))
    if RETENTION_EVIDENCE not in DOMAIN_ORDER or RETENTION_EVIDENCE not in KNOWN_ANALYSES:
        findings.append(ValidationFinding(
            "blocker", "c1C2Registration", "retentionEvidence is not registered end-to-end",
        ))

    checked = 4
    parsed = 4
    if normal_bundle is not None:
        validation = validate_retention_evidence_bundle(normal_bundle)
        if not validation["ready"]:
            findings.append(ValidationFinding(
                "blocker", "normalBundle", "; ".join(validation["findings"]),
            ))
        try:
            loaded = load_source_bundle(normal_bundle)
            evidence = assign_evidence_ids(adapt_single_bundle(loaded, SINGLE_MODE))
            if len(evidence) != 11:
                raise ValueError("expected exactly 11 registered R4 evidence items")
            for item in evidence:
                authority = item.authority
                if (
                    authority.get("factualEligible") is not True
                    or authority.get("decisionEligible") is not False
                    or authority.get("targetEligible") is not False
                    or authority.get("guardrailEligible") is not False
                    or authority.get("rollbackEligible") is not False
                ):
                    raise ValueError("authority projection is incomplete or permissive")
        except Exception as exc:
            findings.append(ValidationFinding(
                "blocker", "c1RetentionEvidence", str(exc),
            ))
        try:
            from defence_project_analytics.analysis_brief import (
                AnalysisBriefRequest,
                generate_analysis_brief,
            )
            from defence_project_analytics.llm_analysis import (
                AnalysisPromptRequest,
                build_analysis_prompt,
            )
            from defence_project_analytics.llm_analysis.anthropic_transport import (
                build_compact_llm_payload,
                expand_compact_evidence_items,
            )

            with tempfile.TemporaryDirectory(prefix="r4c-contract-") as temp_name:
                temp_root = Path(temp_name)
                brief = generate_analysis_brief(
                    AnalysisBriefRequest(
                        mode="singleVersion",
                        source_report_paths=(normal_bundle,),
                    ),
                    output_root=temp_root,
                    workspace_root=normal_bundle.parent,
                )
                package = build_analysis_prompt(
                    AnalysisPromptRequest(
                        brief,
                        analysis_objective="Validate the R4-C authority boundary.",
                        output_language="en",
                    ),
                    workspace_root=temp_root,
                )
                compact = build_compact_llm_payload(package)
                if tuple(expand_compact_evidence_items(compact)) != tuple(
                    package.source.evidence["evidenceItems"]
                ):
                    raise ValueError("provider authority projection round-trip mismatch")
                if any(
                    "authority" not in item
                    for item in compact.get("evidence", ())
                    if item.get("domain") == RETENTION_EVIDENCE
                ):
                    raise ValueError("provider projection lost R4 authority metadata")
        except Exception as exc:
            findings.append(ValidationFinding(
                "blocker", "providerProjection", str(exc),
            ))
        checked += 3
        parsed += 3
    return ContractValidationReport(parsed, checked, tuple(findings))
