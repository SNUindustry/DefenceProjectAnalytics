from types import SimpleNamespace

from defence_project_analytics.analysis_execution import (
    execute_prepared_query_batch,
    prepare_query_batch,
)
from defence_project_analytics.models import QuerySpec


def test_prepare_completes_all_dry_runs_before_execution(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "defence_project_analytics.analysis_execution.dry_run_query",
        lambda query, **kwargs: events.append(f"dry:{query.sql}") or SimpleNamespace(total_bytes_processed=3),
    )
    monkeypatch.setattr(
        "defence_project_analytics.analysis_execution.query_dataframe",
        lambda query, **kwargs: events.append(f"run:{query.sql}") or [],
    )
    batch = prepare_query_batch({"a": QuerySpec("SELECT 1"), "b": QuerySpec("SELECT 2")})
    assert events == ["dry:SELECT 1", "dry:SELECT 2"]
    assert batch.total_estimated_bytes == 6
    execute_prepared_query_batch(batch, maximum_bytes_billed=10)
    assert events == ["dry:SELECT 1", "dry:SELECT 2", "run:SELECT 1", "run:SELECT 2"]

