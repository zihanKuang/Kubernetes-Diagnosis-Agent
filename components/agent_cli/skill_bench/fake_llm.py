"""Scripted investigator for wiring tests. Not a quality diagnosis model."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

_OBS = re.compile(r"\bobs-\d+\b")


class FakeInvestigator:
    """Calls scan, then status, then logs/verify. Scores from this client are invalid."""

    def __init__(self):
        self.seen_tools: List[List[str]] = []
        self.seen_instructions: List[Any] = []
        self.phase = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}

    def reset_usage(self) -> None:
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}

    async def generate_with_tools(self, messages, tools, **kwargs):
        names = [
            (item.get("function") or {}).get("name")
            for item in (tools or [])
            if (item.get("function") or {}).get("name")
        ]
        self.seen_tools.append(names)
        self.seen_instructions.append(kwargs.get("system_instruction"))
        self.usage["llm_calls"] += 1
        blob = json.dumps(messages, default=str)
        available = set(names)
        if self.phase == 0 and "list_pods" in available:
            self.phase = 1
            calls = [_call("list_pods", {}, "scan-pods")]
            if "get_recent_events" in available:
                calls.append(_call("get_recent_events", {}, "scan-events"))
            return {"content": None, "tool_calls": calls}
        obs_ids = list(dict.fromkeys(_OBS.findall(blob)))
        if self.phase == 1 and "get_pod_status" in available:
            self.phase = 2
            if not obs_ids:
                obs_ids = ["obs-1"]
            calls = [
                _call("get_pod_status", {"from_obs": oid}, f"st-{oid}")
                for oid in obs_ids
            ]
            return {"content": None, "tool_calls": calls}
        if self.phase == 2 and ({"get_pod_logs", "validate_recovery"} & available):
            self.phase = 3
            target = obs_ids[0] if obs_ids else "obs-1"
            calls = []
            if "get_pod_logs" in available:
                calls.append(_call("get_pod_logs", {"from_obs": target}, "logs-1"))
            if "validate_recovery" in available:
                calls.append(_call("validate_recovery", {"from_obs": target}, "ver-1"))
            if calls:
                return {"content": None, "tool_calls": calls}
        return {"content": _final_from_tools(blob), "tool_calls": []}


def _call(name: str, args: dict, cid: str) -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _final_from_tools(blob: str) -> str:
    text = blob.lower()
    claims = []
    unknowns: List[str] = []
    if "error: timed out" in text or "timed out talking" in text:
        claims.append(_claim(
            "scan failed; cluster state is unknown",
            "observation",
            "namespace/citrus",
        ))
        unknowns.append("current namespace health")
    elif "oomkilled" in text:
        claims.append(_claim("last_termination is OOMKilled", "causal_claim", "FOCUS"))
        claims.append(_claim("waiting CrashLoopBackOff after the kill", "symptom", "FOCUS"))
    elif "imagepullbackoff" in text or "failed to pull image" in text:
        claims.append(_claim("ImagePullBackOff; failed to pull image", "symptom", "FOCUS"))
    elif "failedscheduling" in text or "nodeselector" in text:
        claims.append(_claim("Pending with FailedScheduling / nodeSelector", "symptom", "FOCUS"))
    elif "readiness probe" in text or "probe failed" in text or "runningnotready" in text:
        claims.append(_claim("RunningNotReady; readiness probe failed; zero restarts", "symptom", "FOCUS"))
    elif "crashloopbackoff" in text:
        claims.append(_claim("CrashLoopBackOff; exit 1; cause unknown", "symptom", "FOCUS"))
        unknowns.append("exit cause")
    elif "killing" in text:
        claims.append(_claim("recent kill then replacement pod Ready", "observation", "FOCUS"))
        if "pass:" in text:
            claims.append(_claim("validate_recovery PASS at checked_at", "recovery_claim", "FOCUS"))
    else:
        claims.append(_claim("current pods Ready; no ongoing incident", "observation", "namespace/citrus"))
    if "fail:" in text and claims:
        claims.append(_claim("validate_recovery FAIL; not Ready", "recovery_claim", "FOCUS"))
    return json.dumps({"scope": "citrus", "unknowns": unknowns, "claims": claims})


def _claim(statement: str, kind: str, obj: str) -> dict:
    return {
        "id": "c1",
        "statement": statement,
        "type": kind,
        "object": obj,
        "window": "current",
        "obs": [],
        "evidence": [],
        "confidence": "medium",
    }
