from types import SimpleNamespace

from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.contract_validation import (
    load_schema_contracts, validate_b8_source_contracts,
    validate_r3b_source_contracts, validate_r3d_source_contracts,
)


def test_all_copied_schema_contracts_parse() -> None:
    contracts = load_schema_contracts()
    assert len(contracts) == 26
    run_summary = {field.name: field for field in contracts["telemetry_run_summary"]}
    assert run_summary["runId"].field_type == "STRING"
    assert run_summary["environment"].field_type == "STRING"
    assert run_summary["attemptId"].field_type == "STRING"
    assert run_summary["gameplayOutcome"].field_type == "STRING"
    run_start = {field.name: field for field in contracts["telemetry_run_start_snapshot"]}
    assert run_start["appProcessSessionId"].mode == "NULLABLE"
    assert run_start["latestForegroundOccurrenceId"].mode == "NULLABLE"
    lifecycle = contracts["telemetry_app_lifecycle_events"]
    assert len(lifecycle) == 26
    assert {field.name for field in lifecycle} >= {
        "lifecycleOccurrenceId", "appProcessSessionId", "occurredAtUtc",
        "previousBackgroundAtUtc", "timingQuality", "telemetryPlayerId",
    }


def test_b8_validator_checks_nullable_run_linkage_fields() -> None:
    contracts = load_schema_contracts()
    tables = {}
    for name in (
        "telemetry_run_summary", "telemetry_run_start_snapshot",
        "telemetry_app_lifecycle_events",
    ):
        tables[name] = SimpleNamespace(
            table_type="TABLE",
            schema=[
                SimpleNamespace(name=field.name, field_type=field.field_type, mode=field.mode)
                for field in contracts[name]
            ],
        )

    class Client:
        def get_table(self, ref: str) -> object:
            return tables[ref.rsplit(".", 1)[-1]]

    config = AnalyticsConfig.for_backend("test")
    assert validate_b8_source_contracts(client=Client(), config=config).ready
    tables["telemetry_run_start_snapshot"].schema = [
        field for field in tables["telemetry_run_start_snapshot"].schema
        if field.name != "latestForegroundOccurrenceId"
    ]
    report = validate_b8_source_contracts(client=Client(), config=config)
    assert not report.ready
    assert any("latestForegroundOccurrenceId" in item.message for item in report.findings)


def test_r3b_validator_checks_nested_ga_source_fields() -> None:
    contracts = load_schema_contracts()

    def field(name: str, field_type: str, mode: str = "NULLABLE", *children: object) -> object:
        return SimpleNamespace(
            name=name, field_type=field_type, mode=mode, fields=list(children),
        )

    custom = SimpleNamespace(
        schema=[
            field(item.name, item.field_type, item.mode)
            for item in contracts["telemetry_app_lifecycle_events"]
        ]
    )
    ga = SimpleNamespace(schema=[
        field("event_timestamp", "INTEGER"),
        field("event_name", "STRING"),
        field(
            "event_params", "RECORD", "REPEATED",
            field("key", "STRING"),
            field(
                "value", "RECORD", "NULLABLE",
                field("string_value", "STRING"),
                field("int_value", "INTEGER"),
            ),
        ),
        field("user_id", "STRING"),
        field("user_pseudo_id", "STRING"),
        field("stream_id", "STRING"),
        field("platform", "STRING"),
        field("app_info", "RECORD", "NULLABLE", field("version", "STRING")),
    ])

    class Client:
        def get_table(self, ref: str) -> object:
            return ga if ref.endswith("events_20260928") else custom

    config = AnalyticsConfig.for_backend("production")
    report = validate_r3b_source_contracts(
        client=Client(), config=config, ga_table_id="events_20260928",
    )
    assert report.ready
    assert report.parsed_contracts == 2
    assert report.checked_objects == 2

    ga.schema[2].fields[1].fields = [field("string_value", "STRING")]
    report = validate_r3b_source_contracts(
        client=Client(), config=config, ga_table_id="events_20260928",
    )
    assert not report.ready
    assert any("event_params.value.int_value" in item.message for item in report.findings)


def test_r3d_validator_checks_app_remove_dedup_fields_and_r3b_dependency() -> None:
    contracts = load_schema_contracts()

    def field(name: str, field_type: str, mode: str = "NULLABLE", *children: object) -> object:
        return SimpleNamespace(
            name=name, field_type=field_type, mode=mode, fields=list(children),
        )

    custom = SimpleNamespace(schema=[
        field(item.name, item.field_type, item.mode)
        for item in contracts["telemetry_app_lifecycle_events"]
    ])
    ga = SimpleNamespace(schema=[
        field("event_date", "STRING"),
        field("event_timestamp", "INTEGER"),
        field("event_name", "STRING"),
        field(
            "event_params", "RECORD", "REPEATED",
            field("key", "STRING"),
            field(
                "value", "RECORD", "NULLABLE",
                field("string_value", "STRING"),
                field("int_value", "INTEGER"),
            ),
        ),
        field("event_previous_timestamp", "INTEGER"),
        field("event_bundle_sequence_id", "INTEGER"),
        field("event_server_timestamp_offset", "INTEGER"),
        field("event_original_occurrence_timestamp", "INTEGER"),
        field("batch_event_index", "INTEGER"),
        field("batch_ordering_id", "INTEGER"),
        field("user_id", "STRING"),
        field("user_pseudo_id", "STRING"),
        field("stream_id", "STRING"),
        field("platform", "STRING"),
        field("app_info", "RECORD", "NULLABLE", field("version", "STRING")),
    ])

    class Client:
        def get_table(self, ref: str) -> object:
            return ga if ref.endswith("events_20260928") else custom

    config = AnalyticsConfig.for_backend("production")
    report = validate_r3d_source_contracts(
        client=Client(), config=config, ga_table_id="events_20260928",
    )
    assert report.ready
    assert report.parsed_contracts == 3
    assert report.checked_objects == 3

    ga.schema = [item for item in ga.schema if item.name != "batch_ordering_id"]
    report = validate_r3d_source_contracts(
        client=Client(), config=config, ga_table_id="events_20260928",
    )
    assert not report.ready
    assert any("batch_ordering_id" in item.message for item in report.findings)
