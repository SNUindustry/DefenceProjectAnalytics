from defence_project_analytics.contract_validation import load_schema_contracts


def test_all_copied_schema_contracts_parse() -> None:
    contracts = load_schema_contracts()
    assert len(contracts) == 25
    run_summary = {field.name: field for field in contracts["telemetry_run_summary"]}
    assert run_summary["runId"].field_type == "STRING"
    assert run_summary["environment"].field_type == "STRING"
    assert run_summary["attemptId"].field_type == "STRING"
    assert run_summary["gameplayOutcome"].field_type == "STRING"

