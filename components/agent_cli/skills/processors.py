"""Controlled stage processors. Manifests cannot import arbitrary Python."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

from .errors import SkillError

PROCESSOR_SCAN = "scan"
PROCESSOR_INSPECT = "inspect"
PROCESSOR_VERIFY = "verify"
PROCESSOR_SYNTHESIZE = "synthesize"


@dataclass(frozen=True)
class ProcessorContract:
    name: str
    inputs: FrozenSet[str]
    outputs: FrozenSet[str]
    capabilities: FrozenSet[str]
    protected: bool = False


PROCESSORS = {
    PROCESSOR_SCAN: ProcessorContract(
        name=PROCESSOR_SCAN,
        inputs=frozenset({"query"}),
        outputs=frozenset({"observation_snapshot"}),
        capabilities=frozenset({"scan"}),
    ),
    PROCESSOR_INSPECT: ProcessorContract(
        name=PROCESSOR_INSPECT,
        inputs=frozenset({"observation_snapshot", "focus"}),
        outputs=frozenset({"inspect_records"}),
        capabilities=frozenset({"inspect"}),
    ),
    PROCESSOR_VERIFY: ProcessorContract(
        name=PROCESSOR_VERIFY,
        inputs=frozenset({"focus"}),
        outputs=frozenset({"recovery_verdict"}),
        capabilities=frozenset({"verify"}),
        protected=True,
    ),
    PROCESSOR_SYNTHESIZE: ProcessorContract(
        name=PROCESSOR_SYNTHESIZE,
        inputs=frozenset({"observation_snapshot", "inspect_records"}),
        outputs=frozenset({"diagnosis_draft"}),
        capabilities=frozenset(),
        protected=True,
    ),
}


def get_processor(name: str, *, skill_id: str = "", path: str = "") -> ProcessorContract:
    contract = PROCESSORS.get(name)
    if contract is None:
        raise SkillError(
            f"unknown processor {name!r}; allowed: {sorted(PROCESSORS)}",
            skill_id=skill_id,
            path=path,
            field="processor",
        )
    return contract


def compatible_replacement(old: ProcessorContract, new: ProcessorContract) -> str:
    """Empty string if compatible. Otherwise a reason."""
    if old.protected and new.name != old.name:
        return (
            f"cannot replace protected processor {old.name} with {new.name}"
        )
    missing = old.outputs - new.outputs
    if missing:
        return f"replacement drops required outputs {sorted(missing)}"
    if old.capabilities - new.capabilities:
        return (
            f"replacement drops required capabilities "
            f"{sorted(old.capabilities - new.capabilities)}"
        )
    return ""


def compatible_stage_replacement(old_stage, new_stage) -> str:
    """Stage-level contract: tools and outputs, not just processor names."""
    old_proc = get_processor(old_stage.processor)
    new_proc = get_processor(new_stage.processor)
    reason = compatible_replacement(old_proc, new_proc)
    if reason:
        return reason
    new_tools = list(new_stage.tools or []) + list(new_stage.complete_tools or [])
    if old_proc.name == PROCESSOR_VERIFY:
        if "validate_recovery" not in new_tools:
            return "verify replacement must keep validate_recovery"
        if "recovery_verdict" not in list(new_stage.outputs or []):
            return "verify replacement must keep recovery_verdict"
        if set(new_stage.tools or []) <= {"list_pods"}:
            return "verify replacement does not produce recovery results"
    if old_proc.name == PROCESSOR_SYNTHESIZE:
        if "diagnosis_draft" not in list(new_stage.outputs or []):
            return "synthesize replacement must keep diagnosis_draft"
    return ""
