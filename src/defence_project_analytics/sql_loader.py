"""Load repository SQL fixtures without executing them."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Mapping

from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QueryParameterValue, QuerySpec


PROJECT_PLACEHOLDER = "<firebase-project-id>"
DATASET_PLACEHOLDER = "<bigquery-dataset-id>"
_NAMED_PARAMETER = re.compile(r"(?<!@)@([A-Za-z_][A-Za-z0-9_]*)")


def repository_root(start: Path | None = None) -> Path:
    configured = os.getenv("DPA_REPOSITORY_ROOT")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([start or Path.cwd(), Path(__file__).resolve()])

    for candidate in candidates:
        candidate = candidate.resolve()
        for parent in (candidate, *candidate.parents):
            if (parent / "pyproject.toml").is_file() and (parent / "sql").is_dir():
                return parent
    raise FileNotFoundError(
        "Could not locate the repository root. Set DPA_REPOSITORY_ROOT explicitly."
    )


def resolve_sql_path(path: str | Path, root: Path | None = None) -> Path:
    repo = (root or repository_root()).resolve()
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
    if not resolved.is_relative_to(repo):
        raise ValueError("SQL path must stay inside the repository")
    if resolved.suffix.lower() != ".sql":
        raise ValueError("SQL path must point to a .sql file")
    if not resolved.is_file():
        raise FileNotFoundError(f"SQL file not found: {resolved}")
    return resolved


def load_sql(
    path: str | Path,
    *,
    config: AnalyticsConfig | None = None,
    root: Path | None = None,
) -> str:
    settings = config or AnalyticsConfig.from_env()
    sql = resolve_sql_path(path, root).read_text(encoding="utf-8")
    return sql.replace(PROJECT_PLACEHOLDER, settings.project_id).replace(
        DATASET_PLACEHOLDER, settings.dataset_id
    )


def named_parameter_names(sql: str) -> frozenset[str]:
    return frozenset(_NAMED_PARAMETER.findall(sql))


def load_query(
    path: str | Path,
    *,
    parameters: Mapping[str, Any | QueryParameterValue] | None = None,
    config: AnalyticsConfig | None = None,
    root: Path | None = None,
) -> QuerySpec:
    sql = load_sql(path, config=config, root=root)
    supplied = dict(parameters or {})
    expected = named_parameter_names(sql)
    missing = expected.difference(supplied)
    extra = supplied.keys() - expected
    if missing:
        raise ValueError(f"Missing named BigQuery parameters: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"Unused named BigQuery parameters: {', '.join(sorted(extra))}")
    return QuerySpec(sql=sql, parameters=supplied)

