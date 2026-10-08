"""Tool capabilities and risk. Never inferred from free-text descriptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Set

from .errors import SkillError

CAP_SCAN = "scan"
CAP_INSPECT = "inspect"
CAP_LOGS = "logs"
CAP_VERIFY = "verify"
CAP_METRICS = "metrics"
CAP_WRITE = "write"

RISK_READ = "read"
RISK_WRITE = "write"


@dataclass(frozen=True)
class ToolCapability:
    name: str
    capability: str
    risk: str


KNOWN_TOOLS: Dict[str, ToolCapability] = {
    "list_pods": ToolCapability("list_pods", CAP_SCAN, RISK_READ),
    "get_recent_events": ToolCapability("get_recent_events", CAP_SCAN, RISK_READ),
    "get_pod_status": ToolCapability("get_pod_status", CAP_INSPECT, RISK_READ),
    "get_pod_logs": ToolCapability("get_pod_logs", CAP_LOGS, RISK_READ),
    "validate_recovery": ToolCapability("validate_recovery", CAP_VERIFY, RISK_READ),
    "query_prometheus": ToolCapability("query_prometheus", CAP_METRICS, RISK_READ),
    "restart_deployment": ToolCapability("restart_deployment", CAP_WRITE, RISK_WRITE),
    "scale_deployment": ToolCapability("scale_deployment", CAP_WRITE, RISK_WRITE),
    "rollback_deployment": ToolCapability("rollback_deployment", CAP_WRITE, RISK_WRITE),
}

DEFAULT_READ_POLICY = frozenset({
    CAP_SCAN, CAP_INSPECT, CAP_LOGS, CAP_VERIFY, CAP_METRICS,
})


def capability_for(name: str) -> ToolCapability | None:
    return KNOWN_TOOLS.get(name)


def allowed_tools(
    *,
    catalog_names: Sequence[str],
    requested: Sequence[str],
    policy_capabilities: Iterable[str],
    allow_writes: bool,
) -> List[str]:
    """Skill names are requests. Policy and catalog grant permission."""
    catalog = set(catalog_names)
    allowed_caps = set(policy_capabilities)
    if not allow_writes:
        allowed_caps.discard(CAP_WRITE)
    out: List[str] = []
    seen: Set[str] = set()
    for name in requested:
        if name in seen:
            continue
        spec = KNOWN_TOOLS.get(name)
        if spec is None:
            continue
        if spec.capability not in allowed_caps:
            continue
        if spec.risk == RISK_WRITE and not allow_writes:
            continue
        if name not in catalog:
            continue
        seen.add(name)
        out.append(name)
    return out


def deny_unknown_or_hidden(name: str, allowed: Sequence[str]) -> str:
    if name in allowed:
        return ""
    spec = KNOWN_TOOLS.get(name)
    if spec is None:
        return f"DENIED: tool {name!r} is not in the skill allow-list (unknown tools are not auto-enabled)."
    return f"DENIED: tool {name!r} is not enabled for the current skill stage."


def scan_tools_for_intent(intent: str) -> List[str]:
    if intent == "health_check":
        return ["list_pods"]
    return ["list_pods", "get_recent_events"]


def initial_stage_tools(intent: str) -> List[str]:
    return scan_tools_for_intent(intent)
