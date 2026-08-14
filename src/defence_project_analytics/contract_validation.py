"""Local schema parsing and non-destructive live BigQuery contract validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
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
