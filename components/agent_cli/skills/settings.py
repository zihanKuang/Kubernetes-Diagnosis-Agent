"""Skill routing / directory settings. CLI beats env beats defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Mapping, Optional

from .errors import SkillError

MODE_LEGACY = "legacy"
MODE_AUTO = "auto"
SUPPORTED_MODES = frozenset({MODE_LEGACY, MODE_AUTO})


@dataclass
class SkillSettings:
    routing: str = MODE_LEGACY
    skill_id: str = ""
    extra_dirs: List[str] = field(default_factory=list)
    disabled: List[str] = field(default_factory=list)
    writes: bool = False
    instruction_dir: str = ""

    def as_dict(self) -> dict:
        return {
            "routing": self.routing,
            "skill_id": self.skill_id,
            "extra_dirs": list(self.extra_dirs),
            "disabled": list(self.disabled),
            "writes": self.writes,
            "instruction_dir": self.instruction_dir,
        }


def resolve_skill_settings(
    *,
    cli_routing: Optional[str] = None,
    cli_skill: Optional[str] = None,
    cli_dirs: Optional[List[str]] = None,
    cli_disabled: Optional[List[str]] = None,
    cli_writes: Optional[bool] = None,
    cli_instruction_dir: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    ablation: Optional[str] = None,
) -> SkillSettings:
    """CLI explicit values win over environment over defaults."""
    environ = env if env is not None else os.environ
    routing = MODE_LEGACY
    env_routing = (environ.get("CITRUS_SKILL_ROUTING") or "").strip().lower()
    if env_routing:
        routing = env_routing
    if cli_routing:
        routing = cli_routing.strip().lower()
    if routing not in SUPPORTED_MODES:
        raise SkillError(
            f"skill routing must be legacy or auto, got {routing!r}",
            field="skill_routing",
        )

    skill_id = (environ.get("CITRUS_SKILL") or "").strip()
    if cli_skill is not None:
        skill_id = cli_skill.strip()

    dirs = _split_paths(environ.get("CITRUS_SKILL_DIRS") or "")
    if cli_dirs is not None:
        dirs = [item.strip() for item in cli_dirs if item and item.strip()]

    disabled = _split_csv(environ.get("CITRUS_SKILL_DISABLED") or "")
    if cli_disabled is not None:
        disabled = [item.strip() for item in cli_disabled if item and item.strip()]

    writes = (environ.get("CITRUS_SKILL_WRITES") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if cli_writes is not None:
        writes = cli_writes

    instruction_dir = (environ.get("CITRUS_SKILL_INSTRUCTION_DIR") or "").strip()
    if cli_instruction_dir is not None:
        instruction_dir = cli_instruction_dir.strip()

    if skill_id and routing == MODE_LEGACY and cli_routing == MODE_LEGACY:
        raise SkillError(
            "--skill cannot be combined with --skill-routing legacy",
            field="skill",
        )
    if skill_id:
        routing = MODE_AUTO

    ablation_value = ablation
    if ablation_value is None:
        ablation_value = (environ.get("CITRUS_ABLATION") or "").strip().lower()
    if ablation_value == "legacy" and routing == MODE_AUTO:
        raise SkillError(
            "CITRUS_ABLATION=legacy cannot be combined with skill routing auto",
            field="skill_routing",
        )

    return SkillSettings(
        routing=routing,
        skill_id=skill_id,
        extra_dirs=dirs,
        disabled=disabled,
        writes=writes,
        instruction_dir=instruction_dir,
    )


def _split_paths(raw: str) -> List[str]:
    if not raw.strip():
        return []
    parts = []
    for chunk in raw.replace(";", os.pathsep).split(os.pathsep):
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts


def _split_csv(raw: str) -> List[str]:
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]
