"""Per-run observation ledger. Targeted tools may only inspect FOCUS objects.

list_pods / get_recent_events scan the namespace. This module extracts objects
from those texts and marks FOCUS: not Ready, a bad State, a live incident
event on a current pod, or a deleted predecessor of a current pod (different
UID). Ready pods with historical restarts stay context. get_pod_status /
get_pod_logs / validate_recovery must name a FOCUS object (from_obs or a
selector whose value equals that component).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .scope import relation_for, scope_from_ledger
from ..stamps import iso_utc


TARGET_TOOLS = frozenset({"get_pod_status", "get_pod_logs", "validate_recovery"})
SCAN_TOOLS = frozenset({"list_pods", "get_recent_events"})

FOCUS_EVENT_REASONS = frozenset({
    "killing",
    "failedscheduling",
    "backoff",
    "unhealthy",
    "failed",
    "errimagepull",
    "failedcreate",
    "evicted",
    "oomkilling",
    "failedkillpod",
    "notready",
})

_COLLECTED = re.compile(r"collected_at=(\S+)")
_HEALTHY_PHASES = frozenset({"running", "succeeded"})
_HEALTHY_STATES = frozenset({"", "running"})


@dataclass
class Observation:
    id: str
    kind: str
    name: str
    component: str = "n/a"
    ready: Optional[bool] = None
    phase: str = ""
    state: str = ""
    restarts: int = 0
    source_tool: str = ""
    focus: bool = False
    focus_reason: str = ""
    window: str = "current"
    uid: str = ""
    namespace: str = ""
    cluster: str = ""
    owner_kind: str = ""
    owner_name: str = ""
    owner_uid: str = ""
    present: bool = True
    event_time: str = ""
    first_seen: str = ""
    event_count: int = 0
    collected_at: str = ""

    @property
    def object_ref(self) -> str:
        return f"{self.kind}/{self.name}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "component": self.component,
            "focus": self.focus,
            "window": self.window or "current",
            "object_ref": self.object_ref,
            "uid": self.uid,
            "namespace": self.namespace,
            "cluster": self.cluster,
            "owner_kind": self.owner_kind,
            "owner_name": self.owner_name,
            "owner_uid": self.owner_uid,
            "present": self.present,
            "ready": self.ready,
            "state": self.state,
            "event_time": self.event_time or self.window or "current",
            "first_seen": self.first_seen,
            "event_count": self.event_count,
            "collected_at": self.collected_at,
        }


@dataclass
class ObservationLedger:
    objects: List[Observation] = field(default_factory=list)
    _next_id: int = 1
    query: str = ""

    def reset(self) -> None:
        self.objects = []
        self._next_id = 1
        self.query = ""

    def focus_objects(self) -> List[Observation]:
        return [obs for obs in self.objects if obs.focus]

    def by_id(self, obs_id: str) -> Optional[Observation]:
        key = (obs_id or "").strip()
        for obs in self.objects:
            if obs.id == key:
                return obs
        return None

    def ingest(self, tool_name: str, text: str) -> List[Observation]:
        if not text or text.lstrip().lower().startswith(("error", "unexpected error")):
            return []
        if tool_name == "list_pods":
            return self._ingest_pods(text)
        if tool_name == "get_recent_events":
            return self._ingest_events(text)
        return []

    def authorize(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Empty string allows the call. Otherwise a DENIED reason for the model."""
        if tool_name not in TARGET_TOOLS:
            return ""
        focus = self.focus_objects()
        if not focus:
            return (
                "DENIED: targeted tool locked until a FOCUS object exists. "
                "Call list_pods first. If every pod is Ready, call get_recent_events; "
                "if that is also empty, answer that the namespace looks healthy. "
                "Do not inspect Ready pods that only have historical restarts."
            )
        resolved = self._resolve_target(arguments)
        if resolved is None:
            return (
                "DENIED: get_pod_status / get_pod_logs / validate_recovery must "
                "pass from_obs=obs-N or a selector whose value equals a FOCUS "
                "component.\n"
                + self.catalog()
            )
        if not resolved.focus:
            return (
                f"DENIED: {resolved.id} ({resolved.kind}/{resolved.name}) is context, "
                "not FOCUS. Targeted tools may only inspect FOCUS objects.\n"
                + self.catalog()
            )
        return ""

    def bind_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Fill pod_selector from from_obs and drop from_obs before MCP."""
        out = dict(arguments or {})
        obs_id = str(out.pop("from_obs", "") or "").strip()
        if not obs_id:
            return out
        obs = self.by_id(obs_id)
        if obs is None:
            return out
        if not (out.get("pod_selector") or "").strip():
            selector = selector_for(obs)
            if selector:
                out["pod_selector"] = selector
        return out

    def catalog(self) -> str:
        focus = self.focus_objects()
        scope = scope_from_ledger(self.query, self)
        lines = ["[Observation ledger]"]
        if focus:
            lines.append("FOCUS (targeted tools may only inspect these):")
            for obs in focus:
                lines.append(f"- {self._line(obs, relation_for(obs, scope))}")
        else:
            lines.append("FOCUS: none")
            lines.append(
                "All listed pods are Running/Ready, or none have been scanned yet. "
                "Do not call get_pod_logs / get_pod_status / validate_recovery."
            )
        context = [obs for obs in self.objects if obs.kind == "Pod" and not obs.focus]
        if context:
            lines.append(f"CONTEXT: {len(context)} Ready pod(s) omitted from investigation.")
        return "\n".join(lines)

    def _ingest_pods(self, text: str) -> List[Observation]:
        collected_at = header_collected_at(text)
        added: List[Observation] = []
        for raw in text.splitlines():
            fields = parse_pod_fields(raw)
            if not fields:
                continue
            ready = fields.get("ready") == "True"
            state = fields.get("state") or ""
            phase = fields.get("phase") or ""
            focus, reason = _pod_focus(ready, phase, state)
            owner_kind, owner_name, owner_uid = parse_owner_field(fields.get("owner") or "")
            present = fields.get("present", "true").lower() != "false"
            obs = self._upsert_pod(
                name=fields["name"],
                component=fields.get("component") or "n/a",
                ready=ready,
                phase=phase,
                state=state,
                restarts=int(fields.get("restarts") or 0),
                source_tool="list_pods",
                focus=focus,
                focus_reason=reason,
                uid=_dash(fields.get("uid") or ""),
                namespace=_dash(fields.get("ns") or fields.get("namespace") or ""),
                cluster=_dash(fields.get("cluster") or ""),
                owner_kind=owner_kind,
                owner_name=owner_name,
                owner_uid=owner_uid,
                present=present,
                collected_at=collected_at,
            )
            added.append(obs)
        return added

    def _ingest_events(self, text: str) -> List[Observation]:
        collected_at = header_collected_at(text)
        promoted: List[Observation] = []
        for raw in text.splitlines():
            cols = raw.split("\t")
            if len(cols) < 5:
                continue
            last_time, _etype, reason, obj_ref = cols[0], cols[1], cols[2], cols[3]
            if reason.upper() == "REASON" or obj_ref.upper() == "OBJECT":
                continue
            if reason.lower() not in FOCUS_EVENT_REASONS:
                continue
            first_time = cols[5] if len(cols) > 5 else last_time
            try:
                count = int(cols[6]) if len(cols) > 6 else 1
            except ValueError:
                count = 1
            stamp = iso_utc(last_time)
            first = iso_utc(first_time)
            kind, name, uid = parse_event_object(obj_ref)
            exact, related = self._split_event_targets(kind, name, uid)
            targets = list(exact)
            if not exact and related and kind.lower() == "pod":
                deleted = self._ensure_absent_pod(
                    name=name,
                    uid=uid,
                    related=related[0],
                    reason=f"event {reason} on {obj_ref}; object not in current list_pods",
                    stamp=stamp,
                    first=first,
                    count=count,
                    collected_at=collected_at,
                )
                targets = [deleted, *related]
            elif not exact:
                targets = related
            for obs in targets:
                if not obs.focus:
                    obs.focus = True
                    obs.focus_reason = f"event {reason} on {obj_ref}"
                    promoted.append(obs)
                if stamp and (not obs.window or obs.window == "current"):
                    obs.window = stamp
                if stamp:
                    obs.event_time = stamp
                if first and not obs.first_seen:
                    obs.first_seen = first
                obs.event_count = max(obs.event_count, count)
                if collected_at and not obs.collected_at:
                    obs.collected_at = collected_at
                if uid and obs.name == name and not obs.uid:
                    obs.uid = uid
        return promoted

    def _ensure_absent_pod(
        self,
        *,
        name: str,
        uid: str,
        related: Observation,
        reason: str,
        stamp: str,
        first: str,
        count: int,
        collected_at: str,
    ) -> Observation:
        found = self._find_pod(uid=uid, name=name, namespace=related.namespace)
        if found is not None:
            found.present = False
            found.focus = True
            found.focus_reason = reason
            found.window = stamp or found.window
            found.event_time = stamp or found.event_time
            if first and not found.first_seen:
                found.first_seen = first
            found.event_count = max(found.event_count, count)
            if collected_at and not found.collected_at:
                found.collected_at = collected_at
            if uid and not found.uid:
                found.uid = uid
            if not found.component or found.component == "n/a":
                found.component = related.component
            if not found.owner_name:
                found.owner_kind = related.owner_kind
                found.owner_name = related.owner_name
                found.owner_uid = related.owner_uid
            return found
        return self._upsert_pod(
            name=name,
            component=related.component,
            ready=None,
            phase="Deleted",
            state="",
            restarts=0,
            source_tool="get_recent_events",
            focus=True,
            focus_reason=reason,
            window=stamp or "current",
            uid=uid,
            namespace=related.namespace,
            cluster=related.cluster,
            owner_kind=related.owner_kind,
            owner_name=related.owner_name,
            owner_uid=related.owner_uid,
            present=False,
            event_time=stamp,
            first_seen=first,
            event_count=count,
            collected_at=collected_at or related.collected_at,
        )

    def _upsert_pod(self, **kwargs) -> Observation:
        found = self._find_pod(
            uid=kwargs.get("uid") or "",
            name=kwargs.get("name") or "",
            namespace=kwargs.get("namespace") or "",
        )
        incoming_uid = kwargs.get("uid") or ""
        if found is not None and incoming_uid and found.uid and found.uid != incoming_uid:
            found = None
        if found is not None:
            keep_focus = found.focus
            keep_reason = found.focus_reason
            for key, value in kwargs.items():
                setattr(found, key, value)
            if keep_focus and not found.focus:
                found.focus = True
                found.focus_reason = keep_reason
            return found
        obs = Observation(id=f"obs-{self._next_id}", kind="Pod", **kwargs)
        self._next_id += 1
        self.objects.append(obs)
        return obs

    def _find_pod(self, *, uid: str = "", name: str = "", namespace: str = "") -> Optional[Observation]:
        if uid:
            for obs in self.objects:
                if obs.kind == "Pod" and obs.uid == uid:
                    return obs
        if not name:
            return None
        for obs in self.objects:
            if obs.kind != "Pod" or obs.name != name:
                continue
            if namespace and obs.namespace and obs.namespace != namespace:
                continue
            if uid and obs.uid and obs.uid != uid:
                continue
            return obs
        return None

    def _split_event_targets(
        self,
        kind: str,
        name: str,
        uid: str,
    ) -> Tuple[List[Observation], List[Observation]]:
        if not name or name == "N/A":
            return [], []
        kind_l = kind.lower()
        exact: List[Observation] = []
        related: List[Observation] = []
        for obs in self.objects:
            if obs.kind != "Pod":
                continue
            if kind_l == "pod":
                same = bool(uid and obs.uid and obs.uid == uid) or obs.name == name
                if same:
                    exact.append(obs)
                elif _replica_set_prefix(obs.name) == _replica_set_prefix(name):
                    related.append(obs)
            elif kind_l in {"deployment", "service"}:
                if obs.component == name:
                    related.append(obs)
            elif kind_l == "replicaset":
                if obs.name.startswith(name + "-") or _replica_set_prefix(obs.name) == name:
                    related.append(obs)
        return exact, related

    def _resolve_target(self, arguments: Dict[str, Any]) -> Optional[Observation]:
        args = arguments or {}
        obs_id = str(args.get("from_obs") or "").strip()
        if obs_id:
            return self.by_id(obs_id)
        selector = str(args.get("pod_selector") or "").strip()
        if not selector:
            return None
        values = _selector_values(selector)
        if not values:
            return None
        for obs in self.objects:
            if obs.kind != "Pod":
                continue
            if obs.component not in ("", "n/a") and obs.component in values:
                return obs
            if obs.name in values:
                return obs
        return None

    @staticmethod
    def _line(obs: Observation, relation: str = "") -> str:
        bits = [obs.id, f"{obs.kind}/{obs.name}", f"component={obs.component}"]
        if obs.uid:
            bits.append(f"uid={obs.uid}")
        if not obs.present:
            bits.append("present=false")
        if obs.owner_name:
            bits.append(f"owner={obs.owner_kind}/{obs.owner_name}")
        if obs.ready is not None:
            bits.append(f"ready={obs.ready}")
        if obs.state:
            bits.append(f"state={obs.state}")
        bits.append(f"window={obs.window or 'current'}")
        if obs.event_time and obs.event_time != obs.window:
            bits.append(f"event_time={obs.event_time}")
        if obs.collected_at:
            bits.append(f"collected_at={obs.collected_at}")
        if relation:
            bits.append(f"relation={relation}")
        if obs.focus_reason:
            bits.append(obs.focus_reason)
        return " ".join(bits)


def selector_for(obs: Observation) -> str:
    if obs.component and obs.component not in ("", "n/a"):
        return f"app.kubernetes.io/component={obs.component}"
    return ""


def parse_pod_fields(line: str) -> Optional[Dict[str, str]]:
    raw = line.strip()
    if not raw.startswith("- "):
        return None
    tokens = [part.strip() for part in raw[2:].split("|")]
    if not tokens:
        return None
    fields = {"name": tokens[0]}
    for token in tokens[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key.strip()] = value.strip()
    if "phase" not in fields or "ready" not in fields:
        return None
    return fields


def parse_owner_field(value: str) -> Tuple[str, str, str]:
    text = (value or "").strip()
    if not text or text == "-":
        return "", "", ""
    uid = ""
    main = text
    if " uid=" in text:
        main, uid = text.split(" uid=", 1)
        uid = uid.strip()
    kind, _, name = main.partition("/")
    return kind.strip(), name.strip(), uid


def parse_event_object(obj_ref: str) -> Tuple[str, str, str]:
    text = (obj_ref or "").strip()
    uid = ""
    main = text
    if " uid=" in text:
        main, uid = text.split(" uid=", 1)
        uid = uid.split()[0].strip()
    kind, _, name = main.partition("/")
    return kind.strip(), name.strip(), uid


def header_collected_at(text: str) -> str:
    match = _COLLECTED.search(text or "")
    if not match:
        return ""
    return iso_utc(match.group(1).rstrip(":,)"))


def _dash(value: str) -> str:
    text = (value or "").strip()
    if text in {"", "-"}:
        return ""
    return text


def _pod_focus(ready: bool, phase: str, state: str) -> Tuple[bool, str]:
    if not ready:
        return True, "not Ready"
    if phase.lower() not in _HEALTHY_PHASES:
        return True, f"phase={phase}"
    if (state or "").lower() not in _HEALTHY_STATES:
        return True, f"state={state}"
    return False, ""


def _replica_set_prefix(pod_name: str) -> str:
    if "-" not in pod_name:
        return pod_name
    return pod_name.rsplit("-", 1)[0]


def _selector_values(selector: str) -> set[str]:
    values: set[str] = set()
    for part in selector.split(","):
        if "=" not in part:
            continue
        _key, value = part.split("=", 1)
        value = value.strip()
        if value:
            values.add(value)
    return values


def annotate_target_tools(tools: List[Dict[str, Any]]) -> None:
    """Tell the model about from_obs without changing the MCP server schema."""
    for tool in tools:
        fn = tool.get("function") or {}
        if fn.get("name") not in TARGET_TOOLS:
            continue
        params = fn.setdefault("parameters", {"type": "object", "properties": {}})
        props = params.setdefault("properties", {})
        props["from_obs"] = {
            "type": "string",
            "description": (
                "Observation ID from the ledger (obs-N). Prefer this over inventing "
                "a selector. Targeted tools may only inspect FOCUS objects. A "
                "deleted pod still binds to its workload selector so the replacement "
                "can be inspected."
            ),
        }
        desc = fn.get("description") or ""
        extra = " Requires a FOCUS observation (from_obs or a matching pod_selector)."
        if extra.strip() not in desc:
            fn["description"] = desc.rstrip() + extra
