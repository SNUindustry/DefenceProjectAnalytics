"""Pure, warning-aware comparison primitives for B-6."""

from __future__ import annotations

from dataclasses import replace
import math
from numbers import Real
from typing import Any, Iterable, Mapping

from defence_project_analytics.reporting.models import (
    ComparisonRow,
    DistributionDelta,
    DistributionSummary,
    MetricRatio,
    RatioDelta,
    ScalarDelta,
)
from defence_project_analytics.reporting.warnings import sort_comparison_codes


INCOMPATIBLE_CODES = {
    "INCOMPATIBLE_REPORT_CONTRACT",
    "INCOMPATIBLE_ANALYSIS_VERSION",
    "INCOMPATIBLE_METRIC_DEFINITION",
    "INCOMPATIBLE_OBSERVATION_UNIT",
    "INCOMPATIBLE_WINDOW_DEFINITION",
}
MISSING_CODES = {
    "BASELINE_VALUE_MISSING",
    "CANDIDATE_VALUE_MISSING",
    "BASELINE_DOMAIN_UNAVAILABLE",
    "CANDIDATE_DOMAIN_UNAVAILABLE",
}


def finite_number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    number = value.item() if hasattr(value, "item") else value
    if isinstance(number, float) and (math.isnan(number) or not math.isfinite(number)):
        return None
    return number


def change_direction(
    delta: float | int | None, *, tolerance: float = 1e-12
) -> str:
    if delta is None:
        return "Unavailable"
    if abs(float(delta)) <= tolerance:
        return "UnchangedWithinTolerance"
    return "Increased" if delta > 0 else "Decreased"


def comparison_status(warning_codes: Iterable[str]) -> str:
    codes = set(warning_codes)
    if codes.intersection(INCOMPATIBLE_CODES):
        return "Incompatible"
    if codes.intersection(MISSING_CODES):
        return "Unavailable"
    if codes:
        return "Limited"
    return "Comparable"


def scalar_delta(baseline: Any, candidate: Any) -> ScalarDelta:
    baseline_number = finite_number(baseline)
    candidate_number = finite_number(candidate)
    if baseline_number is None or candidate_number is None:
        return ScalarDelta(baseline_number, candidate_number, None, None)
    absolute = float(candidate_number) - float(baseline_number)
    relative = None if float(baseline_number) == 0 else absolute / abs(float(baseline_number))
    return ScalarDelta(baseline_number, candidate_number, absolute, relative)


def ratio_delta(
    baseline: MetricRatio | None, candidate: MetricRatio | None
) -> RatioDelta:
    if baseline is None or candidate is None or baseline.ratio is None or candidate.ratio is None:
        return RatioDelta(baseline, candidate, None, None)
    difference = candidate.ratio - baseline.ratio
    relative = None if baseline.ratio == 0 else difference / abs(baseline.ratio)
    return RatioDelta(baseline, candidate, 100.0 * difference, relative)


def distribution_delta(
    baseline: DistributionSummary | None,
    candidate: DistributionSummary | None,
) -> DistributionDelta:
    def subtract(name: str) -> float | None:
        if baseline is None or candidate is None:
            return None
        left = finite_number(getattr(baseline, name))
        right = finite_number(getattr(candidate, name))
        return None if left is None or right is None else float(right) - float(left)

    return DistributionDelta(
        baseline,
        candidate,
        subtract("mean"),
        subtract("p10"),
        subtract("p25"),
        subtract("median"),
        subtract("p75"),
        subtract("p90"),
    )


def _missing_codes(baseline_observed: bool, candidate_observed: bool) -> list[str]:
    result: list[str] = []
    if not baseline_observed:
        result.append("BASELINE_VALUE_MISSING")
    if not candidate_observed:
        result.append("CANDIDATE_VALUE_MISSING")
    return result


def scalar_comparison_row(
    *,
    domain: str,
    metric_family: str,
    metric: str,
    baseline: Any,
    candidate: Any,
    unit: str,
    observation_unit: str,
    warning_codes: Iterable[str] = (),
    entity_type: str | None = None,
    entity_key: str | None = None,
    dimension: str | None = None,
    dimension_value: str | None = None,
    tolerance: float = 1e-12,
) -> ComparisonRow:
    delta = scalar_delta(baseline, candidate)
    baseline_observed = delta.baseline is not None
    candidate_observed = delta.candidate is not None
    codes = sort_comparison_codes(
        (*warning_codes, *_missing_codes(baseline_observed, candidate_observed))
    )
    return ComparisonRow(
        domain=domain,
        metric_family=metric_family,
        metric=metric,
        entity_type=entity_type,
        entity_key=entity_key,
        dimension=dimension,
        dimension_value=dimension_value,
        value_type="scalar",
        unit=unit,
        observation_unit=observation_unit,
        baseline_observed=baseline_observed,
        candidate_observed=candidate_observed,
        baseline_value=delta.baseline,
        candidate_value=delta.candidate,
        absolute_delta=delta.absolute_delta,
        relative_delta=delta.relative_delta,
        direction=change_direction(delta.absolute_delta, tolerance=tolerance),
        status=comparison_status(codes),
        warning_codes=codes,
    )


def ratio_comparison_row(
    *,
    domain: str,
    metric_family: str,
    metric: str,
    baseline: MetricRatio | None,
    candidate: MetricRatio | None,
    observation_unit: str,
    warning_codes: Iterable[str] = (),
    entity_type: str | None = None,
    entity_key: str | None = None,
    dimension: str | None = None,
    dimension_value: str | None = None,
    tolerance: float = 1e-12,
) -> ComparisonRow:
    delta = ratio_delta(baseline, candidate)
    baseline_observed = baseline is not None
    candidate_observed = candidate is not None
    codes = sort_comparison_codes(
        (*warning_codes, *_missing_codes(baseline_observed, candidate_observed))
    )
    return ComparisonRow(
        domain=domain,
        metric_family=metric_family,
        metric=metric,
        entity_type=entity_type,
        entity_key=entity_key,
        dimension=dimension,
        dimension_value=dimension_value,
        value_type="ratio",
        unit="ratio",
        observation_unit=observation_unit,
        baseline_observed=baseline_observed,
        candidate_observed=candidate_observed,
        baseline_count=None if baseline is None else baseline.count,
        baseline_denominator=None if baseline is None else baseline.denominator,
        baseline_value=None if baseline is None else baseline.ratio,
        candidate_count=None if candidate is None else candidate.count,
        candidate_denominator=None if candidate is None else candidate.denominator,
        candidate_value=None if candidate is None else candidate.ratio,
        percentage_point_delta=delta.percentage_point_delta,
        relative_delta=delta.relative_delta,
        direction=change_direction(delta.percentage_point_delta, tolerance=tolerance),
        status=comparison_status(codes),
        warning_codes=codes,
    )


def with_warning_codes(row: ComparisonRow, codes: Iterable[str]) -> ComparisonRow:
    combined = sort_comparison_codes((*row.warning_codes, *codes))
    return replace(row, status=comparison_status(combined), warning_codes=combined)


def metric_ratio_from_mapping(value: Any) -> MetricRatio | None:
    if isinstance(value, MetricRatio):
        return value
    if not isinstance(value, Mapping):
        return None
    count = finite_number(value.get("count"))
    denominator = finite_number(value.get("denominator"))
    ratio = finite_number(value.get("ratio"))
    if count is None or denominator is None:
        return None
    return MetricRatio(int(count), int(denominator), None if ratio is None else float(ratio))
