from pathlib import Path

import pytest

from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.sql_loader import load_query, load_sql, named_parameter_names


def _fixture_root(tmp_path: Path, sql: str) -> Path:
    (tmp_path / "sql").mkdir()
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "sql" / "query.sql").write_text(sql, encoding="utf-8")
    return tmp_path


def test_sql_loader_replaces_project_and_dataset_placeholders(tmp_path: Path) -> None:
    root = _fixture_root(
        tmp_path,
        "SELECT * FROM `<firebase-project-id>.<bigquery-dataset-id>.events`",
    )
    sql = load_sql(
        "sql/query.sql",
        root=root,
        config=AnalyticsConfig("example-project", "analytics", "US"),
    )
    assert "`example-project.analytics.events`" in sql
    assert "<firebase-project-id>" not in sql


def test_loader_passes_named_parameters_without_interpolation(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path, "SELECT @environment, @content_version")
    query = load_query(
        "sql/query.sql",
        root=root,
        parameters={"environment": "Production", "content_version": 7},
    )
    assert named_parameter_names(query.sql) == {"environment", "content_version"}
    assert query.parameters["environment"] == "Production"
    assert "Production" not in query.sql


def test_loader_rejects_missing_or_unused_parameters(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path, "SELECT @required")
    with pytest.raises(ValueError, match="Missing"):
        load_query("sql/query.sql", root=root)
    with pytest.raises(ValueError, match="Unused"):
        load_query(
            "sql/query.sql", root=root, parameters={"required": 1, "extra": 2}
        )

