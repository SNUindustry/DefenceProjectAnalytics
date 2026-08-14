"""Small, read-only BigQuery client surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any, Mapping

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
import pandas as pd

from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.errors import ADC_DIAGNOSTIC, ADCNotConfiguredError
from defence_project_analytics.models import QueryParameterValue, QuerySpec


_MUTATION_KEYWORDS = re.compile(
    r"\b(ALTER|CALL|CREATE|DELETE|DROP|EXECUTE|EXPORT|GRANT|INSERT|LOAD|MERGE|"
    r"RENAME|REPLACE|REVOKE|TRUNCATE|UPDATE)\b",
    flags=re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:''|\\.|[^'])*'", re.DOTALL)


@dataclass(frozen=True, slots=True)
class DryRunResult:
    total_bytes_processed: int
    referenced_tables: tuple[str, ...]


def get_client(config: AnalyticsConfig | None = None) -> bigquery.Client:
    """Create a BigQuery client using ADC only."""

    settings = config or AnalyticsConfig.from_env()
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except DefaultCredentialsError as exc:
        raise ADCNotConfiguredError(ADC_DIAGNOSTIC) from exc
    return bigquery.Client(
        project=settings.project_id,
        credentials=credentials,
        location=settings.location,
    )


def _without_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*(?:\n|$)", " ", sql)


def _validate_read_only(sql: str) -> None:
    uncommented = _without_comments(sql).strip()
    first = re.match(r"([A-Za-z]+)", uncommented)
    if first is None or first.group(1).upper() not in {"SELECT", "WITH"}:
        raise ValueError("Only read-only SELECT/WITH queries are allowed")

    without_strings = _STRING_LITERAL.sub("''", uncommented)
    if _MUTATION_KEYWORDS.search(without_strings):
        raise ValueError("Mutation, DDL, export, and procedure statements are not allowed")
    if ";" in without_strings.rstrip().rstrip(";"):
        raise ValueError("Multiple SQL statements are not allowed")


def _infer_parameter_type(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, Decimal):
        return "NUMERIC"
    if isinstance(value, str):
        return "STRING"
    if value is None:
        raise ValueError("NULL parameters require QueryParameterValue(..., bigquery_type=...)")
    raise TypeError(f"Unsupported BigQuery scalar parameter type: {type(value).__name__}")


def build_query_parameters(
    parameters: Mapping[str, Any | QueryParameterValue] | None,
) -> list[bigquery.ScalarQueryParameter]:
    result: list[bigquery.ScalarQueryParameter] = []
    for name, item in (parameters or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid named BigQuery parameter: {name!r}")
        if isinstance(item, QueryParameterValue):
            value = item.value
            parameter_type = item.bigquery_type or _infer_parameter_type(value)
        else:
            value = item
            parameter_type = _infer_parameter_type(value)
        result.append(bigquery.ScalarQueryParameter(name, parameter_type, value))
    return result


def _query_parts(
    query: str | QuerySpec,
    parameters: Mapping[str, Any | QueryParameterValue] | None,
) -> tuple[str, Mapping[str, Any | QueryParameterValue]]:
    if isinstance(query, QuerySpec):
        if parameters is not None:
            raise ValueError("Pass parameters either in QuerySpec or separately, not both")
        return query.sql, query.parameters
    return query, parameters or {}


def _job_config(
    parameters: Mapping[str, Any | QueryParameterValue],
    *,
    dry_run: bool = False,
    maximum_bytes_billed: int | None = None,
) -> bigquery.QueryJobConfig:
    config = bigquery.QueryJobConfig(
        dry_run=dry_run,
        use_query_cache=not dry_run,
        use_legacy_sql=False,
        query_parameters=build_query_parameters(parameters),
    )
    if maximum_bytes_billed is not None:
        config.maximum_bytes_billed = maximum_bytes_billed
    return config


def query_dataframe(
    query: str | QuerySpec,
    *,
    parameters: Mapping[str, Any | QueryParameterValue] | None = None,
    client: bigquery.Client | None = None,
    config: AnalyticsConfig | None = None,
    maximum_bytes_billed: int | None = None,
) -> pd.DataFrame:
    """Run one read-only query and return its result as a DataFrame."""

    settings = config or AnalyticsConfig.from_env()
    sql, values = _query_parts(query, parameters)
    _validate_read_only(sql)
    bq_client = client or get_client(settings)
    job = bq_client.query(
        sql,
        job_config=_job_config(values, maximum_bytes_billed=maximum_bytes_billed),
        location=settings.location,
    )
    return job.result().to_dataframe(create_bqstorage_client=False)


def dry_run_query(
    query: str | QuerySpec,
    *,
    parameters: Mapping[str, Any | QueryParameterValue] | None = None,
    client: bigquery.Client | None = None,
    config: AnalyticsConfig | None = None,
) -> DryRunResult:
    """Validate a read-only query and report estimated bytes without executing it."""

    settings = config or AnalyticsConfig.from_env()
    sql, values = _query_parts(query, parameters)
    _validate_read_only(sql)
    bq_client = client or get_client(settings)
    job = bq_client.query(
        sql,
        job_config=_job_config(values, dry_run=True),
        location=settings.location,
    )
    referenced = tuple(sorted(str(table) for table in (job.referenced_tables or ())))
    return DryRunResult(int(job.total_bytes_processed or 0), referenced)


def table_exists(
    table_name: str,
    *,
    client: bigquery.Client | None = None,
    config: AnalyticsConfig | None = None,
) -> bool:
    settings = config or AnalyticsConfig.from_env()
    bq_client = client or get_client(settings)
    try:
        table = bq_client.get_table(settings.object_ref(table_name))
    except NotFound:
        return False
    return table.table_type not in {"VIEW", "MATERIALIZED_VIEW"}


def view_exists(
    view_name: str,
    *,
    client: bigquery.Client | None = None,
    config: AnalyticsConfig | None = None,
) -> bool:
    settings = config or AnalyticsConfig.from_env()
    bq_client = client or get_client(settings)
    try:
        table = bq_client.get_table(settings.object_ref(view_name))
    except NotFound:
        return False
    return table.table_type in {"VIEW", "MATERIALIZED_VIEW"}
