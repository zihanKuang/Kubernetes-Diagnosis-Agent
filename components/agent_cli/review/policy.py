"""Deterministic review policy. Overrides LLM labels; evidence cannot change the rules."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from ..diagnosis.models import CLAIM_CAUSAL, CLAIM_RECOVERY, Claim
from ..gated_writes import GATED_TOOLS
from ..observations.scope import HISTORICAL
from .models import (
    ACCEPTED,
    CONTRADICTED,
    FollowUpRequest,
    INSUFFICIENT,
    PARTIAL,
    READ_TOOLS,
    REJECTED,
    SUPPORTED,
    ClaimJudgment,
    ReviewInput,
)


_OOM = re.compile(r"\boomkilled\b|\boom\b|exit\s*=?\s*137", re.I)
_CHAOS = re.compile(r"chaos\s*mesh|chaosmesh|chaosengine|\bchaos\b", re.I)
_KILLING = re.compile(r"\bkilling\b|\bkilled\b", re.I)
_LASTING = re.compile(
    r"request-path|user requests|business|sustained|lasting|fully recovered",
    re.I,
)
_NOT_READY_STATES = frozenset({
    "crashloopbackoff",
    "imagepullbackoff",
    "errimagepull",
    "unschedulable",
    "runningnotready",
    "oomkilled",
})


def apply_policy(
    judgments: List[ClaimJudgment],
    payload: ReviewInput,
) -> List[ClaimJudgment]:
    by_id = {c.claim_id: c for c in payload.draft.claims}
    out: List[ClaimJudgment] = []
    for judgment in judgments:
        claim = by_id.get(judgment.claim_id)
        if claim is None:
            out.append(judgment)
            continue
        out.append(_overlay(judgment, claim, payload))
    return out


def decide_verdict(judgments: List[ClaimJudgment], claims: List[Claim]) -> str:
    if not judgments:
        return PARTIAL
    by_id = {j.claim_id: j for j in judgments}
    published = [c for c in claims if c.citation_status == "valid" or not c.citation_status]
    if any(by_id.get(c.claim_id) and by_id[c.claim_id].status == CONTRADICTED for c in published):
        return REJECTED
    causal = [c for c in published if c.claim_type == CLAIM_CAUSAL]
    if any(by_id.get(c.claim_id) and by_id[c.claim_id].status == CONTRADICTED for c in causal):
        return REJECTED
    if causal and all(by_id.get(c.claim_id) and by_id[c.claim_id].status == SUPPORTED for c in causal):
        if all(by_id.get(c.claim_id) and by_id[c.claim_id].status == SUPPORTED for c in published):
            return ACCEPTED
    return PARTIAL


def sanitize_follow_ups(raw: List[Any], claim_ids: set[str]) -> List[FollowUpRequest]:
    out: List[FollowUpRequest] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if tool in GATED_TOOLS or tool not in READ_TOOLS:
            continue
        claim_id = str(item.get("claim_id") or "").strip()
        question = str(item.get("question") or item.get("missing") or "").strip()
        target = str(item.get("target") or "").strip()
        if not claim_id or not question or not target:
            continue
        if claim_id not in claim_ids:
            continue
        out.append(FollowUpRequest(
            claim_id=claim_id,
            tool=tool,
            target=target,
            question=question,
        ))
    return out


def _overlay(judgment: ClaimJudgment, claim: Claim, payload: ReviewInput) -> ClaimJudgment:
    if judgment.status != SUPPORTED:
        return judgment
    cited = _cited_blob(judgment, payload)
    if claim.claim_type == CLAIM_CAUSAL and not _in_scope_for_targets(claim, payload):
        return _demote(
            judgment,
            CONTRADICTED,
            "claim object is outside the current incident scope",
        )
    if claim.claim_type == CLAIM_CAUSAL and not _object_in_cited_text(claim, cited, payload):
        return _demote(
            judgment,
            INSUFFICIENT,
            "cited evidence does not mention the claim object",
        )
    if claim.claim_type == CLAIM_CAUSAL and _OOM.search(claim.statement or ""):
        if not re.search(r"oomkilled", cited, re.I):
            return _demote(
                judgment,
                INSUFFICIENT,
                "exit 137 / OOM wording without OOMKilled in evidence",
            )
    if claim.claim_type == CLAIM_CAUSAL and _CHAOS.search(claim.statement or ""):
        if not _CHAOS.search(cited):
            return _demote(
                judgment,
                INSUFFICIENT,
                "Killing/termination is not evidence that Chaos Mesh triggered it",
            )
    if claim.claim_type == CLAIM_RECOVERY:
        if _LASTING.search(claim.statement or "") and not _LASTING.search(cited):
            return _demote(
                judgment,
                INSUFFICIENT,
                "Ready at check time does not prove request-path recovery",
            )
        if _unaddressed_not_ready(payload):
            return _demote(
                judgment,
                INSUFFICIENT,
                "in-scope NotReady state was not addressed",
            )
    return judgment


def _demote(judgment: ClaimJudgment, status: str, reason: str) -> ClaimJudgment:
    judgment.llm_status = judgment.llm_status or judgment.status
    judgment.status = status
    judgment.policy_reason = reason
    return judgment


def _cited_blob(judgment: ClaimJudgment, payload: ReviewInput) -> str:
    parts = [payload.evidence_text(eid) for eid in judgment.evidence_ids]
    for obs_id in judgment.obs_ids:
        for row in payload.observations:
            if str(row.get("id") or "").strip().lower() == obs_id.strip().lower():
                parts.append(" ".join(str(v) for v in row.values()))
    return "\n".join(parts)


def _in_scope_for_targets(claim: Claim, payload: ReviewInput) -> bool:
    targets = [t.lower() for t in (payload.scope or {}).get("targets") or [] if t]
    if not targets:
        return True
    components: set[str] = set()
    for obs_id in claim.obs_ids:
        key = obs_id.strip().lower()
        for row in payload.observations:
            if str(row.get("id") or "").strip().lower() != key:
                continue
            component = str(row.get("component") or "").strip().lower()
            if component:
                components.add(component)
    if not components:
        ref = (claim.object_ref or "").strip().lower()
        for row in payload.observations:
            object_ref = str(row.get("object_ref") or "").strip().lower()
            name = str(row.get("name") or "").strip().lower()
            if ref and (object_ref == ref or (name and ref.endswith("/" + name))):
                component = str(row.get("component") or "").strip().lower()
                if component:
                    components.add(component)
    if components:
        return any(t in components for t in targets)
    blob = f"{claim.object_ref} {claim.statement}".lower()
    return any(re.search(rf"(?<![a-z0-9-]){re.escape(t)}(?![a-z0-9-])", blob) for t in targets)


def _object_in_cited_text(claim: Claim, cited: str, payload: ReviewInput) -> bool:
    blob = (cited or "").lower()
    if not blob:
        return False
    ref = (claim.object_ref or "").strip()
    if ref and ref.lower() in blob:
        return True
    if "/" in ref:
        name = ref.split("/", 1)[1].lower()
        if name and name in blob:
            return True
    for obs_id in claim.obs_ids:
        for row in payload.observations:
            if str(row.get("id") or "").strip().lower() != obs_id.strip().lower():
                continue
            for field in ("name", "component", "object_ref"):
                value = str(row.get(field) or "").strip().lower()
                if value and value in blob:
                    return True
    return False


def _unaddressed_not_ready(payload: ReviewInput) -> bool:
    for row in payload.observations:
        relation = str(row.get("relation") or "").strip().lower()
        if relation == HISTORICAL:
            continue
        ready = row.get("ready")
        state = str(row.get("state") or "").strip().lower()
        if ready is False:
            return True
        if state in _NOT_READY_STATES:
            return True
    return False
