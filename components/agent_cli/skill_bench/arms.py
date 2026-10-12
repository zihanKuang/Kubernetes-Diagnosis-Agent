"""A/B/C experiment arms. A is DEFAULT_SRE, not the ablation prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import AgentConfig
from ..prompts import DEFAULT_SRE_SYSTEM_INSTRUCTION, LEGACY_SRE_SYSTEM_INSTRUCTION
from ..skills.fingerprint import fingerprint_text
from ..skills.settings import resolve_skill_settings
from .conditions import SHARED
from .models import ARM_LABELS

ADAPTED_DIR = Path(__file__).resolve().parent / "adapted_instructions"

if DEFAULT_SRE_SYSTEM_INSTRUCTION.strip() == LEGACY_SRE_SYSTEM_INSTRUCTION.strip():
    raise RuntimeError("arm A must not equal LEGACY_SRE_SYSTEM_INSTRUCTION")


@dataclass(frozen=True)
class ArmSpec:
    arm_id: str
    label: str
    skill_routing: str
    instruction_dir: str
    system_instruction: str

    def config(self, **overrides: Any) -> AgentConfig:
        values = dict(
            api_key=overrides.get("api_key"),
            memory_enabled=False,
            writes_interactive=False,
            max_steps=int(overrides.get("max_steps") or SHARED["max_steps"]),
            max_tool_calls=overrides.get("max_tool_calls", SHARED["max_tool_calls"]),
            skill_routing=self.skill_routing,
            skill_instruction_dir=self.instruction_dir,
            system_instruction=self.system_instruction,
            trace_dir=overrides.get("trace_dir"),
            model_name=overrides.get("model_name") or SHARED["model_name"],
            llm_provider=overrides.get("llm_provider") or SHARED["llm_provider"],
            llm_timeout_seconds=float(
                overrides.get("llm_timeout_seconds") or SHARED["llm_timeout_seconds"]
            ),
            llm_max_tokens=int(overrides.get("llm_max_tokens") or SHARED["llm_max_tokens"]),
            max_retries=int(overrides.get("max_retries") or SHARED["max_retries"]),
            tool_timeout_seconds=float(
                overrides.get("tool_timeout_seconds") or SHARED["tool_timeout_seconds"]
            ),
        )
        if "llm_base_url" in overrides:
            values["llm_base_url"] = overrides["llm_base_url"]
        cfg = AgentConfig(**values)
        cfg.memory_enabled = False
        cfg.writes_interactive = False
        cfg.system_instruction = self.system_instruction
        # Re-apply frozen experiment fields after env post_init.
        cfg.model_name = values["model_name"]
        cfg.llm_provider = values["llm_provider"]
        cfg.llm_max_tokens = values["llm_max_tokens"]
        cfg.max_steps = values["max_steps"]
        cfg.max_tool_calls = values["max_tool_calls"]
        cfg.skill_settings = resolve_skill_settings(
            cli_routing=self.skill_routing,
            cli_instruction_dir=self.instruction_dir,
            env={},
        )
        cfg.skill_routing = cfg.skill_settings.routing
        cfg.skill_instruction_dir = cfg.skill_settings.instruction_dir or None
        if "memory_path" in overrides:
            cfg.memory_path = overrides["memory_path"]
        return cfg

    def fingerprints(self) -> Dict[str, str]:
        payload: Dict[str, Any] = {
            "arm": self.arm_id,
            "label": self.label,
            "skill_routing": self.skill_routing,
            "system_instruction": fingerprint_text(self.system_instruction),
            "instruction_dir": self.instruction_dir,
            "not_ablation_legacy_prompt": fingerprint_text(self.system_instruction)
            != fingerprint_text(LEGACY_SRE_SYSTEM_INSTRUCTION),
        }
        if self.instruction_dir:
            texts = []
            root = Path(self.instruction_dir)
            for path in sorted(root.glob("*/SKILL.md")):
                texts.append(f"{path.parent.name}:{fingerprint_text(path.read_text(encoding='utf-8-sig'))}")
            payload["adapted_files"] = texts
        return payload


def arm_a() -> ArmSpec:
    return ArmSpec(
        arm_id="A",
        label=ARM_LABELS["A"],
        skill_routing="legacy",
        instruction_dir="",
        system_instruction=DEFAULT_SRE_SYSTEM_INSTRUCTION,
    )


def arm_b() -> ArmSpec:
    return ArmSpec(
        arm_id="B",
        label=ARM_LABELS["B"],
        skill_routing="auto",
        instruction_dir="",
        system_instruction=DEFAULT_SRE_SYSTEM_INSTRUCTION,
    )


def arm_c() -> ArmSpec:
    return ArmSpec(
        arm_id="C",
        label=ARM_LABELS["C"],
        skill_routing="auto",
        instruction_dir=str(ADAPTED_DIR),
        system_instruction=DEFAULT_SRE_SYSTEM_INSTRUCTION,
    )


def all_arms() -> Dict[str, ArmSpec]:
    return {"A": arm_a(), "B": arm_b(), "C": arm_c()}


def get_arm(arm_id: str) -> ArmSpec:
    arms = all_arms()
    if arm_id not in arms:
        raise KeyError(f"unknown arm {arm_id}")
    return arms[arm_id]
