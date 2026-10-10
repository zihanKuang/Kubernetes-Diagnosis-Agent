"""Reviewer result types. Independent of generator diagnosis models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SUPPORTED = "supported"
CONTRADICTED = "contradicted"
INSUFFICIENT = "insufficient_evidence"
JUDGMENT_STATUSES = frozenset({SUPPORTED, CONTRADICTED, INSUFFICIENT})

ACCEPTED = "accepted"
PARTIAL = "partial"
REJECTED = "rejected"
REVIEW_ERROR = "review_error"
NOT_REVIEWED = "not_reviewed"
VERDICTS = frozenset({ACCEPTED, PARTIAL, REJECTED, REVIEW_ERROR, NOT_REVIEWED})

READ_TOOLS = frozenset({
    "list_pods",
    "get_recent_events",
    "get_pod_status",
    "get_pod_logs",
    "query_prometheus",
    "validate_recovery",
})


@dataclass
class ClaimJudgment:
    claim_id: str
    status: str
    reason: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    obs_ids: List[str] = field(default_factory=list)
    llm_status: str = ""
    policy_reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "status": self.status,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "obs_ids": list(self.obs_ids),
            "llm_status": self.llm_status,
            "policy_reason": self.policy_reason,
        }


@dataclass
class FollowUpRequest:
    claim_id: str
    tool: str
    target: str
    question: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "tool": self.tool,
            "target": self.target,
            "question": self.question,
        }


@dataclass
class Review:
    verdict: str
    judgments: List[ClaimJudgment] = field(default_factory=list)
    follow_ups: List[FollowUpRequest] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    raw: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "judgments": [j.as_dict() for j in self.judgments],
            "follow_ups": [f.as_dict() for f in self.follow_ups],
            "errors": list(self.errors),
        }


@dataclass
class ReviewInput:
    query: str
    scope: Dict[str, Any]
    draft: Any
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    run_id: str = ""

    def evidence_ids(self) -> set[str]:
        return {
            str(item.get("evidence_id") or "").strip()
            for item in self.evidence
            if str(item.get("evidence_id") or "").strip()
        }

    def obs_ids(self) -> set[str]:
        return {
            str(row.get("id") or "").strip()
            for row in self.observations
            if str(row.get("id") or "").strip()
        }

    def evidence_text(self, evidence_id: str) -> str:
        key = (evidence_id or "").strip().lower()
        for item in self.evidence:
            if str(item.get("evidence_id") or "").strip().lower() == key:
                return str(item.get("text") or "")
        return ""
