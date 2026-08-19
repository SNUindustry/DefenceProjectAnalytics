"""Deterministic C-2 evidence-strength, actionability, and prose policy."""

from __future__ import annotations

from enum import StrEnum
import re
from typing import Any, Iterable, Mapping


class CausalMode(StrEnum):
    """Field-specific epistemic mode for deterministic prose checks."""

    DEFAULT = "default"
    PROSPECTIVE_RISK = "prospectiveRisk"
    INVESTIGATION_GAP = "investigationGap"


_STANDALONE_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d+(?:[.,]\d+)?(?:\s*%|\s*pp)?(?![A-Za-z0-9_])"
)
_UNSUPPORTED_CAUSAL = re.compile(
    r"\b(caused|led\s+to|resulted\s+in|proves?|definitely\s+(?:fixes|improves))\b"
    r"|반드시\s*개선|원인(?:이|이다|으로)|이\s*때문에\s*(?:증가|감소)|상충\s*evidence가\s*없|반대\s*evidence가\s*존재하지\s*않",
    re.IGNORECASE,
)
_UNSUPPORTED_COUNTER_EVIDENCE_ABSENCE = re.compile(
    r"상충\s*evidence가\s*없|반대\s*evidence가\s*존재하지\s*않",
    re.IGNORECASE,
)
_PROSPECTIVE_RISK_CERTAINTY = re.compile(
    r"\b(?:caused|led\s+to|resulted\s+in|proves?)\b"
    r"|\b(?:causes|leads\s+to|results\s+in|increases|decreases|reduces|worsens|improves)\b"
    r"|\b(?:will|must|definitely|definitively|certainly)\b"
    r"(?:\s+[A-Za-z-]+){0,3}\s+"
    r"(?:cause|lead\s+to|result\s+in|increase|decrease|reduce|worsen|improve)\b"
    r"|\b(?:is|was|are|were)\s+(?:the\s+)?(?:reason|cause)\b"
    r"|(?:반드시|확실히|명백히)"
    r"|원인(?:이다|이었다|으로\s*(?:작용한다|이어진다))"
    r"|때문(?:이다|이었다|에.{0,80}(?:증가|감소|악화|개선|높아|낮아|줄어|늘어)"
    r".{0,40}(?:한다|된다|했다|됐다))"
    r"|(?:초래|유발)(?:한다|했다|된다|됐다)"
    r"|(?:증가|감소|악화|개선)(?:한다|했다|된다|됐다)"
    r"|(?:높인|낮춘|줄인|늘린)다",
    re.IGNORECASE,
)
_INVESTIGATION_GAP_CAUSAL_MARKER = re.compile(
    r"\b(?:caus(?:e|ed|es|al)|contribut(?:e|ed|es)|affect(?:ed|s)?|"
    r"impact(?:ed|s)?|explain(?:ed|s)?|because\s+of|led\s+to|resulted\s+in|"
    r"increase(?:d|s)?|decrease(?:d|s)?|reduce(?:d|s)?|worsen(?:ed|s)?)\b"
    r"|(?:원인|인과|영향|때문|유발|초래|높였|낮췄|줄였|떨어뜨렸|증가했|감소했|악화됐)",
    re.IGNORECASE,
)
_INVESTIGATION_GAP_CERTAINTY = re.compile(
    r"\b(?:definitely|definitively|certainly|must|will)\b.{0,100}"
    r"\b(?:cause|lead\s+to|result\s+in|contribute|affect|impact|explain|"
    r"increase|decrease|reduce|worsen)\b"
    r"|(?:반드시|확실히|명백히).{0,100}"
    r"(?:원인|영향|때문|유발|초래|증가|감소|악화|높|낮|줄|떨어뜨)",
    re.IGNORECASE,
)
_INVESTIGATION_GAP_CONTEXT = re.compile(
    r"\bwhether\b.{0,180}\b(?:caus(?:e|ed|es)|contribut(?:e|ed|es)|"
    r"affect(?:ed|s)?|impact(?:ed|s)?|explain(?:ed|s)?)\b"
    r"|\b(?:causal\s+relationship|causal\s+link|cause|impact|effect)\b"
    r".{0,140}\b(?:unclear|unknown|unresolved|not\s+(?:yet\s+)?established)\b"
    r"|\b(?:cannot|can't|not\s+possible|insufficient|additional\s+evidence|"
    r"more\s+data|data\s+does\s+not)\b.{0,180}"
    r"\b(?:determine|verify|test|investigate|distinguish)\b"
    r"|(?:원인(?:이|인지|\s*여부)|인과(?:관계)?(?:가|적인지|인지|\s*여부)|"
    r"영향(?:을)?\s*(?:주는지|미치는지|여부)|때문인지|유발하는지|초래하는지)"
    r".{0,160}(?:판단하기\s*어렵|판단할\s*수\s*없|구분하기\s*어렵|확인(?:할|이)?\s*(?:필요|해야)|"
    r"검증(?:할|이)?\s*(?:필요|해야)|구분할\s*수\s*없|근거가\s*필요|"
    r"(?:자료|데이터|telemetry)가\s*부족|확인되지\s*않|불명확|알\s*수\s*없)"
    r"|(?:판단하기\s*어렵|판단할\s*수\s*없|구분하기\s*어렵|확인(?:할|이)?\s*(?:필요|해야)|"
    r"검증(?:할|이)?\s*(?:필요|해야)|구분할\s*수\s*없|근거가\s*부족|"
    r"확인되지\s*않|불명확|알\s*수\s*없).{0,160}"
    r"(?:원인인지|원인\s*여부|인과(?:관계)?|영향\s*여부|때문인지)",
    re.IGNORECASE,
)
_INVESTIGATION_GAP_HARD_ASSERTION = re.compile(
    r"\b(?:increased|decreased|reduced|worsened)\b.{0,80}\bbecause\s+of\b"
    r"|\bbecause\s+of\b.{0,80}\b(?:increased|decreased|reduced|worsened)\b"
    r"|원인(?:이다|이었다)"
    r"|때문에.{0,100}(?:증가|감소|악화|높|낮|줄|떨어뜨).{0,40}(?:했|됐|한다|된다)"
    r"|(?:유발|초래)(?:했|됐다|한다|된다)"
    r"|(?:영향으로|영향을\s*받아).{0,100}(?:발생|증가|감소|악화|높|낮|줄)"
    r"|(?:높였|낮췄|줄였|떨어뜨렸|증가시켰|감소시켰)(?:다|습니다)?",
    re.IGNORECASE,
)
_INVESTIGATION_GAP_ASSERTION = re.compile(
    r"\b(?:caused|led\s+to|resulted\s+in|reduced|increased|decreased|worsened)\b"
    r"|\bbecause\s+of\b"
    r"|\b(?:is|was|are|were)\s+(?:the\s+)?(?:reason|cause)\b"
    r"|원인(?:이다|이었다|으로\s*(?:발생|작용|이어))"
    r"|때문에.{0,100}(?:증가|감소|악화|높|낮|줄|떨어뜨).{0,40}(?:했|됐|한다|된다)"
    r"|(?:유발|초래)(?:했|됐|한다|된다)"
    r"|(?:영향으로|영향을\s*받아).{0,100}(?:발생|증가|감소|악화|높|낮|줄)"
    r"|(?:높였|낮췄|줄였|떨어뜨렸|증가시켰|감소시켰)(?:다|습니다)?",
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


def prohibited_language(
    text: str,
    *,
    causal_mode: CausalMode = CausalMode.DEFAULT,
) -> str | None:
    if causal_mode is CausalMode.PROSPECTIVE_RISK:
        match = (
            _UNSUPPORTED_COUNTER_EVIDENCE_ABSENCE.search(text)
            or _PROSPECTIVE_RISK_CERTAINTY.search(text)
        )
    elif causal_mode is CausalMode.INVESTIGATION_GAP:
        absence = _UNSUPPORTED_COUNTER_EVIDENCE_ABSENCE.search(text)
        if absence:
            return absence.group(0)
        causal = _INVESTIGATION_GAP_CAUSAL_MARKER.search(text)
        if not causal:
            return None
        certainty = _INVESTIGATION_GAP_CERTAINTY.search(text)
        if certainty:
            return certainty.group(0)
        hard_assertion = _INVESTIGATION_GAP_HARD_ASSERTION.search(text)
        if hard_assertion:
            return hard_assertion.group(0)
        if _INVESTIGATION_GAP_CONTEXT.search(text):
            return None
        match = _INVESTIGATION_GAP_ASSERTION.search(text)
    else:
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
