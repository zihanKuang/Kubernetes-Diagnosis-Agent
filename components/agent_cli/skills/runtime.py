"""Per-run skill session used by both ReAct and LangGraph engines."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence

from .context import build_request_context
from .errors import SkillError
from .fingerprint import fingerprint_json, fingerprint_text
from .loader import ResourceLoader
from .models import (
    Budget,
    RouteDecision,
    SkillRunState,
    StageResult,
    STAGE_BUDGET_EXHAUSTED,
    STAGE_CAPABILITY_UNAVAILABLE,
    STAGE_FAILED,
    STAGE_SKIPPED,
    STAGE_SUCCESS,
    ToolOutcome,
)
from .processors import PROCESSOR_INSPECT, PROCESSOR_SCAN, PROCESSOR_SYNTHESIZE, PROCESSOR_VERIFY
from .prompts import GLOBAL_RUNTIME_CONSTRAINTS, assemble_instruction, legacy_instruction
from .registry import SkillRegistry
from .router import GENERIC_ID, maybe_reroute, route_post_scan, route_pre_scan
from .settings import MODE_AUTO, SkillSettings, resolve_skill_settings
from .snapshots import apply_status, enrich_from_status, snapshot_from_ledger
from .tool_policy import allowed_tools, deny_unknown_or_hidden, scan_tools_for_intent
from .workflows import CompiledPlan, compile_workflow


class SkillRuntime:
    def __init__(self, config: Any, settings: Optional[SkillSettings] = None):
        self.config = config
        self.settings = settings or _settings_from_config(config)
        self.loader = ResourceLoader(
            instruction_dir=getattr(self.settings, "instruction_dir", "") or "",
        )
        self.registry: Optional[SkillRegistry] = None
        self.state: Optional[SkillRunState] = None
        self.plan: Optional[CompiledPlan] = None
        self._skill_text = ""
        self._frozen_instruction = ""
        self._cancelled = False
        self._catalog_names: List[str] = []

    def auto(self) -> bool:
        return self.settings.routing == MODE_AUTO

    def cancel(self) -> None:
        self._cancelled = True

    def cancelled(self) -> bool:
        return self._cancelled

    def ensure_registry(self) -> SkillRegistry:
        if self.registry is None:
            self.registry = SkillRegistry(
                extra_dirs=self.settings.extra_dirs,
                disabled=self.settings.disabled,
                loader=self.loader,
            )
        return self.registry

    def begin(
        self,
        query: str,
        *,
        entrypoint: str,
        engine: str = "react",
        named_workloads: Optional[List[str]] = None,
        alert_labels: Optional[Dict[str, str]] = None,
        catalog_names: Sequence[str] = (),
    ) -> SkillRunState:
        self._cancelled = False
        self._skill_text = ""
        self._frozen_instruction = ""
        self.plan = None
        request = build_request_context(
            query,
            entrypoint=entrypoint,
            routing_mode=self.settings.routing,
            skill_override=self.settings.skill_id,
            engine=engine,
            writes_interactive=getattr(self.config, "writes_interactive", None),
            named_workloads=named_workloads,
            alert_labels=alert_labels,
        )
        max_steps = int(getattr(self.config, "max_steps", 10) or 10)
        configured_tools = getattr(self.config, "max_tool_calls", None)
        max_tools = int(configured_tools) if configured_tools else max_steps * 4
        budget = Budget(
            max_steps=max_steps,
            max_tool_calls=max_tools,
            max_stage_transitions=16,
        )
        self.state = SkillRunState(request=request, budget=budget, active=self.auto())
        self._catalog_names = list(catalog_names)
        if not self.auto():
            return self.state
        registry = self.ensure_registry()
        decision = route_pre_scan(request, registry, catalog_names=self._catalog_names)
        self._apply_decision(decision, catalog_names=self._catalog_names, load_text=True)
        return self.state

    def system_instruction(self) -> str:
        if not self.auto():
            return legacy_instruction(getattr(self.config, "system_instruction", "") or "")
        extra = ""
        current = getattr(self.config, "system_instruction", "") or ""
        if current.strip() and current.strip() != GLOBAL_RUNTIME_CONSTRAINTS.strip():
            from ..prompts import DEFAULT_SRE_SYSTEM_INSTRUCTION, LEGACY_SRE_SYSTEM_INSTRUCTION
            if current.strip() not in {
                DEFAULT_SRE_SYSTEM_INSTRUCTION.strip(),
                LEGACY_SRE_SYSTEM_INSTRUCTION.strip(),
            }:
                extra = current
        stage_id = self.state.current_stage_id if self.state else ""
        assembled = assemble_instruction(
            skill_text=self._skill_text,
            stage_id=stage_id,
            extra=extra,
        )
        self._frozen_instruction = assembled
        return assembled

    def tools_for_model(self, catalog: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        copies = [copy.deepcopy(item) for item in catalog]
        if not self.auto() or self.state is None:
            return copies
        if (
            self.state.inspect_status_seen
            and self.state.current_stage_id == "inspect"
        ):
            self.state.inspect_ready_to_leave = True
        allowed = set(self.state.allowed_tools)
        selected = []
        for item in copies:
            name = ((item.get("function") or {}).get("name") or item.get("name") or "")
            if name in allowed:
                selected.append(item)
        names = [((item.get("function") or {}).get("name") or "") for item in selected]
        self.state.provided_tools = names
        blob = fingerprint_json(names)
        size = sum(len(str(item)) for item in selected)
        self.state.schema_bytes.append(size)
        self.state.turn_tool_sets.append({
            "stage_id": self.state.current_stage_id,
            "tools": list(names),
            "schema_bytes": size,
            "fingerprint": blob,
        })
        return selected

    def begin_batch(self) -> frozenset[str]:
        if self.state is None:
            return frozenset()
        if not self.auto():
            allowed = frozenset(self.state.allowed_tools)
            self.state.batch_allowed = list(allowed)
            return allowed
        allowed = frozenset(self.state.allowed_tools)
        self.state.batch_allowed = list(allowed)
        return allowed

    def authorize(self, tool_name: str) -> str:
        if not self.auto() or self.state is None:
            return ""
        return deny_unknown_or_hidden(tool_name, self.state.batch_allowed)

    def allow_final_answer(self) -> bool:
        if not self.auto() or self.state is None:
            return True
        if self.state.unknown_reason or self.state.budget.exhausted():
            return True
        stage = self._current_stage()
        if stage is None:
            return True
        return stage.spec.processor == PROCESSOR_SYNTHESIZE

    def force_tools(self, have_any_calls: bool) -> bool:
        if not self.auto() or self.state is None:
            return False
        if not self.state.allowed_tools:
            return False
        stage = self._current_stage()
        if stage is None or stage.spec.processor == PROCESSOR_SYNTHESIZE:
            return False
        if stage.spec.processor == PROCESSOR_INSPECT:
            return not self._inspect_status_complete()
        needed = stage.spec.complete_tools or stage.spec.tools
        succeeded = {
            row.tool_name for row in self.state.outcomes
            if row.status == "success" and (not row.stage_id or row.stage_id == stage.spec.id)
        }
        return not set(needed).issubset(succeeded)

    def note_step(self) -> None:
        if self.state is None:
            return
        self.state.budget.steps_used += 1

    def note_tool_attempt(self) -> None:
        self.consume_tool_budget()

    def consume_tool_budget(self) -> str:
        if self.state is None:
            return ""
        if self.state.budget.remaining()["tool_calls"] <= 0:
            return "DENIED: tool call budget exhausted"
        self.state.budget.tool_calls_used += 1
        return ""

    def record_outcome(
        self,
        tool_name: str,
        status: str,
        *,
        evidence_id: str = "",
        text: str = "",
        obs_id: str = "",
        object_name: str = "",
        stage_id: str = "",
    ) -> None:
        if self.state is None:
            return
        self.state.outcomes.append(ToolOutcome(
            tool_name=tool_name,
            status=status,
            evidence_id=evidence_id,
            text=text[:2000],
            obs_id=obs_id,
            object_name=object_name,
            stage_id=stage_id or self.state.current_stage_id,
        ))

    def after_batch(self, ledger: Any, catalog_names: Sequence[str] = ()) -> None:
        if not self.auto() or self.state is None or self.plan is None:
            return
        if catalog_names:
            self._catalog_names = list(catalog_names)
        names = self._catalog_names
        snapshot = snapshot_from_ledger(ledger, self.state.outcomes)
        self._enrich_status(snapshot)
        self._complete_or_skip(snapshot)
        if self.state.budget.exhausted():
            self._emit("budget_exhausted", "shared budget exhausted")
            self.state.unknown_reason = self.state.unknown_reason or "budget exhausted"
            self._jump_to_synthesize("budget exhausted")
            return
        scan_row = next(
            (row for row in self.state.stage_results if row.stage_id == "scan"),
            None,
        )
        if scan_row is None:
            return
        if scan_row.status != STAGE_SUCCESS:
            return
        nxt = route_post_scan(
            self.state.request,
            snapshot,
            self.ensure_registry(),
            catalog_names=names,
            previous=self.state.decision,
        )
        previous = self.state.decision
        switched = previous is not None and nxt.skill_id != previous.skill_id
        specific_switch = switched and previous.skill_id not in {GENERIC_ID, ""}
        if specific_switch:
            nxt = maybe_reroute(
                self.state.request,
                snapshot,
                self.ensure_registry(),
                previous=previous,
                switches=self.state.object_switches,
                last_key=self.state.last_route_key,
                catalog_names=names,
            )
        self.state.last_route_key = _snapshot_key(snapshot)
        if previous is None or nxt.skill_id != previous.skill_id:
            self._apply_decision(nxt, catalog_names=names, load_text=True, keep_scan=True)
        else:
            self.state.decision = nxt

    def trace_payload(self) -> Dict[str, Any]:
        if self.state is None:
            return {}
        decision = self.state.decision
        return {
            "routing_mode": self.settings.routing,
            "skill_id": decision.skill_id if decision else "",
            "skill_version": decision.skill_version if decision else "",
            "workflow_id": decision.workflow_id if decision else "",
            "workflow_fingerprint": self.state.fingerprints.get("workflow", ""),
            "route_history": [item.as_dict() for item in self.state.history],
            "stage_results": [item.as_dict() for item in self.state.stage_results],
            "turn_tool_sets": list(self.state.turn_tool_sets),
            "loaded_instructions": list(self.state.loaded_instructions),
            "instruction_bytes": self.state.instruction_bytes,
            "schema_bytes": list(self.state.schema_bytes),
            "events": list(self.state.events),
            "unknown_reason": self.state.unknown_reason,
            "object_bindings": dict(decision.object_bindings) if decision else {},
            "outcomes": [row.as_dict() for row in self.state.outcomes],
        }

    def _apply_decision(
        self,
        decision: RouteDecision,
        *,
        catalog_names: Sequence[str],
        load_text: bool,
        keep_scan: bool = False,
    ) -> None:
        registry = self.ensure_registry()
        skill = registry.get(decision.skill_id)
        done_scan = keep_scan and any(
            row.stage_id == "scan" and row.status == STAGE_SUCCESS
            for row in (self.state.stage_results if self.state else [])
        )
        if keep_scan and self.state:
            self.state.stage_results = [
                row for row in self.state.stage_results if row.stage_id == "scan"
            ]
        plan = compile_workflow(skill.workflow, registry, skill_id=skill.meta.id)
        self._tune_scan_tools(plan, self.state.request.intent if self.state else "unknown")
        self.plan = plan
        if self.state:
            self.state.decision = decision
            self.state.history.append(decision)
            self.state.plan_stage_ids = plan.stage_ids()
            self.state.fingerprints["manifest"] = skill.fingerprint()
            self.state.fingerprints["workflow"] = fingerprint_json(plan.as_dict())
            self.state.budget.transitions_used += 1
        if load_text:
            text = self.loader.load_instructions(skill.meta)
            self._skill_text = text
            if self.state:
                if skill.meta.id not in self.state.loaded_instructions:
                    self.state.loaded_instructions.append(skill.meta.id)
                self.state.instruction_bytes += len(text.encode("utf-8"))
                self.state.fingerprints["instructions"] = fingerprint_text(text)
        start = "scan"
        if done_scan:
            start = self._next_after(plan, "scan")
        self._enter_stage(start, catalog_names=catalog_names)
        self._merge_bound_skills(catalog_names=catalog_names)
        self._emit("route", f"{decision.stage}:{decision.skill_id}")

    def _tune_scan_tools(self, plan: CompiledPlan, intent: str) -> None:
        wanted = scan_tools_for_intent(intent)
        for item in plan.stages:
            if item.spec.processor != PROCESSOR_SCAN:
                continue
            item.spec.tools = list(wanted)
            if intent == "health_check":
                item.spec.optional_tools = ["get_recent_events"]
                item.spec.complete_tools = ["list_pods"]
            else:
                item.spec.optional_tools = []
                item.spec.complete_tools = list(wanted)

    def _enter_stage(self, stage_id: str, *, catalog_names: Sequence[str]) -> None:
        if self.state is None or self.plan is None:
            return
        if self.state.budget.exhausted():
            self.state.unknown_reason = "budget exhausted"
            self._finish_stage(stage_id, STAGE_BUDGET_EXHAUSTED, "budget exhausted")
            return
        compiled = self._stage_by_id(stage_id)
        if compiled is None:
            self.state.current_stage_id = ""
            self.state.allowed_tools = []
            return
        self.state.current_stage_id = stage_id
        requested = list(compiled.spec.tools) + list(compiled.spec.optional_tools)
        granted = allowed_tools(
            catalog_names=catalog_names or requested,
            requested=requested,
            policy_capabilities=_policy_caps(self.settings.writes),
            allow_writes=self.settings.writes,
        )
        missing_required = [name for name in compiled.spec.tools if name not in granted]
        if missing_required:
            if compiled.spec.optional or compiled.spec.on_failure == "skip":
                self._finish_stage(
                    stage_id,
                    STAGE_CAPABILITY_UNAVAILABLE,
                    f"optional capability missing: {missing_required}",
                )
                nxt = self._next_after(self.plan, stage_id)
                if nxt:
                    self._enter_stage(nxt, catalog_names=catalog_names)
                return
            self.state.unknown_reason = f"missing tools {missing_required}"
            self._finish_stage(
                stage_id,
                STAGE_CAPABILITY_UNAVAILABLE,
                self.state.unknown_reason,
            )
            self._jump_to_synthesize(self.state.unknown_reason, catalog_names=catalog_names)
            return
        self.state.allowed_tools = granted
        self._emit("enter", stage_id)

    def _complete_or_skip(self, snapshot) -> None:
        if self.state is None or self.plan is None:
            return
        compiled = self._current_stage()
        if compiled is None:
            return
        if any(row.stage_id == compiled.spec.id for row in self.state.stage_results):
            return
        facts = _facts(snapshot, self.state)
        if compiled.spec.enter_when and not _eval(compiled.spec.enter_when, facts):
            self._finish_stage(compiled.spec.id, STAGE_SKIPPED, "enter_when not met")
            nxt = self._next_after(self.plan, compiled.spec.id)
            if nxt:
                self._enter_stage(nxt, catalog_names=self._catalog_names)
                self._complete_or_skip(snapshot)
            return
        if compiled.spec.processor == PROCESSOR_INSPECT:
            if self._apply_inspect_progress(snapshot, compiled):
                nxt = self._next_after(self.plan, compiled.spec.id)
                if nxt:
                    self._enter_stage(nxt, catalog_names=self._catalog_names)
                    self._complete_or_skip(snapshot)
            return
        succeeded = {
            row.tool_name for row in self.state.outcomes
            if row.status == "success" and (not row.stage_id or row.stage_id == compiled.spec.id)
        }
        needed = compiled.spec.complete_tools
        if needed and not set(needed).issubset(succeeded):
            failed = any(
                row.tool_name in needed and row.status in {"error", "timeout"}
                for row in self.state.outcomes
            )
            if failed and not compiled.spec.optional:
                self._fail_stage(compiled, "required tool failed")
            return
        if compiled.spec.complete_when and not _eval(compiled.spec.complete_when, facts):
            return
        if needed or compiled.spec.processor == PROCESSOR_SYNTHESIZE:
            self._finish_stage(compiled.spec.id, STAGE_SUCCESS, "complete")
            nxt = self._next_after(self.plan, compiled.spec.id)
            if nxt:
                self._enter_stage(nxt, catalog_names=self._catalog_names)
                self._complete_or_skip(snapshot)

    def _fail_stage(self, compiled, reason: str) -> None:
        policy = compiled.spec.on_failure or "unknown_output"
        self._finish_stage(compiled.spec.id, STAGE_FAILED, reason)
        if policy == "skip":
            nxt = self._next_after(self.plan, compiled.spec.id) if self.plan else ""
            if nxt:
                self._enter_stage(nxt, catalog_names=self._catalog_names)
            return
        self.state.unknown_reason = self.state.unknown_reason or reason
        self._jump_to_synthesize(reason)

    def _inspect_status_complete(self, snapshot=None) -> bool:
        if self.state is None:
            return False
        focus_ids = []
        if snapshot is not None:
            focus_ids = [item.obs_id for item in snapshot.focus if item.obs_id]
        if not focus_ids:
            focus_ids = self._focus_ids()
        if not focus_ids:
            return False
        done = {
            row.obs_id for row in self.state.outcomes
            if row.tool_name == "get_pod_status" and row.status == "success" and row.obs_id
        }
        return set(focus_ids).issubset(done)

    def _focus_ids(self) -> List[str]:
        if self.state is None:
            return []
        if self.state.decision and self.state.decision.object_bindings:
            return [oid for oid in self.state.decision.object_bindings if oid]
        return [oid for oid in self.state.object_facts if oid]

    def _apply_inspect_progress(self, snapshot, compiled) -> bool:
        focus_ids = [item.obs_id for item in snapshot.focus if item.obs_id]
        failed = any(
            row.tool_name == "get_pod_status"
            and row.status in {"error", "timeout"}
            and (not row.obs_id or row.obs_id in focus_ids)
            for row in self.state.outcomes
        )
        if failed and not compiled.spec.optional:
            self._fail_stage(compiled, "inspect tool failed")
            return False
        if not self._inspect_status_complete(snapshot):
            return False
        logs_ok = any(
            row.tool_name == "get_pod_logs" and row.status == "success"
            for row in self.state.outcomes
        )
        if logs_ok or self.state.inspect_ready_to_leave:
            self._finish_stage(compiled.spec.id, STAGE_SUCCESS, "complete")
            return True
        self.state.inspect_status_seen = True
        self._emit("inspect_dwell", "keep logs available after first status")
        return False

    def _merge_bound_skills(self, *, catalog_names: Sequence[str]) -> None:
        if self.state is None or self.plan is None or self.state.decision is None:
            return
        bindings = self.state.decision.object_bindings or {}
        skill_ids = [
            sid for sid in dict.fromkeys(
                [self.state.decision.skill_id, *bindings.values()]
            )
            if sid
        ]
        inspect = self._stage_by_id("inspect")
        extra_text = []
        registry = self.ensure_registry()
        for sid in skill_ids:
            row = registry.get(sid)
            plan = compile_workflow(row.workflow, registry, skill_id=sid)
            if inspect is not None:
                for item in plan.stages:
                    if item.spec.processor != PROCESSOR_INSPECT:
                        continue
                    for name in item.spec.tools:
                        if name not in inspect.spec.tools:
                            inspect.spec.tools.append(name)
                    for name in item.spec.optional_tools:
                        if name not in inspect.spec.optional_tools:
                            inspect.spec.optional_tools.append(name)
            if sid == self.state.decision.skill_id:
                continue
            extra_text.append(f"## Bound skill {sid}\n{self.loader.load_instructions(row.meta)}")
            if sid not in self.state.loaded_instructions:
                self.state.loaded_instructions.append(sid)
        if extra_text:
            self._skill_text = (self._skill_text + "\n\n" + "\n\n".join(extra_text)).strip()
        if inspect is not None and self.state.current_stage_id == "inspect":
            granted = allowed_tools(
                catalog_names=catalog_names or inspect.spec.tools,
                requested=list(inspect.spec.tools) + list(inspect.spec.optional_tools),
                policy_capabilities=_policy_caps(self.settings.writes),
                allow_writes=self.settings.writes,
            )
            self.state.allowed_tools = granted

    def _jump_to_synthesize(self, reason: str, catalog_names: Sequence[str] = ()) -> None:
        if self.plan is None or self.state is None:
            return
        self.state.unknown_reason = self.state.unknown_reason or reason
        for item in self.plan.stages:
            if item.spec.processor == PROCESSOR_SYNTHESIZE:
                self._enter_stage(item.spec.id, catalog_names=catalog_names or self._catalog_names)
                self.state.allowed_tools = []
                return

    def _finish_stage(self, stage_id: str, status: str, reason: str) -> None:
        if self.state is None:
            return
        processor = ""
        compiled = self._stage_by_id(stage_id)
        if compiled is not None:
            processor = compiled.spec.processor
        self.state.stage_results.append(StageResult(
            stage_id=stage_id,
            status=status,
            reason=reason,
            processor=processor,
        ))
        self._emit(status, f"{stage_id}: {reason}")

    def _current_stage(self):
        if self.state is None:
            return None
        return self._stage_by_id(self.state.current_stage_id)

    def _stage_by_id(self, stage_id: str):
        if not self.plan or not stage_id:
            return None
        for item in self.plan.stages:
            if item.spec.id == stage_id:
                return item
        return None

    def _next_after(self, plan: CompiledPlan, stage_id: str) -> str:
        ids = plan.stage_ids()
        if stage_id not in ids:
            return ids[0] if ids else ""
        idx = ids.index(stage_id)
        if idx + 1 < len(ids):
            return ids[idx + 1]
        return ""

    def _enrich_status(self, snapshot) -> None:
        if self.state is None:
            return
        by_name = {item.name: item for item in snapshot.focus}
        by_obs = {item.obs_id: item for item in snapshot.focus if item.obs_id}
        for row in self.state.outcomes:
            if row.tool_name != "get_pod_status" or row.status != "success":
                continue
            for parsed in enrich_from_status(row.text):
                name = parsed.get("name", "")
                target = by_name.get(name) if name else None
                if target is None and not name and row.obs_id:
                    target = by_obs.get(row.obs_id)
                if target is None:
                    self.state.unbound_status.append(name or "(unnamed)")
                    continue
                if name and target.name and name != target.name:
                    self.state.unbound_status.append(name)
                    continue
                apply_status(target, parsed)
                self.state.object_facts[target.obs_id] = target
                if not row.obs_id:
                    row.obs_id = target.obs_id
                if not row.object_name:
                    row.object_name = target.name

    def _emit(self, kind: str, detail: str) -> None:
        if self.state is None:
            return
        self.state.events.append({"kind": kind, "detail": detail})


def _settings_from_config(config: Any) -> SkillSettings:
    ready = getattr(config, "skill_settings", None)
    if isinstance(ready, SkillSettings):
        return ready
    return resolve_skill_settings(
        cli_routing=getattr(config, "skill_routing", None),
        cli_skill=getattr(config, "skill_id", None) or None,
        cli_dirs=getattr(config, "skill_extra_dirs", None),
        cli_disabled=getattr(config, "skill_disabled", None),
        cli_writes=getattr(config, "skill_writes", None),
        cli_instruction_dir=getattr(config, "skill_instruction_dir", None),
    )


def _policy_caps(writes: bool) -> list[str]:
    caps = ["scan", "inspect", "logs", "verify", "metrics"]
    if writes:
        caps.append("write")
    return caps


def _facts(snapshot, state: SkillRunState) -> Dict[str, Any]:
    succeeded = [row.tool_name for row in state.outcomes if row.status == "success"]
    facts: Dict[str, Any] = {
        "scan_status": snapshot.scan_status,
        "focus_count": len(snapshot.focus),
        "has_focus": bool(snapshot.focus),
        "intent": state.request.intent,
        "entrypoint": state.request.entrypoint,
        "successful_tools": succeeded,
        "stage_id": state.current_stage_id,
        "capability_missing": bool(state.unknown_reason),
    }
    if snapshot.focus:
        facts.update(snapshot.focus[0].routing_facts(snapshot.scan_status, len(snapshot.focus)))
        facts["successful_tools"] = succeeded
        facts["focus_count"] = len(snapshot.focus)
    return facts


def _eval(conditions, facts) -> bool:
    from .conditions import eval_all
    return eval_all(conditions, facts)


def _snapshot_key(snapshot) -> str:
    from .router import _obs_key
    return _obs_key(snapshot)
