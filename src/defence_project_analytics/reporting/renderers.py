"""Deterministic JSON, CSV, and Markdown rendering helpers."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime, timezone
import json
import math
from numbers import Real
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import pandas as pd


_BALANCING_LANGUAGE = re.compile(r"\b(overtuned|undertuned|nerf|buff|recommend(?:ation|ed)?)\b", re.IGNORECASE)


def camel_case(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _finite_scalar(value: Any) -> Any:
    if value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError("Infinity is not valid in a report")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Report datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


def to_external(value: Any) -> Any:
    """Convert dataclasses and pandas values to finite camelCase JSON values."""

    if is_dataclass(value) and not isinstance(value, type):
        return {camel_case(item.name): to_external(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {camel_case(str(key)): to_external(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_external(item) for item in value]
    return _finite_scalar(value)


def render_json(value: Any) -> str:
    return json.dumps(to_external(value), allow_nan=False, ensure_ascii=False, indent=2) + "\n"


def stable_dataframe(frame: pd.DataFrame, *, columns: Iterable[str], sort_by: Iterable[str] = ()) -> pd.DataFrame:
    result = frame.reindex(columns=list(columns)).copy()
    requested = list(sort_by)
    keys = [key.removeprefix("-") for key in requested if key.removeprefix("-") in result.columns]
    ascending = [not key.startswith("-") for key in requested if key.removeprefix("-") in result.columns]
    if keys and not result.empty:
        result = result.sort_values(keys, ascending=ascending, kind="mergesort", na_position="last")
    return result.reset_index(drop=True)


def render_csv(frame: pd.DataFrame, *, columns: Iterable[str], sort_by: Iterable[str] = ()) -> str:
    stable = stable_dataframe(frame, columns=columns, sort_by=sort_by)
    for column in stable.columns:
        for value in stable[column].dropna():
            if isinstance(value, Real) and not math.isfinite(float(value)):
                raise ValueError(f"Infinity is not valid in CSV column {column}")
    return stable.to_csv(index=False, lineterminator="\n", float_format="%.12g")


def assert_factual_markdown(markdown: str) -> None:
    match = _BALANCING_LANGUAGE.search(markdown)
    if match:
        raise ValueError(f"Report contains prohibited balancing language: {match.group(0)}")


def attached_tables_markdown(paths: Iterable[str]) -> str:
    return "\n".join(f"- `{Path(path).as_posix()}`" for path in paths)
