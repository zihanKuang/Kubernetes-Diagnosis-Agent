"""Read-only observation snapshot for routing. Distinct from eval ground truth."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from .models import (
    SCAN_EMPTY,
    SCAN_FAILED,
    SCAN_NOT_SCANNED,
    SCAN_SUCCESS,
    ObjectFacts,
    ObservationSnapshot,
    ToolOutcome,
)

_ERROR_HEAD = ("error", "unexpected error")


def tool_text_failed(text: str) -> bool:
    head = (text or "").lstrip().lower()
    return head.startswith(_ERROR_HEAD)


_WAITING = re.compile(r"waiting=([A-Za-z0-9_-]+)", re.I)
_LAST_TERM = re.compile(r"last_termination=([A-Za-z0-9_-]+)", re.I)
_STATE = re.compile(r"\bState:\s*(\S+)", re.I)
_PHASE = re.compile(r"\bStatus:\s*(\S+)", re.I)
_POD = re.compile(r"^Pod:\s*(\S+)", re.I)


def snapshot_from_ledger(
    ledger: Any,
    outcomes: Iterable[ToolOutcome],
) -> ObservationSnapshot:
    rows = list(outcomes)
    pods_ok, pods_failed = _tool_flags(rows, "list_pods")
    events_ok, _events_failed = _tool_flags(rows, "get_recent_events")
    objects = list(getattr(ledger, "objects", []) or [])
    pods = [obs for obs in objects if getattr(obs, "kind", "") == "Pod"]
    focus_obs = [obs for obs in objects if getattr(obs, "focus", False)]

    if pods_failed and not pods_ok:
        scan_status = SCAN_FAILED
    elif not pods_ok:
        scan_status = SCAN_NOT_SCANNED
    elif not pods:
        scan_status = SCAN_EMPTY
    else:
        scan_status = SCAN_SUCCESS

    focus = [_facts_from_obs(obs) for obs in focus_obs]
    collected = ""
    for obs in objects:
        stamp = getattr(obs, "collected_at", "") or ""
        if stamp:
            collected = stamp
            break
    return ObservationSnapshot(
        scan_status=scan_status,
        collected_at=collected,
        focus=focus,
        context_count=sum(1 for obs in pods if not getattr(obs, "focus", False)),
        observation_ids=[getattr(obs, "id", "") for obs in objects if getattr(obs, "id", "")],
        list_pods_ok=pods_ok,
        events_ok=events_ok,
    )


def enrich_from_status(text: str) -> List[Dict[str, str]]:
    """Parse get_pod_status into per-Pod blocks. Never merge two pods."""
    blocks: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in (text or "").splitlines():
        pod = _POD.match(line.strip())
        if pod:
            if current.get("name"):
                blocks.append(current)
            current = {"name": pod.group(1)}
            continue
        waiting = _WAITING.search(line)
        last_term = _LAST_TERM.search(line)
        state = _STATE.search(line)
        phase = _PHASE.search(line)
        if waiting:
            current["waiting_reason"] = waiting.group(1)
        if last_term:
            current["last_termination"] = last_term.group(1)
        if state:
            current["state"] = state.group(1)
        if phase:
            current["phase"] = phase.group(1)
    if current.get("name"):
        blocks.append(current)
    return blocks


def apply_status(facts: ObjectFacts, parsed: Dict[str, str]) -> ObjectFacts:
    if parsed.get("waiting_reason"):
        facts.waiting_reason = parsed["waiting_reason"]
    if parsed.get("last_termination"):
        facts.last_termination = parsed["last_termination"]
    if parsed.get("state"):
        facts.state = parsed["state"]
    if parsed.get("phase"):
        facts.phase = parsed["phase"]
    return facts


def _facts_from_obs(obs: Any) -> ObjectFacts:
    reason = str(getattr(obs, "focus_reason", "") or "")
    event_reasons = []
    if reason.lower().startswith("event "):
        parts = reason.split()
        if len(parts) >= 2:
            event_reasons.append(parts[1])
    state = str(getattr(obs, "state", "") or "")
    return ObjectFacts(
        obs_id=str(getattr(obs, "id", "")),
        name=str(getattr(obs, "name", "")),
        component=str(getattr(obs, "component", "") or ""),
        phase=str(getattr(obs, "phase", "") or ""),
        state=state,
        ready=getattr(obs, "ready", None),
        restarts=int(getattr(obs, "restarts", 0) or 0),
        present=bool(getattr(obs, "present", True)),
        waiting_reason=state,
        last_termination="",
        event_reasons=event_reasons,
        uid=str(getattr(obs, "uid", "") or ""),
        window=str(getattr(obs, "window", "") or ""),
    )


def _tool_flags(rows: List[ToolOutcome], name: str) -> tuple[bool, bool]:
    ok = False
    failed = False
    for row in rows:
        if row.tool_name != name:
            continue
        if row.status == "success" and not tool_text_failed(row.text):
            ok = True
        elif row.status in {"error", "timeout"} or tool_text_failed(row.text):
            failed = True
    return ok, failed
