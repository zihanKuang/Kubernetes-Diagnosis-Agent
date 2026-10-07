"""Run-scoped evidence records. No LLM. IDs never leak across runs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_VERSION = 1

CALL_SUCCESS = "success"
CALL_ERROR = "error"
CALL_TIMEOUT = "timeout"
CALL_DENIED = "denied"

CONTENT_TEXT = "text"
CONTENT_STRUCTURED = "structured"
CONTENT_DENIAL = "denial"


@dataclass
class ToolPayload:
    """Transport result of one MCP call. Domain PASS/FAIL is not call_status."""
    text: str
    call_status: str = CALL_SUCCESS
    is_error: bool = False
    structured: Any = None
    source_truncated: bool = False


@dataclass
class ViewerSlice:
    evidence_id: str
    viewer: str  # generator | reviewer
    ranges: List[Tuple[int, int]]
    truncated_for_viewer: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "viewer": self.viewer,
            "ranges": [list(r) for r in self.ranges],
            "truncated_for_viewer": self.truncated_for_viewer,
        }


@dataclass
class EvidenceItem:
    evidence_id: str
    call_id: str
    collected_at: str
    content_type: str
    locator: str
    text: str
    source_truncated: bool = False
    object_key: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolCallRecord:
    call_id: str
    tool_name: str
    requested_arguments: Dict[str, Any]
    bound_arguments: Dict[str, Any]
    started_at: str
    ended_at: str
    call_status: str
    evidence_id: str = ""
    domain_result: Optional[str] = None
    error_message: str = ""
    is_error: bool = False
    structured: Any = None
    source_truncated: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunRecord:
    schema_version: int
    run_id: str
    started_at: str
    query: str
    config: Dict[str, Any]
    ended_at: str = ""
    query_scope: str = "namespace:citrus"
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    evidence: List[EvidenceItem] = field(default_factory=list)
    latest_by_object: Dict[str, str] = field(default_factory=dict)
    generator_seen: List[ViewerSlice] = field(default_factory=list)
    reviewer_seen: List[ViewerSlice] = field(default_factory=list)
    draft: str = ""
    diagnosis: Any = None
    review: Any = None
    final: str = ""
    check: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "query": self.query,
            "query_scope": self.query_scope,
            "config": self.config,
            "tool_calls": [c.as_dict() for c in self.tool_calls],
            "evidence": [e.as_dict() for e in self.evidence],
            "latest_by_object": dict(self.latest_by_object),
            "generator_seen": [s.as_dict() for s in self.generator_seen],
            "reviewer_seen": [s.as_dict() for s in self.reviewer_seen],
            "draft": self.draft,
            "diagnosis": self.diagnosis,
            "review": self.review,
            "final": self.final,
            "check": self.check,
            "stats": self.stats,
            "skill": (self.stats or {}).get("skill") or {},
        }

    def evidence_by_id(self, evidence_id: str) -> Optional[EvidenceItem]:
        for item in self.evidence:
            if item.evidence_id == evidence_id:
                return item
        return None
