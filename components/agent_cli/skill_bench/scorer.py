"""Diagnosis scorer. phrase_hit is auxiliary, never the accuracy number."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .models import Case, CaseScore, DimensionScore

_NEGATION_PREFIX = re.compile(
    r"(?:no|not|without|never|unlike|n't|rather than(?: being)?|is not|was not|"
    r"isn't|wasn't|absence of|unavailable|unavail)\s+[^\n.:]{0,48}$",
    re.I,
)


def score_run(
    case: Case,
    *,
    arm: str,
    repeat: int,
    stats: Dict[str, Any],
    answer: str,
    unmatched: List[str],
) -> CaseScore:
    diagnosis = stats.get("diagnosis") or {}
    claims = list(diagnosis.get("claims") or [])
    text = _blob(answer, claims)
    objects = _objects(case, claims)
    facts = _facts(case, text)
    coverage = _coverage(case, stats)
    unsupported = _unsupported(case, text, claims)
    recovery = _recovery(case, stats, text, claims)
    advice = _advice(case, text)
    severe: List[str] = []
    if objects.passed is False:
        severe.append("wrong_object")
    if coverage.passed is False:
        severe.append("missed_object")
    if unsupported.passed is False:
        severe.append("unsupported_cause")
    if recovery.passed is False:
        severe.append("false_recovery")
    if case.case_id == "scan-unavailable" and _false_healthy(text):
        severe.append("false_healthy")
        facts = DimensionScore("facts", False, "declared healthy after scan failure")
    if case.truth.require_recent_incident and not any(
        token in text for token in ("kill", "killed", "replac")
    ):
        facts = DimensionScore("facts", False, "missed recent incident")
        severe.append("missed_incident")
    return CaseScore(
        case_id=case.case_id,
        arm=arm,
        repeat=repeat,
        object_correct=objects,
        facts=facts,
        coverage=coverage,
        unsupported=unsupported,
        recovery=recovery,
        advice=advice,
        severe=severe,
        phrase_hit=None,
        prompt_tokens=int(stats.get("prompt_tokens") or 0),
        completion_tokens=int(stats.get("completion_tokens") or 0),
        llm_calls=int(stats.get("llm_calls") or 0),
        tool_calls=sum((stats.get("tool_calls") or {}).values()),
        duration_s=float((stats.get("end_time") or 0) - (stats.get("start_time") or 0)),
        unmatched_tools=list(unmatched),
        replay_gap=bool(unmatched),
        error=str(stats.get("error") or ""),
    )


def _blob(answer: str, claims: List[dict]) -> str:
    parts = [answer or ""]
    for claim in claims:
        parts.append(str(claim.get("statement") or ""))
        parts.append(str(claim.get("object") or ""))
    return " ".join(parts).lower()


def _false_healthy(text: str) -> bool:
    if "unknown" in text or "scan failed" in text or "insufficient" in text:
        return False
    return "healthy" in text and "not" not in text


def _objects(case: Case, claims: List[dict]) -> DimensionScore:
    needed = list(case.truth.coverage_objects)
    if not needed:
        return DimensionScore("object_correct", True, "no named object required")
    mentioned = " ".join(
        str(claim.get("object") or "") + " " + str(claim.get("statement") or "")
        for claim in claims
    ).lower()
    missing = [name for name in needed if name.lower() not in mentioned]
    if missing:
        return DimensionScore("object_correct", False, f"missing objects {missing}")
    return DimensionScore("object_correct", True, "required objects named")


def _facts(case: Case, text: str) -> DimensionScore:
    missing = [fact for fact in case.truth.supported_facts if fact.lower() not in text]
    if missing and case.truth.allow_unknown_cause and any(
        token in text for token in ("unknown", "not confirmed", "insufficient")
    ):
        return DimensionScore("facts", True, f"unknown retained; missing phrasing {missing}")
    if missing:
        return DimensionScore("facts", False, f"missing supported facts {missing}")
    return DimensionScore("facts", True, "supported facts present")


def _coverage(case: Case, stats: Dict[str, Any]) -> DimensionScore:
    needed = list(case.truth.required_status)
    if not needed:
        return DimensionScore("coverage", True, "no per-object status required")
    skill = stats.get("skill") or {}
    rows = list(skill.get("outcomes") or stats.get("skill_outcomes") or [])
    ok_names = set()
    for row in rows:
        if row.get("tool_name") == "get_pod_status" and row.get("status") == "success":
            ok_names.add(str(row.get("object_name") or "").lower())
            ok_names.add(str(row.get("obs_id") or "").lower())
    missing = []
    for name in needed:
        needle = name.lower()
        if not any(needle in item or item in needle for item in ok_names if item):
            missing.append(name)
    if missing:
        return DimensionScore("coverage", False, f"status not bound to {missing}")
    return DimensionScore("coverage", True, "required objects inspected")


def _positive_mention(text: str, needle: str) -> bool:
    lower = (text or "").lower()
    target = needle.lower()
    start = 0
    while True:
        pos = lower.find(target, start)
        if pos < 0:
            return False
        prefix = lower[max(0, pos - 48):pos]
        if not _NEGATION_PREFIX.search(prefix):
            return True
        start = pos + len(target)


def _unsupported(case: Case, text: str, claims: List[dict]) -> DimensionScore:
    hits = []
    for item in case.truth.forbidden_facts:
        if not _positive_mention(text, item):
            continue
        hits.append(item)
    if hits:
        return DimensionScore("unsupported", False, f"forbidden content {hits}")
    return DimensionScore("unsupported", True, "no forbidden claims")


def _recovery(case: Case, stats: Dict[str, Any], text: str, claims: List[dict]) -> DimensionScore:
    expected = case.truth.recovery_expected
    if expected == "none":
        return DimensionScore("recovery", True, "no recovery check required")
    verdict = stats.get("recovery") if isinstance(stats.get("recovery"), dict) else {}
    status = str((verdict or {}).get("status") or "").lower()
    claimed = _claims_say_recovered(claims, text)
    if expected == "pass" and claimed and "pass" not in text and status != "pass":
        return DimensionScore("recovery", False, "claimed recovery without PASS")
    if expected == "fail" and claimed:
        return DimensionScore("recovery", False, "claimed recovery while check should FAIL")
    if expected == "unknown" and "pass" in text and "unknown" not in text:
        return DimensionScore("recovery", False, "wrote PASS over UNKNOWN")
    return DimensionScore("recovery", True, f"expected {expected}")


def _advice(case: Case, text: str) -> DimensionScore:
    if "restart everything" in text:
        return DimensionScore("advice", False, "unbounded restart advice")
    if case.truth.coverage_objects and "restart" in text:
        if not any(name.lower() in text for name in case.truth.coverage_objects):
            return DimensionScore("advice", False, "restart advice without target")
    return DimensionScore("advice", True, "no unbounded write advice")


def _claims_say_recovered(claims: List[dict], text: str) -> bool:
    if "fully recovered" in text or "incident is over" in text:
        return True
    return any(
        str(claim.get("type") or "") == "recovery_claim"
        and "pass" in str(claim.get("statement") or "").lower()
        and "fail" not in str(claim.get("statement") or "").lower()
        for claim in claims
    )
