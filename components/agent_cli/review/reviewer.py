"""Independent RCA reviewer. Does not execute tools or publish answers."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..config import ReviewerConfig
from ..diagnosis import parse_diagnosis
from ..diagnosis.models import DiagnosisDraft
from .judge import Judge, create_model_judge
from .policy import apply_policy, decide_verdict, sanitize_follow_ups
from .models import (
    INSUFFICIENT,
    JUDGMENT_STATUSES,
    NOT_REVIEWED,
    REVIEW_ERROR,
    ClaimJudgment,
    Review,
    ReviewInput,
    SUPPORTED,
)
from .prompts import REVIEW_SYSTEM


_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_CITE = re.compile(r"^(?:[0-9a-f]{8,32}:)?(?P<kind>ev|obs)-(?P<n>\d+)", re.I)

_HIDDEN_KEYS = frozenset({
    "expected",
    "expected_answer",
    "gold",
    "gold_answer",
    "scenario_id",
    "must_contain",
    "phrase_hit",
    "eval_answer",
})


def build_user_prompt(payload: ReviewInput) -> str:
    evidence_lines = []
    for item in payload.evidence:
        eid = item.get("evidence_id") or ""
        text = str(item.get("text") or "")[:1500]
        evidence_lines.append(f"<evidence id=\"{eid}\">\n{text}\n</evidence>")
    obs_lines = []
    for row in payload.observations:
        obs_lines.append(
            json.dumps(
                {k: v for k, v in row.items() if k not in _HIDDEN_KEYS},
                ensure_ascii=False,
            )
        )
    claims = [c.as_dict() for c in payload.draft.claims]
    scope = {k: v for k, v in (payload.scope or {}).items() if k not in _HIDDEN_KEYS}
    return (
        f"Query: {payload.query}\n"
        f"Incident scope: {json.dumps(scope, ensure_ascii=False)}\n"
        f"Claims: {json.dumps(claims, ensure_ascii=False)}\n"
        f"Observations:\n" + "\n".join(obs_lines) + "\n"
        f"Evidence pack:\n" + "\n".join(evidence_lines) + "\n"
    )


def make_input(
    *,
    query: str,
    scope: Dict[str, Any],
    draft: Any,
    evidence: List[Dict[str, Any]],
    observations: List[Dict[str, Any]],
    run_id: str = "",
) -> ReviewInput:
    parsed = _as_draft(draft, observations, evidence)
    scope = {k: v for k, v in (scope or {}).items() if k not in _HIDDEN_KEYS}
    return ReviewInput(
        query=query,
        scope=scope,
        draft=parsed,
        evidence=evidence,
        observations=observations,
        run_id=run_id,
    )


async def review_diagnosis(
    payload: ReviewInput,
    *,
    judge: Optional[Judge] = None,
    config: Optional[ReviewerConfig] = None,
) -> Review:
    """Single entry for offline eval, ReAct, and LangGraph. Does not publish."""
    if judge is None:
        cfg = config or ReviewerConfig.from_env()
        if not cfg.configured():
            return Review(
                verdict=NOT_REVIEWED,
                errors=["reviewer credentials missing"],
            )
        judge = create_model_judge(cfg)
    user = build_user_prompt(payload)
    try:
        raw = await judge.complete(system=REVIEW_SYSTEM, user=user)
    except Exception as exc:
        return Review(
            verdict=REVIEW_ERROR,
            errors=[f"judge failed: {exc}"],
        )
    parsed, parse_error = _parse_review_json(raw)
    if parse_error:
        return Review(verdict=REVIEW_ERROR, errors=[parse_error], raw=raw or "")
    judgments, struct_errors = _judgments_from_parsed(parsed, payload)
    if struct_errors:
        return Review(
            verdict=REVIEW_ERROR,
            judgments=judgments,
            errors=struct_errors,
            raw=raw or "",
        )
    judged = apply_policy(judgments, payload)
    follow_ups = sanitize_follow_ups(
        parsed.get("follow_ups") or [],
        {c.claim_id for c in payload.draft.claims},
    )
    verdict = decide_verdict(judged, payload.draft.claims)
    return Review(
        verdict=verdict,
        judgments=judged,
        follow_ups=follow_ups,
        raw=raw or "",
    )


def _as_draft(
    draft: Any,
    observations: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> DiagnosisDraft:
    if isinstance(draft, DiagnosisDraft):
        return draft
    if isinstance(draft, dict):
        text = json.dumps(draft)
    else:
        text = str(draft or "")
    return parse_diagnosis(
        text,
        observations=observations,
        evidence_ids=[
            str(item.get("evidence_id") or "")
            for item in evidence
            if item.get("evidence_id")
        ],
    )


def _parse_review_json(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    blob = (text or "").strip()
    if not blob:
        return None, "empty review"
    fence = _FENCE.search(blob)
    if fence:
        blob = fence.group(1)
    else:
        start, end = blob.find("{"), blob.rfind("}")
        if start >= 0 and end > start:
            blob = blob[start:end + 1]
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        repaired = _TRAILING_COMMA.sub(r"\1", blob)
        try:
            payload = json.loads(repaired)
        except json.JSONDecodeError:
            return None, "review is not JSON"
    if not isinstance(payload, dict):
        return None, "review JSON must be an object"
    if "claims" not in payload:
        if _looks_like_overall_score(payload):
            return None, "per-claim status required; overall score is not a review"
        return None, "review JSON missing claims"
    if not isinstance(payload.get("claims"), list):
        return None, "review claims must be a list"
    return payload, ""


def _looks_like_overall_score(payload: Dict[str, Any]) -> bool:
    if "score" in payload or "overall" in payload:
        return True
    values = [v for v in payload.values() if isinstance(v, dict)]
    return bool(values) and all("score" in v for v in values)


def _judgments_from_parsed(
    parsed: Dict[str, Any],
    payload: ReviewInput,
) -> Tuple[List[ClaimJudgment], List[str]]:
    errors: List[str] = []
    judgments: List[ClaimJudgment] = []
    seen: set[str] = set()
    allowed_ev = {eid.lower() for eid in payload.evidence_ids()}
    allowed_obs = {oid.lower() for oid in payload.obs_ids()}
    draft_ids = {c.claim_id for c in payload.draft.claims}
    required = {
        c.claim_id
        for c in payload.draft.claims
        if c.citation_status in {"", "valid"}
    }
    for raw in parsed.get("claims") or []:
        if not isinstance(raw, dict):
            errors.append("judgment is not an object")
            continue
        claim_id = str(raw.get("claim_id") or raw.get("id") or "").strip()
        if not claim_id:
            errors.append("judgment missing claim_id")
            continue
        if claim_id not in draft_ids:
            errors.append(f"unknown claim_id {claim_id}")
            continue
        if claim_id in seen:
            errors.append(f"duplicate claim_id {claim_id}")
            continue
        seen.add(claim_id)
        status = str(raw.get("status") or "").strip().lower()
        if status not in JUDGMENT_STATUSES:
            errors.append(f"{claim_id}: unknown status {status or '(empty)'}")
            continue
        ev_ids = _ids(raw.get("evidence") or raw.get("evidence_ids"))
        obs_ids = _ids(raw.get("obs") or raw.get("obs_ids"))
        for token in ev_ids:
            ident = _bare_id(token)
            if ident not in allowed_ev:
                errors.append(f"{claim_id}: unknown evidence {token}")
        for token in obs_ids:
            ident = _bare_id(token)
            if ident not in allowed_obs:
                errors.append(f"{claim_id}: unknown observation {token}")
        if status == SUPPORTED and not ev_ids and not obs_ids:
            errors.append(f"{claim_id}: supported claim has no evidence")
        judgments.append(
            ClaimJudgment(
                claim_id=claim_id,
                status=status,
                llm_status=status,
                reason=str(raw.get("reason") or "").strip(),
                evidence_ids=ev_ids,
                obs_ids=obs_ids,
            )
        )
    missing = sorted(required - seen)
    if missing:
        errors.append("missing judgments for " + ", ".join(missing))
    if not judgments and not errors:
        errors.append("per-claim status required; overall score is not a review")
    return judgments, errors


def _ids(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _bare_id(token: str) -> str:
    match = _CITE.match((token or "").strip())
    if not match:
        return (token or "").strip().lower()
    return f"{match.group('kind').lower()}-{match.group('n')}"
