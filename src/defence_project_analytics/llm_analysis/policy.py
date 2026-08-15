"""Deterministic C-2 evidence-strength, actionability, and prose policy."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


_STANDALONE_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d+(?:[.,]\d+)?(?:\s*%|\s*pp)?(?![A-Za-z0-9_])"
)
_UNSUPPORTED_CAUSAL = re.compile(
    r"\b(caused|led\s+to|resulted\s+in|proves?|definitely\s+(?:fixes|improves))\b"
    r"|반드시\s*개선|원인(?:이|이다|으로)|이\s*때문에\s*(?:증가|감소)|상충\s*evidence가\s*없|반대\s*evidence가\s*존재하지\s*않",
    re.IGNORECASE,
)
_RAW_IDENTIFIER = re.compile(
    r"\b(telemetryPlayerId|runId|attemptId|eventId|operationId|exposureId|"
    r"presentationId|batchId|uploadId|instanceId)\b"
)
_SECRET = re.compile(
    r"\b(api[_-]?key|authorization|bearer\s+[A-Za-z0-9._-]+|secret[_-]?key)\b",
    re.IGNORECASE,
)


def contains_numeric_claim(text: str) -> bool:
    return _STANDALONE_NUMBER.search(text) is not None


def prohibited_language(text: str) -> str | None:
    match = _UNSUPPORTED_CAUSAL.search(text)
    return match.group(0) if match else None


def forbidden_private_text(text: str) -> str | None:
    match = _RAW_IDENTIFIER.search(text) or _SECRET.search(text)
    return match.group(0) if match else None


def evidence_strength(
    brief_status: str,
    supporting: Iterable[Mapping[str, Any]],
    counter: Iterable[Mapping[str, Any]] = (),
) -> str:
    supports = tuple(supporting)
    counters = tuple(counter)
    if brief_status == "Insufficient" or not supports:
        return "Insufficient"
    if brief_status == "Limited" or any(
        item.get("status") != "Comparable" or bool(item.get("warningCodes"))
        for item in supports
    ):
        return "Limited"
    families = {
        (str(item.get("domain")), str(item.get("metricFamily"))) for item in supports
    }
    if counters or len(families) < 2:
        return "Moderate"
    return "Strong"


def actionability(
    *,
    action_type: str,
    strength: str,
    brief_status: str,
    has_design_objective: bool,
    conceptual_target: bool,
    heuristic_numeric: bool,
) -> str:
    if action_type == "NoChange":
        return "Hold"
    if action_type in {"CollectMoreData", "Investigate"}:
        return "Investigate"
    if action_type in {"Experiment", "TelemetryChange"}:
        return "ExperimentCandidate"
    if (
        action_type in {"BalanceChange", "UXChange"}
        and strength == "Strong"
        and brief_status == "Ready"
        and has_design_objective
        and not conceptual_target
        and not heuristic_numeric
    ):
        return "HumanReviewCandidate"
    return "ExperimentCandidate"


def overall_assessment(
    brief_status: str,
    action_types: Iterable[str],
    actionabilities: Iterable[str],
) -> str:
    types = tuple(action_types)
    abilities = tuple(actionabilities)
    if brief_status == "Insufficient":
        return "MoreDataRecommended"
    if "HumanReviewCandidate" in abilities:
        return "HumanReviewCandidateAvailable"
    material = [item for item in types if item != "NoChange"]
    if not material:
        return "NoMaterialCandidateIdentified"
    if all(item == "CollectMoreData" for item in material):
        return "MoreDataRecommended"
    return "EvidenceLimited"

