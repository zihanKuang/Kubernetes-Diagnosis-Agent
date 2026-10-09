"""Discover and index Skill packages. Builtin via package resources; extras only from configured dirs."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .errors import SkillError
from .fingerprint import fingerprint_bytes
from .loader import ResourceLoader
from .models import (
    SOURCE_BUILTIN,
    SOURCE_EXTERNAL,
    SkillMetadata,
    WorkflowSpec,
)
from .processors import get_processor
from .conditions import validate_condition


@dataclass
class RegisteredSkill:
    meta: SkillMetadata
    workflow: WorkflowSpec
    manifest_bytes: bytes
    workflow_bytes: bytes
    disabled: bool = False

    def fingerprint(self) -> str:
        return fingerprint_bytes(self.manifest_bytes + b"\0" + self.workflow_bytes)


class SkillRegistry:
    def __init__(
        self,
        *,
        extra_dirs: Optional[Iterable[str]] = None,
        disabled: Optional[Iterable[str]] = None,
        loader: Optional[ResourceLoader] = None,
        include_builtin: bool = True,
    ):
        self.extra_dirs = [str(Path(item)) for item in (extra_dirs or [])]
        self.disabled_ids = {item.strip() for item in (disabled or []) if item.strip()}
        self.loader = loader or ResourceLoader()
        self._by_key: Dict[Tuple[str, str], RegisteredSkill] = {}
        self._order: List[Tuple[str, str]] = []
        if include_builtin:
            self._load_builtin()
        for directory in sorted(self.extra_dirs):
            self._load_external_root(Path(directory))

    def list(self, *, include_disabled: bool = True) -> List[RegisteredSkill]:
        rows = [self._by_key[key] for key in self._order]
        if include_disabled:
            return rows
        return [row for row in rows if not row.disabled and row.meta.enabled]

    def get(self, skill_id: str, version: str = "") -> RegisteredSkill:
        matches = [row for row in self.list() if row.meta.id == skill_id]
        if not matches:
            raise SkillError(f"unknown skill {skill_id!r}", skill_id=skill_id)
        if version:
            for row in matches:
                if row.meta.version == version:
                    return row
            raise SkillError(
                f"skill {skill_id!r} has no version {version!r}",
                skill_id=skill_id,
                field="version",
            )
        if len(matches) > 1:
            versions = ", ".join(sorted({row.meta.version for row in matches}))
            raise SkillError(
                f"skill {skill_id!r} has multiple versions ({versions}); specify one",
                skill_id=skill_id,
                field="version",
            )
        return matches[0]

    def available(self, skill_id: str) -> RegisteredSkill:
        row = self.get(skill_id)
        if row.disabled or not row.meta.enabled:
            raise SkillError(
                f"skill {skill_id!r} is disabled",
                skill_id=skill_id,
            )
        return row

    def fragments(self) -> Dict[str, WorkflowSpec]:
        out: Dict[str, WorkflowSpec] = {}
        for row in self.list():
            if row.meta.id.startswith("fragment:"):
                out[row.meta.id] = row.workflow
            elif row.workflow.id.startswith("fragment:"):
                out[row.workflow.id] = row.workflow
        # Fragments are loaded as workflow-only records under fragment:* ids.
        return out

    def _register(self, row: RegisteredSkill) -> None:
        key = row.meta.key()
        if key in self._by_key:
            raise SkillError(
                f"duplicate skill id/version {row.meta.id}@{row.meta.version}",
                skill_id=row.meta.id,
                path=row.meta.root,
            )
        row.disabled = row.meta.id in self.disabled_ids or not row.meta.enabled
        self._by_key[key] = row
        self._order.append(key)

    def _load_builtin(self) -> None:
        traversable = resources.files("agent_cli.skills.builtin")
        names = sorted(item.name for item in traversable.iterdir() if item.is_dir())
        for name in names:
            if name.startswith("_") or name == "fragments":
                continue
            child = traversable / name
            skill_file = child / "SKILL.toml"
            if not skill_file.is_file():
                continue
            manifest_bytes = skill_file.read_bytes()
            workflow_bytes = (child / "workflow.toml").read_bytes()
            # importlib.resources may be in a zip; copy to a logical root path.
            root = _as_path(child)
            self._ingest(
                manifest_bytes,
                workflow_bytes,
                root=root,
                source=SOURCE_BUILTIN,
                path=str(skill_file),
            )
        self._load_builtin_fragments(traversable / "fragments")

    def _load_builtin_fragments(self, fragments) -> None:
        if not fragments.is_dir():
            return
        files = sorted(item.name for item in fragments.iterdir() if item.name.endswith(".toml"))
        for name in files:
            data = tomllib.loads((fragments / name).read_bytes().decode("utf-8"))
            spec = WorkflowSpec.from_dict(data, path=f"builtin/fragments/{name}")
            for stage in spec.stages:
                get_processor(stage.processor, path=f"builtin/fragments/{name}")
                for cond in stage.enter_when + stage.complete_when:
                    validate_condition(cond, path=f"builtin/fragments/{name}")
            fragment_id = spec.id if spec.id.startswith("fragment:") else f"fragment:{spec.id}"
            meta = SkillMetadata(
                schema_version="1",
                id=fragment_id,
                version=spec.version,
                description=f"Reusable fragment {fragment_id}",
                workflow_id=fragment_id,
                instructions_file="SKILL.md",
                tags=["fragment"],
                entrypoints=["cli"],
                enabled=False,
                source=SOURCE_BUILTIN,
                root=str(_as_path(fragments)),
                workflow_file=name,
            )
            self._register(RegisteredSkill(
                meta=meta,
                workflow=spec,
                manifest_bytes=b"",
                workflow_bytes=(fragments / name).read_bytes(),
                disabled=True,
            ))

    def _load_external_root(self, root: Path) -> None:
        if not root.exists():
            raise SkillError(f"skill directory does not exist: {root}", path=str(root))
        if not root.is_dir():
            raise SkillError(f"skill directory is not a directory: {root}", path=str(root))
        children = sorted(p for p in root.iterdir() if p.is_dir())
        found = False
        if (root / "SKILL.toml").is_file():
            self._load_package(root, source=SOURCE_EXTERNAL)
            found = True
        for child in children:
            if (child / "SKILL.toml").is_file():
                self._load_package(child, source=SOURCE_EXTERNAL)
                found = True
        if not found:
            raise SkillError(
                f"no SKILL.toml under {root}",
                path=str(root),
            )

    def _load_package(self, root: Path, *, source: str) -> None:
        skill_path = root / "SKILL.toml"
        workflow_path = root / "workflow.toml"
        if not workflow_path.is_file():
            raise SkillError(
                "workflow.toml is required",
                path=str(root),
                field="workflow_file",
            )
        self._ingest(
            skill_path.read_bytes(),
            workflow_path.read_bytes(),
            root=root,
            source=source,
            path=str(skill_path),
        )

    def _ingest(
        self,
        manifest_bytes: bytes,
        workflow_bytes: bytes,
        *,
        root: Path,
        source: str,
        path: str,
    ) -> None:
        try:
            manifest = tomllib.loads(manifest_bytes.decode("utf-8"))
        except Exception as exc:
            raise SkillError(f"invalid SKILL.toml: {exc}", path=path) from exc
        meta = SkillMetadata.from_dict(
            manifest,
            path=path,
            source=source,
            root=str(root),
        )
        self.loader.resolve(Path(root), meta.instructions_file, skill_id=meta.id)
        self.loader.resolve(Path(root), meta.workflow_file, skill_id=meta.id)
        try:
            workflow_data = tomllib.loads(workflow_bytes.decode("utf-8"))
        except Exception as exc:
            raise SkillError(
                f"invalid workflow.toml: {exc}",
                skill_id=meta.id,
                path=str(Path(root) / meta.workflow_file),
            ) from exc
        spec = WorkflowSpec.from_dict(
            workflow_data,
            skill_id=meta.id,
            path=str(Path(root) / meta.workflow_file),
        )
        for stage in spec.stages:
            if stage.processor:
                get_processor(stage.processor, skill_id=meta.id, path=str(root))
            for cond in stage.enter_when + stage.complete_when:
                validate_condition(cond, skill_id=meta.id, path=str(root))
            for cond in meta.routing.observations + meta.routing.exclude:
                validate_condition(cond, skill_id=meta.id, path=path)
        self._register(RegisteredSkill(
            meta=meta,
            workflow=spec,
            manifest_bytes=manifest_bytes,
            workflow_bytes=workflow_bytes,
        ))


def _as_path(traversable) -> Path:
    try:
        return Path(traversable)
    except TypeError:
        return Path(str(traversable))
