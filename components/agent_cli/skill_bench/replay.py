"""Offline tool provider. Never starts MCP, Kubernetes, or Prometheus."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..evidence_models import CALL_DENIED, CALL_ERROR, CALL_SUCCESS, ToolPayload
from ..observations import annotate_target_tools
from .clock import COLLECTED_AT
from .models import Case

WRITE_TOOLS = frozenset({"restart_deployment", "scale_deployment", "rollback_deployment"})

_CATALOG_NAMES = [
    "list_pods", "get_recent_events", "get_pod_status", "get_pod_logs",
    "validate_recovery", "query_prometheus", "restart_deployment",
    "scale_deployment", "rollback_deployment",
]


class ReplayToolProvider:
    def __init__(self, case: Case):
        self.case = case
        self.calls: List[tuple[str, dict]] = []
        self.unmatched: List[str] = []
        self.write_attempts: List[dict] = []
        self._tools_cache = {
            name: {
                "name": name,
                "description": name,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pod_selector": {"type": "string"},
                        "minutes": {"type": "integer"},
                        "lines": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
            }
            for name in _CATALOG_NAMES
        }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def get_tools(self, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        selected = names or _CATALOG_NAMES
        tools = []
        for name in selected:
            spec = self._tools_cache.get(name)
            if spec is None:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["input_schema"],
                },
            })
        annotate_target_tools(tools)
        return tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        payload = await self.call_tool_result(tool_name, arguments)
        return payload.text

    async def call_tool_result(self, tool_name: str, arguments: Dict[str, Any]) -> ToolPayload:
        args = dict(arguments or {})
        self.calls.append((tool_name, args))
        if tool_name in WRITE_TOOLS:
            self.write_attempts.append({"tool": tool_name, "arguments": args})
            return ToolPayload(
                text="DENIED: replay bench never executes write tools",
                call_status=CALL_DENIED,
                is_error=True,
            )
        fixture = self.case.tools.get(tool_name)
        if fixture is None:
            self.unmatched.append(tool_name)
            return ToolPayload(
                text=f"ERROR: unmatched replay tool {tool_name}",
                call_status=CALL_ERROR,
                is_error=True,
            )
        if fixture.error:
            return ToolPayload(text=fixture.error, call_status=CALL_SUCCESS, is_error=False)
        text = self._lookup(fixture, args)
        if text is None:
            self.unmatched.append(f"{tool_name}:{args}")
            return ToolPayload(
                text=f"ERROR: unmatched replay arguments for {tool_name}",
                call_status=CALL_ERROR,
                is_error=True,
            )
        if tool_name in {"list_pods", "get_recent_events"} and "collected_at=" not in text:
            text = text.rstrip() + f"\ncollected_at={self.case.collected_at or COLLECTED_AT}"
        return ToolPayload(text=text, call_status=CALL_SUCCESS, is_error=False)

    def _lookup(self, fixture, args: Dict[str, Any]) -> Optional[str]:
        obs = str(args.get("from_obs") or "").strip()
        if obs and obs in fixture.by_obs:
            return fixture.by_obs[obs]
        selector = str(args.get("pod_selector") or args.get("name") or "").strip()
        if selector and selector in fixture.by_selector:
            return fixture.by_selector[selector]
        if selector:
            for obj in self.case.objects:
                if selector == obj.get("selector") or selector == obj.get("name"):
                    mapped = fixture.by_selector.get(obj.get("selector") or "")
                    if mapped:
                        return mapped
                if obs and obs == obj.get("obs_id"):
                    mapped = fixture.by_selector.get(obj.get("selector") or "")
                    if mapped:
                        return mapped
        if fixture.default:
            return fixture.default
        if fixture.by_selector and len(fixture.by_selector) == 1 and not selector and not obs:
            return None
        if fixture.default == "" and not fixture.by_selector:
            return fixture.default
        if not selector and not obs and fixture.by_selector:
            return None
        if fixture.by_selector and selector:
            return None
        return fixture.default or None
