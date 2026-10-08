"""Citrus Skill runtime: register, route, load on demand, compose workflows.

This package is the project's own Skill mechanism. It does not read Cursor or
Codex personal skill directories.
"""

from .errors import SkillError

__all__ = ["SkillError"]
