"""Structured diagnosis draft. No LLM. Fields are independently reviewable."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


CLAIM_OBSERVATION = "observation"
CLAIM_SYMPTOM = "symptom"
CLAIM_CAUSAL = "causal_claim"
CLAIM_RECOVERY = "recovery_claim"
CLAIM_TYPES = frozenset({
    CLAIM_OBSERVATION,
    CLAIM_SYMPTOM,
    CLAIM_CAUSAL,
    CLAIM_RECOVERY,
})

CONFIDENCE_VALUES = frozenset({"high", "medium", "low", "unknown"})

DRAFT_OK = "ok"
DRAFT_INVALID = "draft_invalid"
DRAFT_CITATION_INVALID = "citation_invalid"
DRAFT_OPERATIONAL = "operational"

CITE_VALID = "valid"
CITE_UNKNOWN = "unknown_id"
CITE_CROSS_RUN = "cross_run"
CITE_SUMMARY = "summary_only"
CITE_RANGE = "invalid_locator"
CITE_MISSING = "missing"


@dataclass
class Claim:
    claim_id: str
    statement: str
    claim_type: str
    object_ref: str = ""
    window: str = "current"
    obs_ids: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    confidence: str = "unknown"
    citation_status: str = ""
    citation_reason: str = ""
    repairs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.claim_id,
            "statement": self.statement,
            "type": self.claim_type,
            "object": self.object_ref,
            "window": self.window,
            "obs": list(self.obs_ids),
            "evidence": list(self.evidence_ids),
            "confidence": self.confidence,
            "citation_status": self.citation_status,
            "citation_reason": self.citation_reason,
            "repairs": list(self.repairs),
        }


@dataclass
class DiagnosisDraft:
    status: str
    scope: str = ""
    unknowns: List[str] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    repairs: List[str] = field(default_factory=list)
    raw_text: str = ""

    def facts(self) -> List[Claim]:
        return [c for c in self.claims if c.claim_type == CLAIM_OBSERVATION]

    def symptoms(self) -> List[Claim]:
        return [c for c in self.claims if c.claim_type == CLAIM_SYMPTOM]

    def causal_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.claim_type == CLAIM_CAUSAL]

    def recovery_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.claim_type == CLAIM_RECOVERY]

    def published_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.citation_status == CITE_VALID]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "scope": self.scope,
            "unknowns": list(self.unknowns),
            "claims": [c.as_dict() for c in self.claims],
            "errors": list(self.errors),
            "repairs": list(self.repairs),
        }
