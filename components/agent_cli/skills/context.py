"""Build RequestContext from CLI, interactive, webhook, and eval entrypoints."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from .intents import detect_intent
from .models import RequestContext
from .settings import MODE_LEGACY

_WORKLOAD = re.compile(
    r"\b(citrus-[a-z0-9-]+|frontend|checkout|accounting|payment|product-catalog|"
    r"recommendation|email|shipping|currency|ad|cart|quote|flagd|kafka|"
    r"frontend-proxy)\b",
    re.I,
)


def build_request_context(
    query: str,
    *,
    entrypoint: str,
    routing_mode: str = MODE_LEGACY,
    skill_override: str = "",
    engine: str = "react",
    writes_interactive: Optional[bool] = None,
    named_workloads: Optional[List[str]] = None,
    alert_labels: Optional[Mapping[str, Any]] = None,
) -> RequestContext:
    labels = {
        str(key): str(value)
        for key, value in dict(alert_labels or {}).items()
        if value is not None
    }
    # Payload labels are routing clues, never a write grant.
    workloads = list(named_workloads or [])
    if not workloads:
        workloads = [match.group(1).lower() for match in _WORKLOAD.finditer(query or "")]
    seen = []
    for name in workloads:
        item = name.strip().lower()
        if item and item not in seen:
            seen.append(item)
    return RequestContext(
        query=query or "",
        entrypoint=entrypoint,
        intent=detect_intent(query or ""),
        writes_interactive=writes_interactive,
        named_workloads=seen,
        alert_labels=labels,
        routing_mode=routing_mode,
        skill_override=skill_override or None,
        engine=engine,
    )


def webhook_labels(payload: Mapping[str, Any]) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    common = payload.get("commonLabels") or {}
    if isinstance(common, dict):
        for key, value in common.items():
            if value:
                labels[str(key)] = str(value)
    for alert in payload.get("alerts") or []:
        if not isinstance(alert, dict):
            continue
        for key, value in (alert.get("labels") or {}).items():
            if key not in labels and value:
                labels[str(key)] = str(value)
    return labels
