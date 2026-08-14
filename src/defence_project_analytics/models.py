"""Shared immutable query models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class QueryParameterValue:
    """A named scalar value with an optional explicit BigQuery type."""

    value: Any
    bigquery_type: str | None = None


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """SQL text and its named BigQuery parameters."""

    sql: str
    parameters: Mapping[str, Any | QueryParameterValue] = field(default_factory=dict)

