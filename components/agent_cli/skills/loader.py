"""Resource path sandbox and delayed instruction loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

from .errors import SkillError
from .fingerprint import fingerprint_bytes, fingerprint_text
from .models import SkillMetadata, WorkflowSpec


ReadBytes = Callable[[str], bytes]


@dataclass
class LoadedResources:
    manifest_fp: str
    workflow_fp: str
    instructions_fp: str = ""
    instructions: str = ""
    reads: Dict[str, int] = field(default_factory=dict)


class ResourceLoader:
    """Resolve files inside a skill package. Instruction bodies load on activate."""

    def __init__(
        self,
        *,
        read_bytes: Optional[ReadBytes] = None,
        instruction_dir: str = "",
    ):
        self._read_bytes = read_bytes
        self._instruction_cache: Dict[str, str] = {}
        self.reads: Dict[str, int] = {}
        self.instruction_dir = instruction_dir

    def resolve(self, root: Path, relative: str, *, skill_id: str = "") -> Path:
        if not relative or not str(relative).strip():
            raise SkillError(
                "resource path is empty",
                skill_id=skill_id,
                field="path",
            )
        rel = Path(relative)
        if rel.is_absolute():
            raise SkillError(
                "absolute resource paths are not allowed",
                skill_id=skill_id,
                path=relative,
            )
        if ".." in rel.parts:
            raise SkillError(
                "parent-directory resource paths are not allowed",
                skill_id=skill_id,
                path=relative,
            )
        base = root.resolve()
        candidate = (base / rel)
        if candidate.is_symlink():
            target = candidate.resolve()
            if not _inside(target, base):
                raise SkillError(
                    "symlink resource escapes the skill package",
                    skill_id=skill_id,
                    path=relative,
                )
        resolved = candidate.resolve()
        if not _inside(resolved, base):
            raise SkillError(
                "resource path escapes the skill package",
                skill_id=skill_id,
                path=relative,
            )
        return resolved

    def read_text(self, root: Path, relative: str, *, skill_id: str = "") -> str:
        path = self.resolve(root, relative, skill_id=skill_id)
        key = str(path)
        self.reads[key] = self.reads.get(key, 0) + 1
        if self._read_bytes is not None:
            data = self._read_bytes(key)
        else:
            data = path.read_bytes()
        return data.decode("utf-8")

    def load_instructions(self, meta: SkillMetadata) -> str:
        cache_key = f"{meta.id}@{meta.version}:{meta.instructions_file}:{self.instruction_dir}"
        if cache_key in self._instruction_cache:
            return self._instruction_cache[cache_key]
        override = self._override_text(meta.id)
        if override is not None:
            self._instruction_cache[cache_key] = override
            return override
        text = self.read_text(Path(meta.root), meta.instructions_file, skill_id=meta.id)
        self._instruction_cache[cache_key] = text
        return text

    def _override_text(self, skill_id: str) -> Optional[str]:
        if not self.instruction_dir:
            return None
        base = Path(self.instruction_dir).resolve()
        candidate = (base / skill_id / "SKILL.md").resolve()
        if not candidate.is_file():
            return None
        if not _inside(candidate, base):
            raise SkillError(
                "instruction override escapes instruction_dir",
                skill_id=skill_id,
                field="instruction_dir",
            )
        key = str(candidate)
        self.reads[key] = self.reads.get(key, 0) + 1
        return candidate.read_text(encoding="utf-8-sig")

    def snapshot(
        self,
        meta: SkillMetadata,
        workflow: WorkflowSpec,
        *,
        load_instructions: bool = False,
        manifest_bytes: bytes = b"",
        workflow_bytes: bytes = b"",
    ) -> LoadedResources:
        loaded = LoadedResources(
            manifest_fp=fingerprint_bytes(manifest_bytes),
            workflow_fp=fingerprint_bytes(workflow_bytes or fingerprint_text(str(workflow.as_dict())).encode()),
        )
        if load_instructions:
            text = self.load_instructions(meta)
            loaded.instructions = text
            loaded.instructions_fp = fingerprint_text(text)
        loaded.reads = dict(self.reads)
        return loaded


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
