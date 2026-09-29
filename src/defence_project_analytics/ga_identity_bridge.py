"""R3-B factual GA-to-profile temporal identity bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from defence_project_analytics.bigquery_client import dry_run_query, query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QuerySpec
from defence_project_analytics.reporting.models import (
    REPORT_CONTRACT_VERSION,
    GaIdentityBridgeScope,
    ReportBundle,
    ReportMetadata,
    ReportWarning,
)
from defence_project_analytics.reporting.renderers import render_csv, render_json, to_external
from defence_project_analytics.reporting.writer import scope_hash, write_report_bundle
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_TYPE = "gaIdentityBridge"
ANALYSIS_VERSION = "1.0.0"
DEFAULT_MAXIMUM_TOTAL_BYTES = 1_000_000_000
DEFAULT_GA_PROJECT = "bald-ops"
DEFAULT_GA_PROPERTY_ID = "538301722"
DEFAULT_GA_STREAM_ID = "14908341790"
DEFAULT_GA_EXPORT_START_DATE = date(2026, 9, 28)
DEFAULT_GA_BRIDGE_OBSERVATION_START_UTC = datetime(
    2026, 9, 28, 11, 10, 5, tzinfo=timezone.utc
)
SQL_FILE = "sql/analysis/ga_identity_bridge_observations_v1.sql"
SQL_MARKER = "-- @include ga_source_union_v1"

RESTRICTED_OBSERVATION_COLUMNS = (
    "gaProject", "gaPropertyId", "gaStreamId", "telemetryBackend",
    "environment", "observedAtUtc", "telemetryPlayerId",
    "retentionBridgeId", "userPseudoId", "lifecycleOccurrenceId",
    "mappingStatus", "sourceTableDate", "sourceTableKind",
    "sourceFinalizationState", "contentVersion", "releaseId",
    "isDevelopmentBuild", "physicalCount", "exactDuplicateRowsRemoved",
)
RESTRICTED_INTERVAL_COLUMNS = (
    "gaProject", "gaPropertyId", "gaStreamId", "telemetryBackend",
    "environment", "userPseudoId", "telemetryPlayerId",
    "retentionBridgeId", "validFromUtc", "validUntilUtc",
    "observationCount", "firstObservedAtUtc", "lastObservedAtUtc",
    "sourceFinalizationState",
)
TABLE_SPECS = {
    "mapping-quality-summary.csv": (
        ("metric", "count", "denominator", "ratio"), ("metric",),
    ),
    "source-summary.csv": (
        ("sourceTableKind", "finalizationState", "selectedTableCount",
         "foregroundPhysicalRows", "logicalObservations"),
        ("sourceTableKind", "finalizationState"),
    ),
    "conflict-summary.csv": (
        ("mappingStatus", "count"), ("mappingStatus",),
    ),
}
RAW_IDENTIFIER_FIELDS = frozenset({
    "telemetryPlayerId", "retentionBridgeId", "user_id",
    "user_pseudo_id", "userPseudoId", "lifecycleOccurrenceId",
})


@dataclass(frozen=True, slots=True)
class GaSourceTable:
    source_date: date
    table_id: str
    kind: str
    finalization_state: str


@dataclass(frozen=True, slots=True)
class GaIdentityBridgeRequest:
    telemetry_backend: str
    environment: str
    ga_project: str
    ga_property_id: str
    ga_stream_id: str
    analysis_as_of_utc: datetime
    observation_start_utc: datetime = DEFAULT_GA_BRIDGE_OBSERVATION_START_UTC
    ga_date_start: date = DEFAULT_GA_EXPORT_START_DATE
    ga_date_end: date | None = None

    def __post_init__(self) -> None:
        if self.telemetry_backend not in {"production", "test"}:
            raise ValueError("telemetry_backend must be production or test")
        if self.environment not in {"Production", "Test"}:
            raise ValueError("environment must be Production or Test")
        if self.analysis_as_of_utc.tzinfo is None or self.analysis_as_of_utc.utcoffset() is None:
            raise ValueError("analysis_as_of_utc must be timezone-aware")
        if self.observation_start_utc.tzinfo is None or self.observation_start_utc.utcoffset() is None:
            raise ValueError("observation_start_utc must be timezone-aware")
        if self.observation_start_utc >= self.analysis_as_of_utc:
            raise ValueError("observation_start_utc must precede analysis_as_of_utc")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.ga_project):
            raise ValueError("invalid GA project")
        if not re.fullmatch(r"[0-9]+", self.ga_property_id):
            raise ValueError("invalid GA property ID")
        if not re.fullmatch(r"[0-9]+", self.ga_stream_id):
            raise ValueError("invalid GA stream ID")
        end = self.ga_date_end or self.analysis_as_of_utc.astimezone(timezone.utc).date()
        if self.ga_date_start > end:
            raise ValueError("ga_date_start must not follow ga_date_end")
        object.__setattr__(self, "ga_date_end", end)
        if self.telemetry_backend == "test" or self.environment == "Test":
            raise ValueError("GA_SOURCE_UNAVAILABLE_FOR_TEST")
        if self.telemetry_backend != "production" or self.environment != "Production":
            raise ValueError("Production GA bridge requires the Production telemetry backend")

    @property
    def ga_dataset(self) -> str:
        return f"analytics_{self.ga_property_id}"

    def scope(self) -> GaIdentityBridgeScope:
        return GaIdentityBridgeScope(
            environment=self.environment,
            telemetry_backend=self.telemetry_backend,
            ga_project=self.ga_project,
            ga_property_id=self.ga_property_id,
            ga_stream_id=self.ga_stream_id,
            ga_dataset=self.ga_dataset,
            ga_date_start=self.ga_date_start,
            ga_date_end=self.ga_date_end,
            analysis_as_of_utc=self.analysis_as_of_utc,
            observation_start_utc=self.observation_start_utc,
        )


@dataclass(frozen=True, slots=True)
class GaIdentityBridgeAnalysis:
    bundle: ReportBundle
    observations: pd.DataFrame
    intervals: pd.DataFrame
    selected_tables: tuple[GaSourceTable, ...]
    dry_run_estimated_bytes: int


@dataclass(frozen=True, slots=True)
class GaIdentityBridgeOutput:
    report_path: Path
    restricted_path: Path
    analysis: GaIdentityBridgeAnalysis


def select_ga_source_tables(
    table_ids: Iterable[str], *, start: date, end: date,
) -> tuple[GaSourceTable, ...]:
    """Choose finalized daily over provisional intraday independently per date."""
    available = set(table_ids)
    selected: list[GaSourceTable] = []
    current = start
    while current <= end:
        suffix = current.strftime("%Y%m%d")
        daily = f"events_{suffix}"
        intraday = f"events_intraday_{suffix}"
        if daily in available:
            selected.append(GaSourceTable(current, daily, "Daily", "Final"))
        elif intraday in available:
            selected.append(
                GaSourceTable(current, intraday, "Intraday", "Provisional")
            )
        current += timedelta(days=1)
    return tuple(selected)


def resolve_ga_source_tables(
    request: GaIdentityBridgeRequest, *, client: Any,
) -> tuple[GaSourceTable, ...]:
    dataset = f"{request.ga_project}.{request.ga_dataset}"
    table_ids = (item.table_id for item in client.list_tables(dataset))
    selected = select_ga_source_tables(
        table_ids, start=request.ga_date_start, end=request.ga_date_end,
    )
    if not selected:
        raise RuntimeError("No GA export table exists in the requested source-date range")
    return selected


def _ga_source_union(request: GaIdentityBridgeRequest, tables: Sequence[GaSourceTable]) -> str:
    parts: list[str] = []
    for item in tables:
        if not re.fullmatch(r"events_(?:intraday_)?[0-9]{8}", item.table_id):
            raise ValueError(f"Unsafe GA table identifier: {item.table_id}")
        reference = f"{request.ga_project}.{request.ga_dataset}.{item.table_id}"
        parts.append(
            "SELECT *, "
            f"DATE '{item.source_date.isoformat()}' AS sourceTableDate, "
            f"'{item.kind}' AS sourceTableKind, "
            f"'{item.finalization_state}' AS sourceFinalizationState "
            f"FROM `{reference}`"
        )
    return "\n  UNION ALL\n  ".join(parts)


def build_query(
    request: GaIdentityBridgeRequest, *, config: AnalyticsConfig,
    selected_tables: Sequence[GaSourceTable],
) -> QuerySpec:
    config.require_environment(request.environment)
    if config.backend_name != request.telemetry_backend:
        raise ValueError("telemetry backend selection does not match request")
    sql = load_sql(SQL_FILE, config=config)
    if sql.count(SQL_MARKER) != 1:
        raise ValueError("R3-B SQL source marker missing or duplicated")
    sql = sql.replace(SQL_MARKER, _ga_source_union(request, selected_tables))
    parameters: dict[str, Any] = {
        "analysis_as_of_utc": request.analysis_as_of_utc,
        "observation_start_utc": request.observation_start_utc,
        "environment": request.environment,
        "ga_project": request.ga_project,
        "ga_property_id": request.ga_property_id,
        "ga_stream_id": request.ga_stream_id,
        "telemetry_backend": request.telemetry_backend,
    }
    if named_parameter_names(sql) != parameters.keys():
        raise ValueError("R3-B SQL parameter contract mismatch")
    return QuerySpec(sql, parameters)


def _require_columns(frame: pd.DataFrame, columns: Sequence[str]) -> None:
    missing = [item for item in columns if item not in frame.columns]
    if missing:
        raise ValueError(f"R3-B result is missing columns: {', '.join(missing)}")


def materialize_intervals(observations: pd.DataFrame) -> pd.DataFrame:
    _require_columns(observations, RESTRICTED_OBSERVATION_COLUMNS)
    mapped = observations[observations["mappingStatus"] == "Mapped"].copy()
    if mapped.empty:
        return pd.DataFrame(columns=RESTRICTED_INTERVAL_COLUMNS)
    mapped["observedAtUtc"] = pd.to_datetime(mapped["observedAtUtc"], utc=True)
    mapped = mapped.sort_values(
        ["userPseudoId", "observedAtUtc", "lifecycleOccurrenceId"],
        kind="mergesort",
    ).reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    for _, pseudo_rows in mapped.groupby("userPseudoId", sort=False, dropna=False):
        groups: list[pd.DataFrame] = []
        start = 0
        pair = None
        for index, row in pseudo_rows.reset_index(drop=True).iterrows():
            current = (row["telemetryPlayerId"], row["retentionBridgeId"])
            if pair is None:
                pair = current
            elif current != pair:
                groups.append(pseudo_rows.reset_index(drop=True).iloc[start:index])
                start = index
                pair = current
        groups.append(pseudo_rows.reset_index(drop=True).iloc[start:])
        for index, group in enumerate(groups):
            first = group.iloc[0]
            next_start = groups[index + 1].iloc[0]["observedAtUtc"] if index + 1 < len(groups) else None
            states = set(group["sourceFinalizationState"].dropna().astype(str))
            finalization = "Final" if states == {"Final"} else "Provisional"
            rows.append({
                "gaProject": first["gaProject"],
                "gaPropertyId": first["gaPropertyId"],
                "gaStreamId": first["gaStreamId"],
                "telemetryBackend": first["telemetryBackend"],
                "environment": first["environment"],
                "userPseudoId": first["userPseudoId"],
                "telemetryPlayerId": first["telemetryPlayerId"],
                "retentionBridgeId": first["retentionBridgeId"],
                "validFromUtc": first["observedAtUtc"],
                "validUntilUtc": next_start,
                "observationCount": len(group),
                "firstObservedAtUtc": group["observedAtUtc"].min(),
                "lastObservedAtUtc": group["observedAtUtc"].max(),
                "sourceFinalizationState": finalization,
            })
    return pd.DataFrame(rows, columns=RESTRICTED_INTERVAL_COLUMNS)


def resolve_temporal_profile(
    intervals: pd.DataFrame, *, user_pseudo_id: str, event_at_utc: datetime,
) -> str:
    """R3-D handoff classification without returning a raw profile identifier."""
    if event_at_utc.tzinfo is None or event_at_utc.utcoffset() is None:
        raise ValueError("event_at_utc must be timezone-aware")
    if intervals.empty:
        return "Unmapped"
    frame = intervals[intervals["userPseudoId"] == user_pseudo_id].copy()
    if frame.empty:
        return "Unmapped"
    starts = pd.to_datetime(frame["validFromUtc"], utc=True)
    ends = pd.to_datetime(frame["validUntilUtc"], utc=True)
    instant = pd.Timestamp(event_at_utc).tz_convert("UTC")
    matches = frame[(starts <= instant) & (ends.isna() | (ends > instant))]
    if len(matches) == 1:
        return "Mapped"
    return "Unmapped" if matches.empty else "Ambiguous"


def _count_status(observations: pd.DataFrame, status: str) -> int:
    return int((observations["mappingStatus"] == status).sum())


def assemble_bundle(
    request: GaIdentityBridgeRequest,
    observations: pd.DataFrame,
    intervals: pd.DataFrame,
    selected_tables: Sequence[GaSourceTable],
    *, estimated_bytes: int,
    generated_at_utc: datetime,
) -> ReportBundle:
    _require_columns(observations, RESTRICTED_OBSERVATION_COLUMNS)
    logical = len(observations)
    mapped = _count_status(observations, "Mapped")
    if mapped + int((observations["mappingStatus"] != "Mapped").sum()) != logical:
        raise ValueError("R3-B classification invariant failed")
    mapped_rows = observations[observations["mappingStatus"] == "Mapped"]
    physical = int(pd.to_numeric(observations["physicalCount"]).sum()) if logical else 0
    duplicate_rows = int(pd.to_numeric(observations["exactDuplicateRowsRemoved"]).sum()) if logical else 0
    if physical - duplicate_rows != logical:
        raise ValueError("R3-B exact-duplicate invariant failed")
    mapped_rate = mapped / logical if logical else None
    switch_count = max(0, len(intervals) - int(intervals["userPseudoId"].nunique())) if not intervals.empty else 0
    multi_pseudo = 0
    if not mapped_rows.empty:
        pair_counts = mapped_rows.assign(
            _pair=mapped_rows["telemetryPlayerId"].astype(str) + ":" + mapped_rows["retentionBridgeId"].astype(str)
        ).groupby("userPseudoId")["_pair"].nunique()
        multi_pseudo = int((pair_counts > 1).sum())
    statuses = (
        "UnmappedMissingUserId", "UnmappedMissingPseudoId",
        "UnmappedMissingOccurrence", "UnmatchedGaOccurrence", "CustomConflict",
        "GaConflict", "TemporalMappingConflict", "DurableIdentityConflict",
        "InvalidEnvironment", "InvalidIdentifier", "InvalidContext",
    )
    status_counts = {item: _count_status(observations, item) for item in statuses}
    unmapped = logical - mapped
    metrics = {
        "source": {
            "gaForegroundEvents": logical,
            "dailyRows": int(observations.loc[observations["sourceTableKind"] == "Daily", "physicalCount"].sum()),
            "intradayRows": int(observations.loc[observations["sourceTableKind"] == "Intraday", "physicalCount"].sum()),
            "selectedDailyTables": sum(item.kind == "Daily" for item in selected_tables),
            "selectedIntradayTables": sum(item.kind == "Intraday" for item in selected_tables),
        },
        "mapping": {
            "mappedCount": mapped,
            "mappedRate": {"count": mapped, "denominator": logical, "ratio": mapped_rate},
            "unmappedCount": unmapped,
            "missingUserIdCount": status_counts["UnmappedMissingUserId"],
            "missingPseudoIdCount": status_counts["UnmappedMissingPseudoId"],
            "missingOccurrenceCount": status_counts["UnmappedMissingOccurrence"],
            "unmatchedGaCount": status_counts["UnmatchedGaOccurrence"],
            "customConflictCount": status_counts["CustomConflict"],
            "gaConflictCount": status_counts["GaConflict"],
            "temporalConflictCount": status_counts["TemporalMappingConflict"],
            "durableIdentityConflictCount": status_counts["DurableIdentityConflict"],
            "invalidEnvironmentCount": status_counts["InvalidEnvironment"],
            "invalidIdentifierCount": status_counts["InvalidIdentifier"],
            "invalidContextCount": status_counts["InvalidContext"],
            "distinctPseudoCount": int(mapped_rows["userPseudoId"].nunique()),
            "distinctTelemetryPlayerCount": int(mapped_rows["telemetryPlayerId"].nunique()),
            "distinctRetentionBridgeCount": int(mapped_rows["retentionBridgeId"].nunique()),
            "multiProfilePseudoCount": multi_pseudo,
            "profileSwitchObservationCount": switch_count,
        },
        "dataQuality": {
            "physicalRows": physical,
            "logicalObservations": logical,
            "exactDuplicateRowsRemoved": duplicate_rows,
            "restrictedIntervalCount": len(intervals),
        },
    }
    quality_rows = [
        {"metric": "mapped", "count": mapped, "denominator": logical, "ratio": mapped_rate},
        {"metric": "unmapped", "count": unmapped, "denominator": logical,
         "ratio": unmapped / logical if logical else None},
        {"metric": "exactDuplicateRowsRemoved", "count": duplicate_rows,
         "denominator": physical, "ratio": duplicate_rows / physical if physical else None},
    ]
    for status, count in status_counts.items():
        quality_rows.append({"metric": status, "count": count, "denominator": logical,
                             "ratio": count / logical if logical else None})
    source_rows = []
    for kind, state in (("Daily", "Final"), ("Intraday", "Provisional")):
        selected_count = sum(item.kind == kind for item in selected_tables)
        source_frame = observations[observations["sourceTableKind"] == kind]
        source_rows.append({
            "sourceTableKind": kind,
            "finalizationState": state,
            "selectedTableCount": selected_count,
            "foregroundPhysicalRows": int(source_frame["physicalCount"].sum()),
            "logicalObservations": len(source_frame),
        })
    conflict_rows = [
        {"mappingStatus": status, "count": count}
        for status, count in status_counts.items() if "Conflict" in status
    ]
    warnings = tuple(
        ReportWarning(code, message) for condition, code, message in (
            (logical == 0, "NO_GA_FOREGROUND_OBSERVATIONS", "No GA foreground observations matched the source range."),
            (mapped == 0, "NO_MAPPED_PROFILE_OBSERVATIONS", "No exact canonical profile mapping was established."),
            (any(item.kind == "Intraday" for item in selected_tables), "PROVISIONAL_GA_SOURCE", "At least one source date uses a provisional intraday table."),
            (sum(status_counts[item] for item in statuses if "Conflict" in item) > 0, "IDENTITY_MAPPING_CONFLICT", "Conflicting identity observations were excluded from intervals."),
        ) if condition
    )
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION,
        ANALYSIS_TYPE,
        ANALYSIS_VERSION,
        generated_at_utc,
        request.scope(),
        {"gaForegroundEvents": logical, "mappedObservations": mapped,
         "unmappedObservations": unmapped},
        {"physicalRows": physical, "logicalObservations": logical,
         "exactDuplicateRowsRemoved": duplicate_rows},
        {
            "observationSemantics": "A point-in-time exact lifecycle bridge observation",
            "intervalSemantics": "Consecutive identical profile mappings reinforce one interval",
            "identityAuthority": "Temporal factual infrastructure only",
            "uninstallMeaning": False,
        },
        warnings,
        estimated_bytes,
    )
    markdown = (
        "# GA Temporal Identity Bridge Report\n\n"
        f"- GA foreground observations: {logical}\n"
        f"- Exact mapped observations: {mapped}\n"
        f"- Unmapped or conflicting observations: {unmapped}\n"
        f"- Profile-switch observations: {switch_count}\n"
        f"- Multi-profile app instances: {multi_pseudo}\n\n"
        "This report contains aggregate mapping coverage and quality only. "
        "The separately controlled restricted artifact contains the temporal relation. "
        "The relation is factual infrastructure and does not establish permanent ownership, churn, or uninstall.\n"
    )
    return ReportBundle(metadata, metrics, markdown, {
        "mapping-quality-summary.csv": pd.DataFrame(quality_rows),
        "source-summary.csv": pd.DataFrame(source_rows),
        "conflict-summary.csv": pd.DataFrame(conflict_rows),
    })


def _raw_values(observations: pd.DataFrame) -> tuple[str, ...]:
    values: set[str] = set()
    for column in RAW_IDENTIFIER_FIELDS.intersection(observations.columns):
        values.update(str(item) for item in observations[column].dropna() if str(item))
    return tuple(sorted(values))


def assert_normal_bundle_privacy(bundle: ReportBundle, observations: pd.DataFrame) -> None:
    serialized = "\n".join((
        render_json(bundle.metadata), render_json(bundle.metrics), bundle.markdown,
        *(render_csv(value, columns=value.columns) for value in bundle.tables.values()),
    ))
    for field in RAW_IDENTIFIER_FIELDS:
        if field in serialized:
            raise ValueError(f"Raw identifier field escaped into normal bundle: {field}")
    for value in _raw_values(observations):
        if value in serialized:
            raise ValueError("Raw identifier value escaped into normal bundle")


def _write_restricted_artifact(
    observations: pd.DataFrame,
    intervals: pd.DataFrame,
    request: GaIdentityBridgeRequest,
    *, output_root: Path,
    overwrite: bool,
) -> Path:
    target = output_root / "ga-profile-temporal-map" / scope_hash(request.scope())
    if target.exists() and not overwrite:
        raise FileExistsError(f"Restricted artifact target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    backup: Path | None = None
    try:
        observation_text = render_csv(
            observations, columns=RESTRICTED_OBSERVATION_COLUMNS,
            sort_by=("observedAtUtc", "userPseudoId", "lifecycleOccurrenceId"),
        )
        interval_text = render_csv(
            intervals, columns=RESTRICTED_INTERVAL_COLUMNS,
            sort_by=("userPseudoId", "validFromUtc"),
        )
        (temporary / "mapping-observations.csv").write_text(
            observation_text, encoding="utf-8", newline="\n"
        )
        (temporary / "mapping-intervals.csv").write_text(
            interval_text, encoding="utf-8", newline="\n"
        )
        metadata = {
            "artifactType": "restrictedGaProfileTemporalMap",
            "contractVersion": ANALYSIS_VERSION,
            "classification": "RestrictedRawIdentity",
            "permittedUse": "R3-D temporal lookup and restricted diagnostics",
            "forbiddenUse": "normal report, C-1 evidence, C-2 context, public analysis",
            "scope": to_external(request.scope()),
        }
        (temporary / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
        files = sorted(path for path in temporary.iterdir() if path.is_file())
        manifest = {
            "artifactType": metadata["artifactType"],
            "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
        for path in temporary.iterdir():
            if path.is_file():
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
        if target.exists():
            backup = target.with_name(f".{target.name}.backup-{os.getpid()}")
            target.rename(backup)
        temporary.rename(target)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return target
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise


def analyze_ga_identity_bridge(
    request: GaIdentityBridgeRequest, *, config: AnalyticsConfig, client: Any,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
) -> GaIdentityBridgeAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be nonnegative")
    selected, query, estimate = estimate_ga_identity_bridge_bytes(
        request, config=config, client=client
    )
    if estimate > maximum_total_bytes:
        raise RuntimeError(
            f"R3-B dry-run estimate {estimate:,} bytes exceeds maximum "
            f"{maximum_total_bytes:,}; no analysis query was executed"
        )
    observations = query_dataframe(
        query, client=client, config=config, maximum_bytes_billed=maximum_total_bytes
    )
    _require_columns(observations, RESTRICTED_OBSERVATION_COLUMNS)
    intervals = materialize_intervals(observations)
    bundle = assemble_bundle(
        request, observations, intervals, selected,
        estimated_bytes=estimate, generated_at_utc=datetime.now(timezone.utc),
    )
    assert_normal_bundle_privacy(bundle, observations)
    return GaIdentityBridgeAnalysis(bundle, observations, intervals, selected, estimate)


def estimate_ga_identity_bridge_bytes(
    request: GaIdentityBridgeRequest, *, config: AnalyticsConfig, client: Any,
) -> tuple[tuple[GaSourceTable, ...], QuerySpec, int]:
    selected = resolve_ga_source_tables(request, client=client)
    query = build_query(request, config=config, selected_tables=selected)
    estimate = dry_run_query(query, client=client, config=config).total_bytes_processed
    return selected, query, estimate


def generate_ga_identity_bridge_report(
    request: GaIdentityBridgeRequest, *, config: AnalyticsConfig, client: Any,
    output_root: Path = Path("reports/generated"),
    restricted_output_root: Path = Path("reports/restricted"),
    overwrite: bool = False,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
) -> GaIdentityBridgeOutput:
    analysis = analyze_ga_identity_bridge(
        request, config=config, client=client,
        maximum_total_bytes=maximum_total_bytes,
    )
    restricted = _write_restricted_artifact(
        analysis.observations, analysis.intervals, request,
        output_root=restricted_output_root, overwrite=overwrite,
    )
    try:
        report = write_report_bundle(
            analysis.bundle, output_root=output_root, overwrite=overwrite,
            table_specs=TABLE_SPECS, include_manifest=True,
        )
    except Exception:
        if restricted.exists():
            shutil.rmtree(restricted)
        raise
    return GaIdentityBridgeOutput(report, restricted, analysis)
