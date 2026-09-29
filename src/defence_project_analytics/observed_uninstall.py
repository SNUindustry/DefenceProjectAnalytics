"""R3-D factual GA app_remove observation and temporal Profile attribution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Sequence

import pandas as pd

from defence_project_analytics.bigquery_client import dry_run_query, query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.ga_identity_bridge import (
    ANALYSIS_TYPE as BRIDGE_ANALYSIS_TYPE,
    ANALYSIS_VERSION as BRIDGE_ANALYSIS_VERSION,
    DEFAULT_GA_BRIDGE_OBSERVATION_START_UTC,
    DEFAULT_GA_EXPORT_START_DATE,
    DEFAULT_GA_PROJECT,
    DEFAULT_GA_PROPERTY_ID,
    DEFAULT_GA_STREAM_ID,
    DEFAULT_MAXIMUM_TOTAL_BYTES,
    GaIdentityBridgeRequest,
    GaSourceTable,
    build_query as build_bridge_query,
    resolve_ga_source_tables,
)
from defence_project_analytics.reporting.models import (
    REPORT_CONTRACT_VERSION,
    ObservedUninstallScope,
    ReportBundle,
    ReportMetadata,
    ReportWarning,
)
from defence_project_analytics.reporting.renderers import (
    render_csv,
    render_json,
    to_external,
)
from defence_project_analytics.reporting.writer import scope_hash, write_report_bundle
from defence_project_analytics.models import QuerySpec
from defence_project_analytics.sql_loader import load_sql, named_parameter_names


ANALYSIS_TYPE = "observedUninstall"
ANALYSIS_VERSION = "1.0.0"
SQL_FILE = "sql/analysis/observed_uninstall_events_v1.sql"
SQL_MARKER = "-- @include ga_source_union_v1"

APP_REMOVE_COLUMNS = (
    "gaProject", "gaPropertyId", "gaStreamId", "telemetryBackend", "environment",
    "eventTimestampUtc", "userPseudoId", "gaUserId", "rawEventStatus",
    "sourceTableDate", "sourceTableKind", "sourceFinalizationState",
    "eventBundleSequenceId", "batchEventIndex", "batchOrderingId",
    "physicalCount", "variantCount", "exactDuplicateRowsRemoved",
)
ATTRIBUTED_EVENT_COLUMNS = (
    *APP_REMOVE_COLUMNS,
    "attributionStatus", "attributionReason", "telemetryPlayerId",
    "retentionBridgeId", "bridgeObservedAtUtc", "mappingAgeSeconds",
)
BRIDGE_REQUIRED_COLUMNS = (
    "observedAtUtc", "userPseudoId", "telemetryPlayerId",
    "retentionBridgeId", "mappingStatus",
)
RAW_IDENTIFIER_FIELDS = frozenset({
    "userPseudoId", "gaUserId", "user_id", "user_pseudo_id",
    "telemetryPlayerId", "retentionBridgeId", "lifecycleOccurrenceId",
})
BRIDGE_CONFLICT_STATUSES = frozenset({
    "TemporalMappingConflict", "DurableIdentityConflict", "GaConflict", "CustomConflict",
})
TABLE_SPECS = {
    "source-summary.csv": (
        ("sourceTableKind", "finalizationState", "selectedTableCount",
         "appRemovePhysicalRows", "logicalEvents"),
        ("sourceTableKind",),
    ),
    "attribution-summary.csv": (
        ("attributionStatus", "attributionReason", "count", "denominator", "ratio"),
        ("attributionStatus", "attributionReason"),
    ),
    "mapping-age-summary.csv": (
        ("distribution", "mappedCount", "p50Seconds", "p95Seconds",
         "minSeconds", "maxSeconds"),
        ("distribution",),
    ),
}


@dataclass(frozen=True, slots=True)
class ObservedUninstallRequest:
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
        bridge = self.bridge_request()
        object.__setattr__(self, "ga_date_end", bridge.ga_date_end)

    @property
    def ga_dataset(self) -> str:
        return f"analytics_{self.ga_property_id}"

    def bridge_request(self) -> GaIdentityBridgeRequest:
        return GaIdentityBridgeRequest(
            telemetry_backend=self.telemetry_backend,
            environment=self.environment,
            ga_project=self.ga_project,
            ga_property_id=self.ga_property_id,
            ga_stream_id=self.ga_stream_id,
            analysis_as_of_utc=self.analysis_as_of_utc,
            observation_start_utc=self.observation_start_utc,
            ga_date_start=self.ga_date_start,
            ga_date_end=self.ga_date_end,
        )

    def scope(self) -> ObservedUninstallScope:
        return ObservedUninstallScope(
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
class ObservedUninstallAnalysis:
    bundle: ReportBundle
    events: pd.DataFrame
    bridge_observations: pd.DataFrame
    selected_tables: tuple[GaSourceTable, ...]
    dry_run_estimated_bytes: int


@dataclass(frozen=True, slots=True)
class ObservedUninstallOutput:
    report_path: Path
    restricted_path: Path
    analysis: ObservedUninstallAnalysis


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = [item for item in columns if item not in frame.columns]
    if missing:
        raise ValueError(f"{label} is missing columns: {', '.join(missing)}")


def _ga_source_union(request: ObservedUninstallRequest, tables: Sequence[GaSourceTable]) -> str:
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


def build_app_remove_query(
    request: ObservedUninstallRequest, *, config: AnalyticsConfig,
    selected_tables: Sequence[GaSourceTable],
) -> QuerySpec:
    config.require_environment(request.environment)
    if config.backend_name != request.telemetry_backend:
        raise ValueError("telemetry backend selection does not match request")
    sql = load_sql(SQL_FILE, config=config)
    if sql.count(SQL_MARKER) != 1:
        raise ValueError("R3-D SQL source marker missing or duplicated")
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
        raise ValueError("R3-D SQL parameter contract mismatch")
    return QuerySpec(sql, parameters)


def _empty_raw_identifier_values(*frames: pd.DataFrame) -> tuple[str, ...]:
    values: set[str] = set()
    for frame in frames:
        for column in RAW_IDENTIFIER_FIELDS.intersection(frame.columns):
            values.update(str(item) for item in frame[column].dropna() if str(item))
    return tuple(sorted(values))


def attribute_app_remove_events(
    app_remove_events: pd.DataFrame,
    bridge_observations: pd.DataFrame,
) -> pd.DataFrame:
    """Apply latest-at-or-before R3-B observations without using GA user_id."""
    _require_columns(app_remove_events, APP_REMOVE_COLUMNS, "R3-D app_remove result")
    _require_columns(bridge_observations, BRIDGE_REQUIRED_COLUMNS, "R3-B observations")
    removes = app_remove_events.copy()
    bridge = bridge_observations.copy()
    removes["eventTimestampUtc"] = pd.to_datetime(
        removes["eventTimestampUtc"], utc=True, errors="coerce"
    )
    bridge["observedAtUtc"] = pd.to_datetime(
        bridge["observedAtUtc"], utc=True, errors="coerce"
    )
    rows: list[dict[str, Any]] = []
    for _, event in removes.iterrows():
        output = event.to_dict()
        output.update({
            "attributionStatus": None,
            "attributionReason": None,
            "telemetryPlayerId": None,
            "retentionBridgeId": None,
            "bridgeObservedAtUtc": None,
            "mappingAgeSeconds": None,
        })
        raw_status = str(event["rawEventStatus"])
        event_time = event["eventTimestampUtc"]
        pseudo = event["userPseudoId"]
        if raw_status == "GaDuplicateConflict":
            output.update(
                attributionStatus="Ambiguous", attributionReason="GaDuplicateConflict"
            )
        elif raw_status == "UnmappedMissingPseudoId":
            output.update(
                attributionStatus="Unmapped",
                attributionReason="UnmappedMissingPseudoId",
            )
        elif raw_status != "Valid" or pd.isna(event_time):
            output.update(
                attributionStatus="Invalid", attributionReason="InvalidGaEvent"
            )
        else:
            candidates = bridge[
                (bridge["userPseudoId"] == pseudo)
                & bridge["observedAtUtc"].notna()
                & (bridge["observedAtUtc"] <= event_time)
                & bridge["mappingStatus"].isin(("Mapped", *BRIDGE_CONFLICT_STATUSES))
            ]
            if candidates.empty:
                output.update(
                    attributionStatus="Unmapped",
                    attributionReason="UnmappedNoPriorMapping",
                )
            else:
                latest_time = candidates["observedAtUtc"].max()
                latest = candidates[candidates["observedAtUtc"] == latest_time]
                conflicts = latest["mappingStatus"].isin(BRIDGE_CONFLICT_STATUSES).any()
                mapped = latest[latest["mappingStatus"] == "Mapped"]
                pairs = mapped[["telemetryPlayerId", "retentionBridgeId"]].drop_duplicates()
                if conflicts or len(pairs) != 1:
                    output.update(
                        attributionStatus="Ambiguous",
                        attributionReason="TemporalMappingConflict",
                        bridgeObservedAtUtc=latest_time,
                    )
                else:
                    pair = pairs.iloc[0]
                    age = (event_time - latest_time).total_seconds()
                    if age < 0:
                        raise ValueError("R3-D future mapping invariant failed")
                    output.update(
                        attributionStatus="Mapped",
                        attributionReason="Mapped",
                        telemetryPlayerId=pair["telemetryPlayerId"],
                        retentionBridgeId=pair["retentionBridgeId"],
                        bridgeObservedAtUtc=latest_time,
                        mappingAgeSeconds=float(age),
                    )
        rows.append(output)
    return pd.DataFrame(rows, columns=ATTRIBUTED_EVENT_COLUMNS)


def _status_count(events: pd.DataFrame, status: str) -> int:
    return int((events["attributionStatus"] == status).sum()) if not events.empty else 0


def _reason_count(events: pd.DataFrame, reason: str) -> int:
    return int((events["attributionReason"] == reason).sum()) if not events.empty else 0


def assemble_bundle(
    request: ObservedUninstallRequest,
    events: pd.DataFrame,
    selected_tables: Sequence[GaSourceTable],
    *,
    estimated_bytes: int,
    generated_at_utc: datetime,
) -> ReportBundle:
    _require_columns(events, ATTRIBUTED_EVENT_COLUMNS, "R3-D attributed events")
    observed = len(events)
    mapped = _status_count(events, "Mapped")
    unmapped = _status_count(events, "Unmapped")
    ambiguous = _status_count(events, "Ambiguous")
    invalid = _status_count(events, "Invalid")
    if mapped + unmapped + ambiguous + invalid != observed:
        raise ValueError("R3-D attribution classification invariant failed")
    physical = int(pd.to_numeric(events["physicalCount"]).sum()) if observed else 0
    exact_duplicates = (
        int(pd.to_numeric(events["exactDuplicateRowsRemoved"]).sum()) if observed else 0
    )
    variants = int(pd.to_numeric(events["variantCount"]).sum()) if observed else 0
    if physical != exact_duplicates + variants:
        raise ValueError("R3-D duplicate normalization invariant failed")
    mapped_rate = mapped / observed if observed else None
    age = pd.to_numeric(
        events.loc[events["attributionStatus"] == "Mapped", "mappingAgeSeconds"],
        errors="coerce",
    ).dropna()
    if (age < 0).any():
        raise ValueError("R3-D mapping age must be nonnegative")
    p50 = float(age.quantile(0.50, interpolation="linear")) if not age.empty else None
    p95 = float(age.quantile(0.95, interpolation="linear")) if not age.empty else None
    reasons = (
        "Mapped", "UnmappedMissingPseudoId", "UnmappedNoPriorMapping",
        "TemporalMappingConflict", "GaDuplicateConflict", "InvalidGaEvent",
    )
    reason_counts = {item: _reason_count(events, item) for item in reasons}
    metrics = {
        "source": {
            "appRemoveEvents": observed,
            "dailyRows": int(events.loc[
                events["sourceTableKind"] == "Daily", "physicalCount"
            ].sum()) if observed else 0,
            "intradayRows": int(events.loc[
                events["sourceTableKind"] == "Intraday", "physicalCount"
            ].sum()) if observed else 0,
            "selectedDailyTables": sum(item.kind == "Daily" for item in selected_tables),
            "selectedIntradayTables": sum(
                item.kind == "Intraday" for item in selected_tables
            ),
        },
        "uninstall": {
            "observedCount": observed,
            "distinctMappedProfiles": int(events.loc[
                events["attributionStatus"] == "Mapped", "telemetryPlayerId"
            ].nunique()),
            "distinctPseudoIds": int(events["userPseudoId"].dropna().nunique()),
        },
        "attribution": {
            "mappedCount": mapped,
            "mappedRate": {
                "count": mapped, "denominator": observed, "ratio": mapped_rate,
            },
            "unmappedCount": unmapped,
            "ambiguousCount": ambiguous,
            "invalidCount": invalid,
            "missingPseudoCount": reason_counts["UnmappedMissingPseudoId"],
            "noPriorMappingCount": reason_counts["UnmappedNoPriorMapping"],
            "temporalConflictCount": reason_counts["TemporalMappingConflict"],
            "gaDuplicateConflictCount": reason_counts["GaDuplicateConflict"],
            "invalidGaEventCount": reason_counts["InvalidGaEvent"],
        },
        "mappingAge": {
            "p50Seconds": p50,
            "p95Seconds": p95,
        },
        "dataQuality": {
            "physicalRows": physical,
            "logicalEvents": observed,
            "exactDuplicateRowsRemoved": exact_duplicates,
            "conflictingEventKeys": reason_counts["GaDuplicateConflict"],
        },
    }
    source_rows: list[dict[str, Any]] = []
    for kind, state in (("Daily", "Final"), ("Intraday", "Provisional")):
        source = events[events["sourceTableKind"] == kind]
        source_rows.append({
            "sourceTableKind": kind,
            "finalizationState": state,
            "selectedTableCount": sum(item.kind == kind for item in selected_tables),
            "appRemovePhysicalRows": int(source["physicalCount"].sum()),
            "logicalEvents": len(source),
        })
    attribution_rows = [
        {
            "attributionStatus": (
                "Mapped" if reason == "Mapped"
                else "Unmapped" if reason.startswith("Unmapped")
                else "Ambiguous" if reason in {
                    "TemporalMappingConflict", "GaDuplicateConflict"
                }
                else "Invalid"
            ),
            "attributionReason": reason,
            "count": count,
            "denominator": observed,
            "ratio": count / observed if observed else None,
        }
        for reason, count in reason_counts.items()
    ]
    age_rows = [{
        "distribution": "mappedUninstallMappingAge",
        "mappedCount": len(age),
        "p50Seconds": p50,
        "p95Seconds": p95,
        "minSeconds": float(age.min()) if not age.empty else None,
        "maxSeconds": float(age.max()) if not age.empty else None,
    }]
    warnings = tuple(
        ReportWarning(code, message)
        for condition, code, message in (
            (
                observed == 0,
                "NO_OBSERVED_APP_REMOVE",
                "No GA app_remove event was observed in the selected export window.",
            ),
            (
                any(item.kind == "Intraday" for item in selected_tables),
                "PROVISIONAL_GA_SOURCE",
                "At least one source date uses a provisional intraday table.",
            ),
            (
                ambiguous > 0,
                "UNINSTALL_ATTRIBUTION_AMBIGUOUS",
                "At least one observed app_remove event has ambiguous attribution.",
            ),
        )
        if condition
    )
    metadata = ReportMetadata(
        REPORT_CONTRACT_VERSION,
        ANALYSIS_TYPE,
        ANALYSIS_VERSION,
        generated_at_utc,
        request.scope(),
        {
            "observedAppRemoveEvents": observed,
            "mappedEvents": mapped,
            "unmappedEvents": unmapped,
            "ambiguousEvents": ambiguous,
        },
        {
            "physicalRows": physical,
            "logicalEvents": observed,
            "exactDuplicateRowsRemoved": exact_duplicates,
            "conflictingEventKeys": reason_counts["GaDuplicateConflict"],
        },
        {
            "eventMeaning": "A GA app_remove observation, not a current uninstall state",
            "attributionRule": "Latest unambiguous R3-B mapping at or before the event",
            "futureMappingAllowed": False,
            "gaExportUserIdentityAuthority": False,
            "mappingAgeThresholdSeconds": None,
            "churnMeaning": False,
            "bridgeAnalysisType": BRIDGE_ANALYSIS_TYPE,
            "bridgeAnalysisVersion": BRIDGE_ANALYSIS_VERSION,
        },
        warnings,
        estimated_bytes,
    )
    markdown = (
        "# Observed Uninstall Report\n\n"
        f"- Observed GA app_remove events: {observed}\n"
        f"- Mapped through the temporal bridge: {mapped}\n"
        f"- Unmapped: {unmapped}\n"
        f"- Ambiguous: {ambiguous}\n\n"
        "This report contains aggregate factual observations. An app_remove event is an "
        "observed event for one GA app instance; it is not a current state, permanent churn, "
        "user abandonment, or decision authority. Raw temporal attribution remains in the "
        "separately controlled restricted artifact.\n"
    )
    return ReportBundle(
        metadata,
        metrics,
        markdown,
        {
            "source-summary.csv": pd.DataFrame(source_rows),
            "attribution-summary.csv": pd.DataFrame(attribution_rows),
            "mapping-age-summary.csv": pd.DataFrame(age_rows),
        },
    )


def assert_normal_bundle_privacy(
    bundle: ReportBundle,
    events: pd.DataFrame,
    bridge_observations: pd.DataFrame,
) -> None:
    serialized = "\n".join((
        render_json(bundle.metadata),
        render_json(bundle.metrics),
        bundle.markdown,
        *(render_csv(value, columns=value.columns) for value in bundle.tables.values()),
    ))
    for field in RAW_IDENTIFIER_FIELDS:
        if field in serialized:
            raise ValueError(f"Raw identifier field escaped into normal bundle: {field}")
    for value in _empty_raw_identifier_values(events, bridge_observations):
        if value in serialized:
            raise ValueError("Raw identifier value escaped into normal bundle")


def _write_restricted_artifact(
    events: pd.DataFrame,
    request: ObservedUninstallRequest,
    *,
    output_root: Path,
    overwrite: bool,
) -> Path:
    target = output_root / "observed-uninstall-events" / scope_hash(request.scope())
    if target.exists() and not overwrite:
        raise FileExistsError(f"Restricted artifact target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    backup: Path | None = None
    try:
        (temporary / "observed-uninstall-events.csv").write_text(
            render_csv(
                events,
                columns=ATTRIBUTED_EVENT_COLUMNS,
                sort_by=(
                    "eventTimestampUtc", "userPseudoId", "eventBundleSequenceId",
                    "batchEventIndex", "batchOrderingId",
                ),
            ),
            encoding="utf-8",
            newline="\n",
        )
        metadata = {
            "artifactType": "restrictedObservedUninstallEvents",
            "contractVersion": ANALYSIS_VERSION,
            "classification": "RestrictedRawIdentity",
            "bridgeDependency": {
                "analysisType": BRIDGE_ANALYSIS_TYPE,
                "analysisVersion": BRIDGE_ANALYSIS_VERSION,
                "selection": "latest unambiguous mapping at or before eventTimestampUtc",
            },
            "permittedUse": "R4 normalized factual event input and restricted diagnostics",
            "forbiddenUse": "normal report, C-1 evidence, C-2 context, public analysis",
            "scope": to_external(request.scope()),
        }
        (temporary / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        files = sorted(path for path in temporary.iterdir() if path.is_file())
        manifest = {
            "artifactType": metadata["artifactType"],
            "files": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in files
            },
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        for path in temporary.iterdir():
            if path.is_file():
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
        if target.exists():
            backup = target.with_name(f".{target.name}.backup-{os.getpid()}")
            if backup.exists():
                raise FileExistsError(f"Atomic restricted backup already exists: {backup}")
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


def estimate_observed_uninstall_bytes(
    request: ObservedUninstallRequest,
    *,
    config: AnalyticsConfig,
    client: Any,
) -> tuple[tuple[GaSourceTable, ...], QuerySpec, QuerySpec, int]:
    bridge_request = request.bridge_request()
    selected = resolve_ga_source_tables(bridge_request, client=client)
    app_remove_query = build_app_remove_query(
        request, config=config, selected_tables=selected
    )
    bridge_query = build_bridge_query(
        bridge_request, config=config, selected_tables=selected
    )
    app_remove_bytes = dry_run_query(
        app_remove_query, client=client, config=config
    ).total_bytes_processed
    bridge_bytes = dry_run_query(
        bridge_query, client=client, config=config
    ).total_bytes_processed
    return selected, app_remove_query, bridge_query, app_remove_bytes + bridge_bytes


def analyze_observed_uninstall(
    request: ObservedUninstallRequest,
    *,
    config: AnalyticsConfig,
    client: Any,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
) -> ObservedUninstallAnalysis:
    if maximum_total_bytes < 0:
        raise ValueError("maximum_total_bytes must be nonnegative")
    selected, app_remove_query, bridge_query, estimate = estimate_observed_uninstall_bytes(
        request, config=config, client=client
    )
    if estimate > maximum_total_bytes:
        raise RuntimeError(
            f"R3-D dry-run estimate {estimate:,} bytes exceeds maximum "
            f"{maximum_total_bytes:,}; no analysis query was executed"
        )
    app_remove = query_dataframe(
        app_remove_query,
        client=client,
        config=config,
        maximum_bytes_billed=maximum_total_bytes,
    )
    bridge = query_dataframe(
        bridge_query,
        client=client,
        config=config,
        maximum_bytes_billed=maximum_total_bytes,
    )
    events = attribute_app_remove_events(app_remove, bridge)
    bundle = assemble_bundle(
        request,
        events,
        selected,
        estimated_bytes=estimate,
        generated_at_utc=datetime.now(timezone.utc),
    )
    assert_normal_bundle_privacy(bundle, events, bridge)
    return ObservedUninstallAnalysis(bundle, events, bridge, selected, estimate)


def generate_observed_uninstall_report(
    request: ObservedUninstallRequest,
    *,
    config: AnalyticsConfig,
    client: Any,
    output_root: Path = Path("reports/generated"),
    restricted_output_root: Path = Path("reports/restricted"),
    overwrite: bool = False,
    maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
) -> ObservedUninstallOutput:
    analysis = analyze_observed_uninstall(
        request,
        config=config,
        client=client,
        maximum_total_bytes=maximum_total_bytes,
    )
    restricted = _write_restricted_artifact(
        analysis.events,
        request,
        output_root=restricted_output_root,
        overwrite=overwrite,
    )
    try:
        report = write_report_bundle(
            analysis.bundle,
            output_root=output_root,
            overwrite=overwrite,
            table_specs=TABLE_SPECS,
            include_manifest=True,
        )
    except Exception:
        if restricted.exists():
            shutil.rmtree(restricted)
        raise
    return ObservedUninstallOutput(report, restricted, analysis)
