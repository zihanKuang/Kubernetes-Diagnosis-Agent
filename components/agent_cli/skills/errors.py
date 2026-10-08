"""Skill errors that name the skill, file, and field."""

from __future__ import annotations


class SkillError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        skill_id: str = "",
        path: str = "",
        field: str = "",
    ):
        self.skill_id = skill_id
        self.path = str(path or "")
        self.field = field
        loc = []
        if skill_id:
            loc.append(f"skill={skill_id}")
        if self.path:
            loc.append(f"file={self.path}")
        if field:
            loc.append(f"field={field}")
        prefix = f"[{', '.join(loc)}] " if loc else ""
        super().__init__(prefix + message)
