"""Compile declared workflows: compose fragments, apply replacements, preview plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .conditions import validate_condition
from .errors import SkillError
from .models import StageReplacement, StageSpec, WorkflowSpec
from .processors import PROCESSORS, compatible_stage_replacement, get_processor
from .registry import RegisteredSkill, SkillRegistry


@dataclass
class CompiledStage:
    spec: StageSpec
    origin: str

    def as_dict(self) -> dict:
        payload = self.spec.as_dict()
        payload["origin"] = self.origin
        return payload


@dataclass
class CompiledPlan:
    workflow_id: str
    version: str
    stages: List[CompiledStage] = field(default_factory=list)
    replacements: List[str] = field(default_factory=list)

    def stage_ids(self) -> List[str]:
        return [item.spec.id for item in self.stages]

    def as_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "version": self.version,
            "stages": [item.as_dict() for item in self.stages],
            "replacements": list(self.replacements),
        }


def compile_workflow(
    spec: WorkflowSpec,
    registry: SkillRegistry,
    *,
    skill_id: str = "",
) -> CompiledPlan:
    fragments = _fragment_index(registry)
    expanded = _expand(spec, fragments, skill_id=skill_id, stack=[spec.id])
    replacements: List[str] = []
    for item in spec.replacements:
        expanded, note = _apply_replace(expanded, item, fragments, skill_id=skill_id)
        replacements.append(note)
    seen = []
    ids = []
    for stage, origin in expanded:
        if stage.id in ids:
            raise SkillError(
                f"duplicate stage id {stage.id!r} after compose",
                skill_id=skill_id,
                field="stages",
            )
        ids.append(stage.id)
        get_processor(stage.processor, skill_id=skill_id)
        for cond in stage.enter_when + stage.complete_when:
            validate_condition(cond, skill_id=skill_id)
        seen.append(CompiledStage(spec=stage, origin=origin))
    return CompiledPlan(
        workflow_id=spec.id,
        version=spec.version,
        stages=seen,
        replacements=replacements,
    )


def preview_plan(
    skill: RegisteredSkill,
    registry: SkillRegistry,
    *,
    snapshot: Optional[dict] = None,
) -> dict:
    plan = compile_workflow(skill.workflow, registry, skill_id=skill.meta.id)
    live_needed = True
    if snapshot and snapshot.get("scan_status") in {"success", "empty", "failed"}:
        live_needed = False
    return {
        "skill_id": skill.meta.id,
        "skill_version": skill.meta.version,
        "workflow_id": plan.workflow_id,
        "stages": plan.as_dict()["stages"],
        "replacements": plan.replacements,
        "needs_live_evidence": live_needed,
        "note": "preview compiles the same plan as runtime; it does not call tools",
    }


def _fragment_index(registry: SkillRegistry) -> Dict[str, WorkflowSpec]:
    out: Dict[str, WorkflowSpec] = {}
    for row in registry.list(include_disabled=True):
        if row.meta.id.startswith("fragment:"):
            out[row.meta.id] = row.workflow
            key = row.meta.id.split(":", 1)[-1]
            out[f"fragment:{key}"] = row.workflow
            out[key] = row.workflow
        if row.workflow.id.startswith("fragment:"):
            out[row.workflow.id] = row.workflow
    return out


def _expand(
    spec: WorkflowSpec,
    fragments: Dict[str, WorkflowSpec],
    *,
    skill_id: str,
    stack: List[str],
) -> List[tuple[StageSpec, str]]:
    if spec.compose:
        out: List[tuple[StageSpec, str]] = []
        for ref in spec.compose:
            if ref in stack:
                raise SkillError(
                    f"recursive compose {ref!r}",
                    skill_id=skill_id,
                    field="compose",
                )
            fragment = fragments.get(ref) or fragments.get(_frag_key(ref))
            if fragment is None:
                raise SkillError(
                    f"unknown compose reference {ref!r}",
                    skill_id=skill_id,
                    field="compose",
                )
            if fragment.compose:
                nested = _expand(
                    fragment,
                    fragments,
                    skill_id=skill_id,
                    stack=[*stack, ref],
                )
                out.extend(nested)
            else:
                origin = f"fragment:{fragment.id}"
                for stage in fragment.stages:
                    copied = StageSpec.from_dict(stage.as_dict(), skill_id=skill_id)
                    copied.source = origin
                    out.append((copied, origin))
        for stage in spec.stages:
            copied = StageSpec.from_dict(stage.as_dict(), skill_id=skill_id)
            copied.source = f"workflow:{spec.id}"
            out.append((copied, copied.source))
        return out
    return [
        (StageSpec.from_dict(stage.as_dict(), skill_id=skill_id), f"workflow:{spec.id}")
        for stage in spec.stages
    ]


def _apply_replace(
    stages: List[tuple[StageSpec, str]],
    item: StageReplacement,
    fragments: Dict[str, WorkflowSpec],
    *,
    skill_id: str,
) -> tuple[List[tuple[StageSpec, str]], str]:
    replacement = item.stage
    if item.with_fragment:
        fragment = fragments.get(item.with_fragment) or fragments.get(_frag_key(item.with_fragment))
        if fragment is None:
            raise SkillError(
                f"unknown replacement fragment {item.with_fragment!r}",
                skill_id=skill_id,
                field="replace",
            )
        if not fragment.stages:
            raise SkillError(
                f"replacement fragment {item.with_fragment!r} has no stages",
                skill_id=skill_id,
                field="replace",
            )
        replacement = StageSpec.from_dict(fragment.stages[0].as_dict(), skill_id=skill_id)
        replacement.id = item.stage_id
    if not replacement.processor:
        raise SkillError(
            "replacement stage is missing processor",
            skill_id=skill_id,
            field="replace",
        )
    found = False
    out: List[tuple[StageSpec, str]] = []
    note = ""
    for stage, origin in stages:
        if stage.id != item.stage_id:
            out.append((stage, origin))
            continue
        found = True
        new_proc = get_processor(replacement.processor, skill_id=skill_id)
        reason = compatible_stage_replacement(stage, replacement)
        if reason:
            raise SkillError(reason, skill_id=skill_id, field="replace")
        if not replacement.outputs:
            replacement.outputs = list(new_proc.outputs)
        copied = StageSpec.from_dict(replacement.as_dict(), skill_id=skill_id)
        copied.id = item.stage_id
        copied.source = f"replace:{item.stage_id}"
        out.append((copied, copied.source))
        note = f"{item.stage_id} <- {copied.source} processor={copied.processor}"
    if not found:
        raise SkillError(
            f"cannot replace unknown stage {item.stage_id!r}",
            skill_id=skill_id,
            field="replace",
        )
    return out, note


def _frag_key(ref: str) -> str:
    if ref.startswith("fragment:"):
        return ref
    return f"fragment:{ref}"
