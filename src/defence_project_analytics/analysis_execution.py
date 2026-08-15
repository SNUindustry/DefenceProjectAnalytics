"""Prepared read-only query execution shared by comparison orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from defence_project_analytics.bigquery_client import dry_run_query, query_dataframe
from defence_project_analytics.config import AnalyticsConfig
from defence_project_analytics.models import QuerySpec


@dataclass(frozen=True, slots=True)
class PreparedQueryBatch:
    """A query set whose complete dry-run estimate is known before execution."""

    queries: Mapping[str, QuerySpec]
    estimated_bytes: Mapping[str, int]

    @property
    def total_estimated_bytes(self) -> int:
        return sum(self.estimated_bytes.values())


def prepare_query_batch(
    queries: Mapping[str, QuerySpec],
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
) -> PreparedQueryBatch:
    estimates = {
        name: dry_run_query(query, client=client, config=config).total_bytes_processed
        for name, query in queries.items()
    }
    return PreparedQueryBatch(dict(queries), estimates)


def execute_prepared_query_batch(
    batch: PreparedQueryBatch,
    *,
    client: Any | None = None,
    config: AnalyticsConfig | None = None,
    maximum_bytes_billed: int | None = None,
) -> dict[str, pd.DataFrame]:
    """Execute only queries that have already completed a dry run."""

    return {
        name: query_dataframe(
            query,
            client=client,
            config=config,
            maximum_bytes_billed=maximum_bytes_billed,
        )
        for name, query in batch.queries.items()
    }
