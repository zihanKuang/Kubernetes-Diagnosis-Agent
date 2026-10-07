"""Deterministic process stamp. No LLM. Appended after the agent's answer.

This module records whether tools ran, whether obs-N strings match the
ledger, and whether a structured recovery verdict exists. It does not
verify root cause. A HIGH stamp, a matching citation, or a
validate_recovery call count is not RCA quality.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .stamps import iso_utc


RCA_HINTS = (
    "rca",
    "what happened",
    "what just happened",
    "incident",
    "killed",
    "crash",
    "recover",
    "outage",
    "unhealthy",
)

EVIDENCE_TOOLS = (
    "list_pods",
    "get_recent_events",
    "get_pod_status",
    "get_pod_logs",
    "query_prometheus",
    "validate_recovery",
)

REF_VALID = "reference_valid"
REF_INVALID = "reference_invalid"
REF_UNVERIFIABLE = "unverifiable"
DIAGNOSIS_NOT_REVIEWED = "not_reviewed"
RECOVERY_UNKNOWN = "unknown"
RECOVERY_PASS = "pass"
RECOVERY_FAIL = "fail"

SCAN_SCOPE_PODS = (
    "pods in this list_pods scan currently match the checked "
    "Ready/phase/state conditions; does not prove no incident occurred, "
    "request-path health, or cluster-wide health"
)

_OBS_ID = re.compile(r"\bobs-\d+\b", re.I)
_OBJECT = re.compile(
    r"\b(Pod|Deployment|ReplicaSet|Service)/([A-Za-z0-9][A-Za-z0-9.-]*)",
    re.I,
)
_WINDOW = re.compile(r"window=(\S+)", re.I)
FOOTER_MARK = "\n---\n"


def split_answer(text: str) -> tuple[str, str]:
    """Body is published diagnosis. Footer is the evidence stamp."""
    if FOOTER_MARK in (text or ""):
        body, footer = text.rsplit(FOOTER_MARK, 1)
        return body.strip(), footer.strip()
    return (text or "").strip(), ""


@dataclass(frozen=True)
class EvidenceClaim:
    status: str  # reference_valid | reference_invalid | unverifiable
    obs_id: str
    object_ref: str = ""
    window: str = ""
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "obs_id": self.obs_id,
            "object_ref": self.object_ref,
            "window": self.window,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EvidenceCheck:
    level: str  # HIGH | MEDIUM | LOW — process stamp only
    reasons: List[str]
    tool_calls: int
    errors: int
    tools_used: Dict[str, int]
    claims: List[EvidenceClaim] = field(default_factory=list)
    diagnosis_status: str = DIAGNOSIS_NOT_REVIEWED
    recovery_status: str = RECOVERY_UNKNOWN
    scan_scope: str = ""

    def diagnosis_passed(self) -> bool:
        return self.diagnosis_status == "accepted"

    def footer(self) -> str:
        lines = [
            f"Evidence check: {self.level} (process stamp, not RCA verification)",
            (
                f"- diagnosis: {self.diagnosis_status} — no semantic review; "
                "a matching citation is not root-cause verification"
            ),
            f"- recovery: {self.recovery_status}",
        ]
        if self.scan_scope:
            lines.append(f"- scan: {self.scan_scope}")
        for reason in self.reasons:
            lines.append(f"- {reason}")
        used = ", ".join(f"{name}×{n}" for name, n in sorted(self.tools_used.items())) or "none"
        lines.append(f"- tools used: {used}  errors: {self.errors}")
        if self.claims:
            valid = sum(1 for c in self.claims if c.status == REF_VALID)
            invalid = sum(1 for c in self.claims if c.status == REF_INVALID)
            unverifiable = sum(1 for c in self.claims if c.status == REF_UNVERIFIABLE)
            lines.append(
                f"- references: valid={valid} invalid={invalid} "
                f"unverifiable={unverifiable}"
            )
            for claim in self.claims:
                extra = f" {claim.object_ref}" if claim.object_ref else ""
                win = f" window={claim.window}" if claim.window else ""
                why = f" ({claim.reason})" if claim.reason else ""
                lines.append(f"- {claim.status} {claim.obs_id}{extra}{win}{why}")
        return "\n".join(lines)


def _is_rca_query(query: str) -> bool:
    q = query.lower()
    return any(hint in q for hint in RCA_HINTS)


def _answer_body(answer: str) -> str:
    text = answer or ""
    if FOOTER_MARK in text:
        return text.rsplit(FOOTER_MARK, 1)[0]
    return text


_RECOVERY_KV = re.compile(
    r"^(status|reason|checked_at|matched|ready|required|scope)=(\S+)\s*$",
    re.I | re.M,
)


def parse_recovery_verdict(text: str) -> Dict[str, str]:
    """PASS/FAIL/UNKNOWN from the validate_recovery payload. Call count is not a verdict."""
    head = (text or "").lstrip()
    out: Dict[str, str] = {"status": RECOVERY_UNKNOWN}
    if head.startswith("PASS"):
        out["status"] = RECOVERY_PASS
    elif head.startswith("FAIL"):
        out["status"] = RECOVERY_FAIL
    elif head.startswith("UNKNOWN"):
        out["status"] = RECOVERY_UNKNOWN
    for match in _RECOVERY_KV.finditer(text or ""):
        out[match.group(1).lower()] = match.group(2)
    status = str(out.get("status") or "").strip().lower()
    if status in {RECOVERY_PASS, RECOVERY_FAIL, RECOVERY_UNKNOWN}:
        out["status"] = status
    else:
        out["status"] = RECOVERY_UNKNOWN
    return out


def recovery_status_from_stats(stats: Dict[str, Any]) -> str:
    raw = (stats or {}).get("recovery")
    if isinstance(raw, dict):
        status = str(raw.get("status") or "").strip().lower()
    elif isinstance(raw, str):
        status = raw.strip().lower()
    else:
        status = ""
    if status in {RECOVERY_PASS, RECOVERY_FAIL, RECOVERY_UNKNOWN}:
        return status
    return RECOVERY_UNKNOWN


def bind_claims(answer: str, observations: List[Dict[str, Any]]) -> List[EvidenceClaim]:
    """Match obs-N / OBJECT / window against FOCUS rows. Not a semantic verdict."""
    body = _answer_body(answer)
    by_id = {(str(row.get("id") or "").strip().lower()): row for row in observations}
    claims: List[EvidenceClaim] = []
    cited: set[str] = set()

    for match in _OBS_ID.finditer(body):
        obs_id = match.group(0).lower()
        if obs_id in cited:
            continue
        cited.add(obs_id)
        snippet = _line_around(body, match.start(), match.end())
        cited_object = _object_in(snippet)
        cited_window = _window_in(snippet)
        row = by_id.get(obs_id)
        if row is None:
            claims.append(
                EvidenceClaim(
                    REF_UNVERIFIABLE,
                    obs_id,
                    cited_object or "",
                    cited_window or "",
                    "unknown observation",
                )
            )
            continue
        ledger_ref = _object_ref(row)
        ledger_window = str(row.get("window") or "current")
        if cited_object and not _object_matches(cited_object, row):
            claims.append(
                EvidenceClaim(
                    REF_INVALID,
                    obs_id,
                    cited_object,
                    cited_window or ledger_window,
                    "OBJECT does not match ledger",
                )
            )
            continue
        if cited_window and not _window_matches(cited_window, ledger_window):
            claims.append(
                EvidenceClaim(
                    REF_INVALID,
                    obs_id,
                    cited_object or ledger_ref,
                    cited_window,
                    "window does not match ledger",
                )
            )
            continue
        claims.append(
            EvidenceClaim(
                REF_VALID,
                obs_id,
                cited_object or ledger_ref,
                cited_window or ledger_window,
                "citation string matches ledger",
            )
        )

    for row in observations:
        obs_id = str(row.get("id") or "").strip().lower()
        if not obs_id or obs_id in cited:
            continue
        claims.append(
            EvidenceClaim(
                REF_UNVERIFIABLE,
                obs_id,
                _object_ref(row),
                str(row.get("window") or "current"),
                "FOCUS object not cited",
            )
        )
    return claims


def assess(query: str, answer: str, stats: Dict[str, Any]) -> EvidenceCheck:
    """Grade process and record citations. Does not accept a diagnosis."""
    tools_used: Dict[str, int] = dict(stats.get("tool_calls") or {})
    tool_calls = sum(tools_used.values())
    errors = int(stats.get("errors") or 0)
    reasons: List[str] = []
    level = "HIGH"
    claims: List[EvidenceClaim] = []
    scan_scope = ""
    recovery = recovery_status_from_stats(stats)

    if not (answer or "").strip():
        return EvidenceCheck("LOW", ["empty answer"], tool_calls, errors, tools_used)

    if tool_calls == 0:
        return EvidenceCheck(
            "LOW",
            ["0 live tool calls — answer is not grounded in cluster state"],
            tool_calls,
            errors,
            tools_used,
        )

    if errors * 2 >= tool_calls:
        level = "LOW"
        reasons.append(
            f"tool/LLM error rate high ({errors} errors / {tool_calls} tool calls)"
        )

    evidence_calls = sum(tools_used.get(name, 0) for name in EVIDENCE_TOOLS)
    if evidence_calls == 0:
        level = "LOW"
        reasons.append("called tools, but none of them inspect cluster state")

    # FOCUS-empty after list_pods is a finished scan of current pod conditions.
    # RCA-style wording must not force get_recent_events / validate_recovery.
    require_rca_followup = _is_rca_query(query) and not _healthy_scan(stats, tools_used)

    if require_rca_followup and tools_used.get("validate_recovery", 0) == 0:
        if level == "HIGH":
            level = "MEDIUM"
        reasons.append(
            "RCA-style question but validate_recovery was never called"
        )

    if require_rca_followup and tools_used.get("get_recent_events", 0) == 0:
        if level == "HIGH":
            level = "MEDIUM"
        reasons.append("RCA-style question but get_recent_events was never called")

    if tools_used.get("validate_recovery", 0) > 0 and "recovery" not in (stats or {}):
        reasons.append(
            "validate_recovery ran; no structured verdict — recovery unknown"
        )

    diagnosis = (stats or {}).get("diagnosis") or {}
    diag_status = str(diagnosis.get("status") or "").strip()
    if diag_status == "draft_invalid":
        reasons.append("diagnosis draft invalid; free text was not published")
    elif diag_status == "citation_invalid":
        reasons.append("some diagnosis claims have citations outside this run")

    observations: Optional[List[Dict[str, Any]]]
    if "observations" in stats:
        observations = list(stats.get("observations") or [])
    else:
        observations = None
    if observations:
        claims = bind_claims(answer, observations)

    if _healthy_scan(stats, tools_used):
        scan_scope = SCAN_SCOPE_PODS
        if not reasons:
            reasons.append(SCAN_SCOPE_PODS)
    elif not reasons:
        reasons.append("live tools succeeded")

    return EvidenceCheck(
        level,
        reasons,
        tool_calls,
        errors,
        tools_used,
        claims,
        DIAGNOSIS_NOT_REVIEWED,
        recovery,
        scan_scope,
    )


def _healthy_scan(stats: Dict[str, Any], tools_used: Dict[str, int]) -> bool:
    """Ledger scanned the namespace and marked nothing to investigate."""
    if "focus" not in stats:
        return False
    if int(tools_used.get("list_pods") or 0) <= 0:
        return False
    return not stats.get("focus")


def _line_around(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return text[line_start:line_end]


def _object_in(snippet: str) -> str:
    match = _OBJECT.search(snippet or "")
    if not match:
        return ""
    return f"{match.group(1)}/{match.group(2)}"


def _window_in(snippet: str) -> str:
    match = _WINDOW.search(snippet or "")
    if not match:
        return ""
    return match.group(1).rstrip(".,;)]")


def _object_ref(row: Dict[str, Any]) -> str:
    ref = str(row.get("object_ref") or "").strip()
    if ref:
        return ref
    kind = str(row.get("kind") or "").strip()
    name = str(row.get("name") or "").strip()
    if kind and name:
        return f"{kind}/{name}"
    return name


def _object_matches(cited: str, row: Dict[str, Any]) -> bool:
    cited_l = cited.strip().lower()
    ref = _object_ref(row).lower()
    if cited_l == ref:
        return True
    name = str(row.get("name") or "").strip().lower()
    if name and (cited_l == name or cited_l.endswith("/" + name)):
        return True
    return False


def _window_matches(cited: str, ledger: str) -> bool:
    a = iso_utc(cited).strip().lower()
    b = iso_utc(ledger or "current").strip().lower()
    if a == b:
        return True
    return bool(a) and bool(b) and (a in b or b in a)


def attach_footer(answer: str, check: EvidenceCheck) -> str:
    return f"{answer.rstrip()}\n\n---\n{check.footer()}\n"
