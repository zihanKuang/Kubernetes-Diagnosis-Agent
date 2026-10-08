"""Skill data contracts. Plain dataclasses, explicit validation, stable JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from .errors import SkillError

SCHEMA_VERSION = "1"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

ENTRYPOINTS = frozenset({"cli", "interactive", "webhook", "eval"})
INTENTS = frozenset({
    "health_check",
    "fault_diagnosis",
    "recovery_check",
    "unknown",
})
SCAN_NOT_SCANNED = "not_scanned"
SCAN_SUCCESS = "success"
SCAN_EMPTY = "empty"
SCAN_FAILED = "failed"
SCAN_STATUSES = frozenset({
    SCAN_NOT_SCANNED,
    SCAN_SUCCESS,
    SCAN_EMPTY,
    SCAN_FAILED,
})

STAGE_SUCCESS = "success"
STAGE_FAILED = "failed"
STAGE_SKIPPED = "skipped"
STAGE_CAPABILITY_UNAVAILABLE = "capability_unavailable"
STAGE_BUDGET_EXHAUSTED = "budget_exhausted"
STAGE_STATUSES = frozenset({
    STAGE_SUCCESS,
    STAGE_FAILED,
    STAGE_SKIPPED,
    STAGE_CAPABILITY_UNAVAILABLE,
    STAGE_BUDGET_EXHAUSTED,
})

FAILURE_POLICIES = frozenset({
    "unknown_output",
    "skip",
    "fail",
})

ROUTE_PRE_SCAN = "pre_scan"
ROUTE_POST_SCAN = "post_scan"
ROUTE_REROUTE = "reroute"
ROUTE_OVERRIDE = "override"
ROUTE_FALLBACK = "fallback"

SOURCE_BUILTIN = "builtin"
SOURCE_EXTERNAL = "external"


def _require_str(value: Any, field_name: str, *, skill_id: str = "", path: str = "") -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillError(
            f"{field_name} is required",
            skill_id=skill_id,
            path=path,
            field=field_name,
        )
    return value.strip()


def _as_str_list(value: Any, field_name: str, *, skill_id: str = "", path: str = "") -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise SkillError(
            f"{field_name} must be a list of strings",
            skill_id=skill_id,
            path=path,
            field=field_name,
        )
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SkillError(
                f"{field_name} entries must be non-empty strings",
                skill_id=skill_id,
                path=path,
                field=field_name,
            )
        out.append(item.strip())
    return out


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, data: Any, *, skill_id: str = "", path: str = "") -> "Condition":
        if not isinstance(data, dict):
            raise SkillError("condition must be a table", skill_id=skill_id, path=path)
        field_name = _require_str(data.get("field"), "field", skill_id=skill_id, path=path)
        op = _require_str(data.get("op"), "op", skill_id=skill_id, path=path)
        return cls(field=field_name, op=op, value=data.get("value"))


@dataclass
class RoutingRule:
    intents: List[str] = field(default_factory=list)
    entrypoints: List[str] = field(default_factory=list)
    observations: List[Condition] = field(default_factory=list)
    exclude: List[Condition] = field(default_factory=list)
    priority: int = 0
    fallback: bool = False
    specificity: int = 0
    match: str = "all"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "intents": list(self.intents),
            "entrypoints": list(self.entrypoints),
            "observations": [item.as_dict() for item in self.observations],
            "exclude": [item.as_dict() for item in self.exclude],
            "priority": self.priority,
            "fallback": self.fallback,
            "specificity": self.specificity,
            "match": self.match,
        }

    @classmethod
    def from_dict(cls, data: Any, *, skill_id: str = "", path: str = "") -> "RoutingRule":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise SkillError(
                "routing must be a table",
                skill_id=skill_id,
                path=path,
                field="routing",
            )
        intents = _as_str_list(data.get("intents"), "routing.intents", skill_id=skill_id, path=path)
        for intent in intents:
            if intent not in INTENTS:
                raise SkillError(
                    f"unsupported intent {intent!r}",
                    skill_id=skill_id,
                    path=path,
                    field="routing.intents",
                )
        entrypoints = _as_str_list(
            data.get("entrypoints"),
            "routing.entrypoints",
            skill_id=skill_id,
            path=path,
        )
        for entry in entrypoints:
            if entry not in ENTRYPOINTS:
                raise SkillError(
                    f"unsupported entrypoint {entry!r}",
                    skill_id=skill_id,
                    path=path,
                    field="routing.entrypoints",
                )
        observations = [
            Condition.from_dict(item, skill_id=skill_id, path=path)
            for item in (data.get("observations") or [])
        ]
        exclude = [
            Condition.from_dict(item, skill_id=skill_id, path=path)
            for item in (data.get("exclude") or [])
        ]
        priority = int(data.get("priority") or 0)
        specificity = int(data.get("specificity") or 0)
        fallback = bool(data.get("fallback") or False)
        match = str(data.get("match") or "all").strip().lower()
        if match not in {"all", "any"}:
            raise SkillError(
                "routing.match must be all or any",
                skill_id=skill_id,
                path=path,
                field="routing.match",
            )
        return cls(
            intents=intents,
            entrypoints=entrypoints,
            observations=observations,
            exclude=exclude,
            priority=priority,
            fallback=fallback,
            specificity=specificity,
            match=match,
        )


@dataclass
class SkillMetadata:
    schema_version: str
    id: str
    version: str
    description: str
    workflow_id: str
    instructions_file: str
    tags: List[str] = field(default_factory=list)
    entrypoints: List[str] = field(default_factory=list)
    enabled: bool = True
    routing: RoutingRule = field(default_factory=RoutingRule)
    required_capabilities: List[str] = field(default_factory=list)
    source: str = SOURCE_BUILTIN
    root: str = ""
    workflow_file: str = "workflow.toml"

    def key(self) -> tuple[str, str]:
        return (self.id, self.version)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "tags": list(self.tags),
            "entrypoints": list(self.entrypoints),
            "workflow_id": self.workflow_id,
            "instructions_file": self.instructions_file,
            "enabled": self.enabled,
            "routing": self.routing.as_dict(),
            "required_capabilities": list(self.required_capabilities),
            "source": self.source,
            "root": self.root,
            "workflow_file": self.workflow_file,
        }

    @classmethod
    def from_dict(cls, data: Any, *, path: str = "", source: str = SOURCE_BUILTIN, root: str = "") -> "SkillMetadata":
        if not isinstance(data, dict):
            raise SkillError("manifest must be a table", path=path)
        schema = str(data.get("schema_version") or "").strip()
        if schema not in SUPPORTED_SCHEMA_VERSIONS:
            raise SkillError(
                f"unsupported schema_version {schema!r}; supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
                path=path,
                field="schema_version",
            )
        skill_id = _require_str(data.get("id"), "id", path=path)
        version = _require_str(data.get("version"), "version", path=path)
        description = _require_str(data.get("description"), "description", skill_id=skill_id, path=path)
        workflow_id = _require_str(data.get("workflow_id"), "workflow_id", skill_id=skill_id, path=path)
        instructions = str(data.get("instructions") or data.get("instructions_file") or "SKILL.md").strip()
        if not instructions:
            raise SkillError(
                "instructions file is required",
                skill_id=skill_id,
                path=path,
                field="instructions",
            )
        entrypoints = _as_str_list(data.get("entrypoints"), "entrypoints", skill_id=skill_id, path=path)
        if not entrypoints:
            raise SkillError(
                "entrypoints is required",
                skill_id=skill_id,
                path=path,
                field="entrypoints",
            )
        for entry in entrypoints:
            if entry not in ENTRYPOINTS:
                raise SkillError(
                    f"unsupported entrypoint {entry!r}",
                    skill_id=skill_id,
                    path=path,
                    field="entrypoints",
                )
        enabled = data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise SkillError(
                "enabled must be a boolean",
                skill_id=skill_id,
                path=path,
                field="enabled",
            )
        routing = RoutingRule.from_dict(data.get("routing"), skill_id=skill_id, path=path)
        if not routing.entrypoints:
            routing.entrypoints = list(entrypoints)
        return cls(
            schema_version=schema,
            id=skill_id,
            version=version,
            description=description,
            workflow_id=workflow_id,
            instructions_file=instructions,
            tags=_as_str_list(data.get("tags"), "tags", skill_id=skill_id, path=path),
            entrypoints=entrypoints,
            enabled=enabled,
            routing=routing,
            required_capabilities=_as_str_list(
                data.get("required_capabilities"),
                "required_capabilities",
                skill_id=skill_id,
                path=path,
            ),
            source=source,
            root=root,
            workflow_file=str(data.get("workflow_file") or "workflow.toml").strip(),
        )


@dataclass
class RequestContext:
    query: str
    entrypoint: str
    intent: str = "unknown"
    writes_interactive: Optional[bool] = None
    namespace: str = "citrus"
    named_workloads: List[str] = field(default_factory=list)
    alert_labels: Dict[str, str] = field(default_factory=dict)
    routing_mode: str = "legacy"
    skill_override: Optional[str] = None
    engine: str = "react"

    def __post_init__(self) -> None:
        self.named_workloads = list(self.named_workloads)
        self.alert_labels = dict(self.alert_labels)
        if self.entrypoint not in ENTRYPOINTS:
            raise SkillError(
                f"unsupported entrypoint {self.entrypoint!r}",
                field="entrypoint",
            )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "entrypoint": self.entrypoint,
            "intent": self.intent,
            "writes_interactive": self.writes_interactive,
            "namespace": self.namespace,
            "named_workloads": list(self.named_workloads),
            "alert_labels": dict(self.alert_labels),
            "routing_mode": self.routing_mode,
            "skill_override": self.skill_override,
            "engine": self.engine,
        }


@dataclass
class ObjectFacts:
    obs_id: str
    name: str
    component: str = ""
    phase: str = ""
    state: str = ""
    ready: Optional[bool] = None
    restarts: int = 0
    present: bool = True
    waiting_reason: str = ""
    last_termination: str = ""
    event_reasons: List[str] = field(default_factory=list)
    uid: str = ""
    window: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "obs_id": self.obs_id,
            "name": self.name,
            "component": self.component,
            "phase": self.phase,
            "state": self.state,
            "ready": self.ready,
            "restarts": self.restarts,
            "present": self.present,
            "waiting_reason": self.waiting_reason,
            "last_termination": self.last_termination,
            "event_reasons": list(self.event_reasons),
            "uid": self.uid,
            "window": self.window,
        }

    def routing_facts(self, scan_status: str, focus_count: int) -> Dict[str, Any]:
        return {
            "scan_status": scan_status,
            "focus_count": focus_count,
            "has_focus": focus_count > 0,
            "phase": self.phase,
            "state": self.state,
            "waiting_reason": self.waiting_reason or self.state,
            "last_termination": self.last_termination,
            "event_reason": self.event_reasons[0] if self.event_reasons else "",
            "event_reasons": list(self.event_reasons),
            "ready": self.ready,
            "restarts": self.restarts,
            "present": self.present,
        }


@dataclass
class ObservationSnapshot:
    scan_status: str = SCAN_NOT_SCANNED
    collected_at: str = ""
    focus: List[ObjectFacts] = field(default_factory=list)
    context_count: int = 0
    observation_ids: List[str] = field(default_factory=list)
    list_pods_ok: bool = False
    events_ok: bool = False

    def __post_init__(self) -> None:
        if self.scan_status not in SCAN_STATUSES:
            raise SkillError(
                f"unsupported scan_status {self.scan_status!r}",
                field="scan_status",
            )
        self.focus = list(self.focus)
        self.observation_ids = list(self.observation_ids)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scan_status": self.scan_status,
            "collected_at": self.collected_at,
            "focus": [item.as_dict() for item in self.focus],
            "context_count": self.context_count,
            "observation_ids": list(self.observation_ids),
            "list_pods_ok": self.list_pods_ok,
            "events_ok": self.events_ok,
        }

    def valid_scan(self) -> bool:
        return self.scan_status in {SCAN_SUCCESS, SCAN_EMPTY}


@dataclass
class RouteDecision:
    skill_id: str
    skill_version: str
    workflow_id: str
    stage: str
    candidates: List[str] = field(default_factory=list)
    matched_rules: List[str] = field(default_factory=list)
    fallback_reason: str = ""
    observation_ids: List[str] = field(default_factory=list)
    object_bindings: Dict[str, str] = field(default_factory=dict)
    override: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "workflow_id": self.workflow_id,
            "stage": self.stage,
            "candidates": list(self.candidates),
            "matched_rules": list(self.matched_rules),
            "fallback_reason": self.fallback_reason,
            "observation_ids": list(self.observation_ids),
            "object_bindings": dict(self.object_bindings),
            "override": self.override,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)


@dataclass
class StageSpec:
    id: str
    processor: str
    tools: List[str] = field(default_factory=list)
    optional_tools: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    enter_when: List[Condition] = field(default_factory=list)
    complete_when: List[Condition] = field(default_factory=list)
    complete_tools: List[str] = field(default_factory=list)
    on_failure: str = "unknown_output"
    optional: bool = False
    source: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "processor": self.processor,
            "tools": list(self.tools),
            "optional_tools": list(self.optional_tools),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "enter_when": [item.as_dict() for item in self.enter_when],
            "complete_when": [item.as_dict() for item in self.complete_when],
            "complete_tools": list(self.complete_tools),
            "on_failure": self.on_failure,
            "optional": self.optional,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Any, *, skill_id: str = "", path: str = "") -> "StageSpec":
        if not isinstance(data, dict):
            raise SkillError("stage must be a table", skill_id=skill_id, path=path)
        stage_id = _require_str(data.get("id"), "id", skill_id=skill_id, path=path)
        processor = _require_str(data.get("processor"), "processor", skill_id=skill_id, path=path)
        on_failure = str(data.get("on_failure") or "unknown_output").strip()
        if on_failure not in FAILURE_POLICIES:
            raise SkillError(
                f"unsupported on_failure {on_failure!r}",
                skill_id=skill_id,
                path=path,
                field="on_failure",
            )
        return cls(
            id=stage_id,
            processor=processor,
            tools=_as_str_list(data.get("tools"), "tools", skill_id=skill_id, path=path),
            optional_tools=_as_str_list(
                data.get("optional_tools"), "optional_tools", skill_id=skill_id, path=path
            ),
            inputs=_as_str_list(data.get("inputs"), "inputs", skill_id=skill_id, path=path),
            outputs=_as_str_list(data.get("outputs"), "outputs", skill_id=skill_id, path=path),
            enter_when=[
                Condition.from_dict(item, skill_id=skill_id, path=path)
                for item in (data.get("enter_when") or [])
            ],
            complete_when=[
                Condition.from_dict(item, skill_id=skill_id, path=path)
                for item in (data.get("complete_when") or [])
            ],
            complete_tools=_as_str_list(
                data.get("complete_tools"), "complete_tools", skill_id=skill_id, path=path
            ),
            on_failure=on_failure,
            optional=bool(data.get("optional") or False),
            source=str(data.get("source") or ""),
        )


@dataclass
class StageReplacement:
    stage_id: str
    stage: StageSpec
    with_fragment: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "stage": self.stage.as_dict(),
            "with_fragment": self.with_fragment,
        }


@dataclass
class WorkflowSpec:
    id: str
    version: str
    stages: List[StageSpec] = field(default_factory=list)
    compose: List[str] = field(default_factory=list)
    replacements: List[StageReplacement] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "stages": [item.as_dict() for item in self.stages],
            "compose": list(self.compose),
            "replacements": [item.as_dict() for item in self.replacements],
        }

    @classmethod
    def from_dict(cls, data: Any, *, skill_id: str = "", path: str = "") -> "WorkflowSpec":
        if not isinstance(data, dict):
            raise SkillError("workflow must be a table", skill_id=skill_id, path=path)
        workflow_id = _require_str(data.get("id"), "id", skill_id=skill_id, path=path)
        version = _require_str(data.get("version"), "version", skill_id=skill_id, path=path)
        stages = [
            StageSpec.from_dict(item, skill_id=skill_id, path=path)
            for item in (data.get("stages") or [])
        ]
        compose = _as_str_list(data.get("compose"), "compose", skill_id=skill_id, path=path)
        replacements: List[StageReplacement] = []
        for raw in data.get("replace") or data.get("replacements") or []:
            if not isinstance(raw, dict):
                raise SkillError("replace entry must be a table", skill_id=skill_id, path=path)
            stage_id = _require_str(raw.get("stage_id"), "stage_id", skill_id=skill_id, path=path)
            fragment = str(raw.get("with_fragment") or raw.get("with") or "").strip()
            if isinstance(raw.get("stage"), dict):
                stage = StageSpec.from_dict(raw["stage"], skill_id=skill_id, path=path)
            elif fragment:
                stage = StageSpec(id=stage_id, processor="", source=fragment)
            else:
                raise SkillError(
                    "replace requires stage or with_fragment",
                    skill_id=skill_id,
                    path=path,
                    field="replace",
                )
            if not stage.id:
                stage.id = stage_id
            replacements.append(StageReplacement(stage_id=stage_id, stage=stage, with_fragment=fragment))
        ids = [item.id for item in stages]
        if len(ids) != len(set(ids)):
            raise SkillError(
                "duplicate stage id in workflow",
                skill_id=skill_id,
                path=path,
                field="stages",
            )
        return cls(
            id=workflow_id,
            version=version,
            stages=stages,
            compose=compose,
            replacements=replacements,
        )


@dataclass
class StageResult:
    stage_id: str
    status: str
    reason: str = ""
    processor: str = ""

    def __post_init__(self) -> None:
        if self.status not in STAGE_STATUSES:
            raise SkillError(f"unsupported stage status {self.status!r}", field="status")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "status": self.status,
            "reason": self.reason,
            "processor": self.processor,
        }


@dataclass
class Budget:
    max_steps: int = 10
    max_tool_calls: int = 40
    max_stage_transitions: int = 16
    steps_used: int = 0
    tool_calls_used: int = 0
    transitions_used: int = 0

    def remaining(self) -> Dict[str, int]:
        return {
            "steps": max(0, self.max_steps - self.steps_used),
            "tool_calls": max(0, self.max_tool_calls - self.tool_calls_used),
            "stage_transitions": max(0, self.max_stage_transitions - self.transitions_used),
        }

    def exhausted(self) -> bool:
        left = self.remaining()
        return left["steps"] <= 0 or left["tool_calls"] <= 0 or left["stage_transitions"] <= 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_stage_transitions": self.max_stage_transitions,
            "steps_used": self.steps_used,
            "tool_calls_used": self.tool_calls_used,
            "transitions_used": self.transitions_used,
            "remaining": self.remaining(),
        }


@dataclass
class ToolOutcome:
    tool_name: str
    status: str
    evidence_id: str = ""
    text: str = ""
    obs_id: str = ""
    object_name: str = ""
    stage_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "evidence_id": self.evidence_id,
            "text": self.text,
            "obs_id": self.obs_id,
            "object_name": self.object_name,
            "stage_id": self.stage_id,
        }


@dataclass
class SkillRunState:
    request: RequestContext
    decision: Optional[RouteDecision] = None
    plan_stage_ids: List[str] = field(default_factory=list)
    current_stage_id: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    provided_tools: List[str] = field(default_factory=list)
    batch_allowed: List[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    history: List[RouteDecision] = field(default_factory=list)
    stage_results: List[StageResult] = field(default_factory=list)
    outcomes: List[ToolOutcome] = field(default_factory=list)
    object_facts: Dict[str, ObjectFacts] = field(default_factory=dict)
    object_switches: Dict[str, int] = field(default_factory=dict)
    last_route_key: str = ""
    fingerprints: Dict[str, str] = field(default_factory=dict)
    loaded_instructions: List[str] = field(default_factory=list)
    instruction_bytes: int = 0
    schema_bytes: List[int] = field(default_factory=list)
    turn_tool_sets: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    unknown_reason: str = ""
    active: bool = False
    inspect_status_seen: bool = False
    inspect_ready_to_leave: bool = False
    unbound_status: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.plan_stage_ids = list(self.plan_stage_ids)
        self.allowed_tools = list(self.allowed_tools)
        self.provided_tools = list(self.provided_tools)
        self.batch_allowed = list(self.batch_allowed)
        self.history = list(self.history)
        self.stage_results = list(self.stage_results)
        self.outcomes = list(self.outcomes)
        self.object_facts = dict(self.object_facts)
        self.object_switches = dict(self.object_switches)
        self.fingerprints = dict(self.fingerprints)
        self.loaded_instructions = list(self.loaded_instructions)
        self.schema_bytes = list(self.schema_bytes)
        self.turn_tool_sets = list(self.turn_tool_sets)
        self.events = list(self.events)
        self.unbound_status = list(self.unbound_status)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request.as_dict(),
            "decision": self.decision.as_dict() if self.decision else None,
            "plan_stage_ids": list(self.plan_stage_ids),
            "current_stage_id": self.current_stage_id,
            "allowed_tools": list(self.allowed_tools),
            "provided_tools": list(self.provided_tools),
            "budget": self.budget.as_dict(),
            "history": [item.as_dict() for item in self.history],
            "stage_results": [item.as_dict() for item in self.stage_results],
            "fingerprints": dict(self.fingerprints),
            "loaded_instructions": list(self.loaded_instructions),
            "instruction_bytes": self.instruction_bytes,
            "schema_bytes": list(self.schema_bytes),
            "turn_tool_sets": list(self.turn_tool_sets),
            "events": list(self.events),
            "unknown_reason": self.unknown_reason,
            "object_bindings": dict(self.decision.object_bindings) if self.decision else {},
        }


def round_trip(obj: Any) -> Any:
    """Serialize then rebuild dataclasses that expose as_dict/from_dict."""
    payload = obj.as_dict()
    blob = json.dumps(payload, sort_keys=True)
    return json.loads(blob)


def field_names(cls: type) -> List[str]:
    return [item.name for item in fields(cls)]


def dump(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    return asdict(obj)
