"""Exam case and score records. Truth never goes to the model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


ARMS = ("A", "B", "C")
ARM_LABELS = {
    "A": "legacy",
    "B": "own-skills",
    "C": "external-adapted",
}


@dataclass
class ToolFixture:
    default: str = ""
    by_selector: Dict[str, str] = field(default_factory=dict)
    by_obs: Dict[str, str] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "default": self.default,
            "by_selector": dict(self.by_selector),
            "by_obs": dict(self.by_obs),
            "error": self.error,
        }


@dataclass
class CaseTruth:
    """Scorer-only. Never copied into RequestContext or prompts."""
    focus_names: List[str]
    required_status: List[str]
    supported_facts: List[str]
    forbidden_facts: List[str]
    allow_unknown_cause: bool = False
    allow_bounded_healthy: bool = False
    require_recent_incident: bool = False
    recovery_expected: str = "none"  # pass|fail|unknown|none
    recovery_object: str = ""
    coverage_objects: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Case:
    case_id: str
    split: str
    kind: str
    query: str
    collected_at: str
    window_start: str
    objects: List[Dict[str, str]]
    tools: Dict[str, ToolFixture]
    truth: CaseTruth
    version: str = "1"

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "kind": self.kind,
            "query": self.query,
            "collected_at": self.collected_at,
            "window_start": self.window_start,
            "objects": list(self.objects),
            "tools": {name: item.as_dict() for name, item in self.tools.items()},
            "truth": self.truth.as_dict(),
            "version": self.version,
        }

    def model_visible(self) -> dict:
        return {
            "query": self.query,
            "collected_at": self.collected_at,
            "window_start": self.window_start,
        }


@dataclass
class DimensionScore:
    name: str
    passed: Optional[bool]
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class CaseScore:
    case_id: str
    arm: str
    repeat: int
    object_correct: DimensionScore
    facts: DimensionScore
    coverage: DimensionScore
    unsupported: DimensionScore
    recovery: DimensionScore
    advice: DimensionScore
    severe: List[str]
    phrase_hit: Optional[bool] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    duration_s: float = 0.0
    unmatched_tools: List[str] = field(default_factory=list)
    replay_gap: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "arm": self.arm,
            "repeat": self.repeat,
            "object_correct": self.object_correct.as_dict(),
            "facts": self.facts.as_dict(),
            "coverage": self.coverage.as_dict(),
            "unsupported": self.unsupported.as_dict(),
            "recovery": self.recovery.as_dict(),
            "advice": self.advice.as_dict(),
            "severe": list(self.severe),
            "phrase_hit": self.phrase_hit,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "duration_s": self.duration_s,
            "unmatched_tools": list(self.unmatched_tools),
            "replay_gap": self.replay_gap,
            "error": self.error,
        }
