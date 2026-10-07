"""Parse and validate a diagnosis draft. No LLM. No eval answers."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .models import (
    CITE_CROSS_RUN,
    CITE_MISSING,
    CITE_RANGE,
    CITE_SUMMARY,
    CITE_UNKNOWN,
    CITE_VALID,
    CLAIM_CAUSAL,
    CLAIM_OBSERVATION,
    CLAIM_RECOVERY,
    CLAIM_SYMPTOM,
    CLAIM_TYPES,
    CONFIDENCE_VALUES,
    DRAFT_CITATION_INVALID,
    DRAFT_INVALID,
    DRAFT_OK,
    DRAFT_OPERATIONAL,
    Claim,
    DiagnosisDraft,
)
from ..stamps import iso_utc


_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_CITE = re.compile(
    r"^(?:(?P<run>[0-9a-f]{8,32}):)?(?P<kind>ev|obs)-(?P<n>\d+)"
    r"(?:#(?P<a>\d+)-(?P<b>\d+))?$",
    re.I,
)
_SUMMARY_IDS = frozenset({
    "summary", "draft", "self", "answer", "model", "generator", "prose",
})
_OPERATIONAL_PREFIXES = (
    "Agent exceeded",
    "Agent encountered",
    "Agent stopped after",
)
_BUCKETS = {
    "restart": re.compile(r"\brestarts?\b|\brestarted\b", re.I),
    "oom": re.compile(r"\boomkilled\b|\boom\b|exit\s*=?\s*137", re.I),
    "recovery": re.compile(
        r"\brecovered\b|\brecovery\b|ready at|validate_recovery\s*=?\s*pass",
        re.I,
    ),
}
_SYMPTOM_ONLY = re.compile(
    r"crashloopbackoff|imagepullbackoff|errimagepull|unschedulable|"
    r"runningnotready|pending",
    re.I,
)
_CAUSE_MARKER = re.compile(
    r"oomkilled|exit\s*=?\s*\d+|last[_\s-]?termination|probe|unhealthy",
    re.I,
)
OpenLocator = Callable[[str, int, Optional[int]], str]


def operational_failure(text: str) -> bool:
    head = (text or "").lstrip()
    return any(head.startswith(p) for p in _OPERATIONAL_PREFIXES)


def publish_diagnosis(
    raw_text: str,
    *,
    observations: Optional[List[Dict[str, Any]]] = None,
    evidence_ids: Optional[Iterable[str]] = None,
    run_id: str = "",
    open_locator: Optional[OpenLocator] = None,
) -> Tuple[DiagnosisDraft, str]:
    """Validate a model draft and render the published body. Does not review RCA."""
    from .renderer import render_diagnosis

    draft = parse_diagnosis(
        raw_text,
        observations=observations,
        evidence_ids=evidence_ids,
        run_id=run_id,
        open_locator=open_locator,
    )
    return draft, render_diagnosis(draft)


def parse_diagnosis(
    raw_text: str,
    *,
    observations: Optional[List[Dict[str, Any]]] = None,
    evidence_ids: Optional[Iterable[str]] = None,
    run_id: str = "",
    open_locator: Optional[OpenLocator] = None,
) -> DiagnosisDraft:
    text = raw_text or ""
    if operational_failure(text):
        return DiagnosisDraft(
            status=DRAFT_OPERATIONAL,
            errors=["operational failure; not a diagnosis draft"],
            raw_text=text,
        )
    payload, repairs = _load_json(text)
    if payload is None:
        return DiagnosisDraft(
            status=DRAFT_INVALID,
            errors=["not JSON; unreviewed free text was not published"],
            repairs=repairs,
            raw_text=text,
        )
    if not isinstance(payload, dict):
        return DiagnosisDraft(
            status=DRAFT_INVALID,
            errors=["diagnosis JSON must be an object"],
            repairs=repairs,
            raw_text=text,
        )
    draft = _draft_from_payload(payload, repairs)
    draft.raw_text = text
    if draft.status == DRAFT_INVALID:
        return draft
    _bind_citations(
        draft,
        observations=list(observations or []),
        evidence_ids=set(evidence_ids or []),
        run_id=run_id or "",
        open_locator=open_locator,
    )
    if any(c.citation_status != CITE_VALID for c in draft.claims):
        draft.status = DRAFT_CITATION_INVALID
    else:
        draft.status = DRAFT_OK
    return draft


def _load_json(text: str) -> Tuple[Optional[Any], List[str]]:
    repairs: List[str] = []
    blob = _extract_object(text)
    if blob is None:
        return None, repairs
    if blob != text.strip():
        repairs.append("extracted JSON object")
    parsed = _try_json(blob)
    if parsed is not None:
        return parsed, repairs
    repaired = _TRAILING_COMMA.sub(r"\1", blob)
    if repaired != blob:
        repairs.append("removed trailing commas")
        parsed = _try_json(repaired)
        if parsed is not None:
            return parsed, repairs
    return None, repairs + ["JSON parse failed"]


def _extract_object(text: str) -> Optional[str]:
    stripped = (text or "").strip()
    if not stripped:
        return None
    fence = _FENCE.search(stripped)
    if fence:
        return fence.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start:end + 1]
    return None


def _try_json(blob: str) -> Optional[Any]:
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def _draft_from_payload(payload: Dict[str, Any], repairs: List[str]) -> DiagnosisDraft:
    errors: List[str] = []
    if "claims" not in payload:
        repairs.append("missing claims defaulted to []")
        payload = dict(payload)
        payload["claims"] = []
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        return DiagnosisDraft(
            status=DRAFT_INVALID,
            errors=["claims must be a list"],
            repairs=repairs,
        )
    unknowns = _as_str_list(payload.get("unknowns"))
    scope = str(payload.get("scope") or "").strip()
    claims: List[Claim] = []
    seen_ids: Set[str] = set()
    for index, raw in enumerate(raw_claims, start=1):
        if not isinstance(raw, dict):
            errors.append(f"claim {index} is not an object")
            continue
        claim, claim_errors, claim_repairs = _claim_from_payload(raw, index)
        repairs.extend(claim_repairs)
        if claim_errors:
            errors.extend(claim_errors)
            continue
        if claim.claim_id in seen_ids:
            errors.append(f"duplicate claim id {claim.claim_id}")
            continue
        seen_ids.add(claim.claim_id)
        claims.append(claim)
    claims.sort(key=lambda c: c.claim_id)
    if errors:
        return DiagnosisDraft(
            status=DRAFT_INVALID,
            scope=scope,
            unknowns=unknowns,
            claims=claims,
            errors=errors,
            repairs=repairs,
        )
    return DiagnosisDraft(
        status=DRAFT_OK,
        scope=scope,
        unknowns=unknowns,
        claims=claims,
        repairs=repairs,
    )


def _claim_from_payload(
    raw: Dict[str, Any],
    index: int,
) -> Tuple[Claim, List[str], List[str]]:
    errors: List[str] = []
    repairs: List[str] = []
    claim_id = str(raw.get("id") or raw.get("claim_id") or "").strip()
    if not claim_id:
        claim_id = f"c{index}"
        repairs.append(f"claim {index} missing id → {claim_id}")
    statement = str(raw.get("statement") or raw.get("text") or raw.get("claim") or "").strip()
    if not statement:
        errors.append(f"{claim_id}: missing statement")
    claim_type = str(raw.get("type") or raw.get("claim_type") or "").strip().lower()
    if claim_type not in CLAIM_TYPES:
        errors.append(f"{claim_id}: type must be one of {sorted(CLAIM_TYPES)}")
    confidence = str(raw.get("confidence") or "").strip().lower()
    if not confidence:
        confidence = "unknown"
        repairs.append(f"{claim_id} missing confidence → unknown")
    elif confidence not in CONFIDENCE_VALUES:
        errors.append(f"{claim_id}: confidence must be one of {sorted(CONFIDENCE_VALUES)}")
    object_ref = str(
        raw.get("object") or raw.get("object_ref") or raw.get("target") or ""
    ).strip()
    window = str(raw.get("window") or raw.get("time_range") or "").strip()
    if not window:
        window = "current"
        repairs.append(f"{claim_id} missing window → current")
    else:
        window = iso_utc(window) or window
    obs_ids = _normalize_ids(_as_str_list(raw.get("obs") or raw.get("obs_ids") or raw.get("from_obs")))
    evidence_ids = _normalize_ids(
        _as_str_list(raw.get("evidence") or raw.get("evidence_ids") or raw.get("citations"))
    )
    if statement and _compound_buckets(statement) >= 2:
        errors.append(
            f"{claim_id}: compound statement; split restart, OOM, and recovery "
            "into separate claims"
        )
    if claim_type == CLAIM_CAUSAL and statement and _waiting_state_only(statement):
        claim_type = CLAIM_SYMPTOM
        repairs.append(f"{claim_id} causal_claim → symptom (waiting state is not an exit cause)")
    claim = Claim(
        claim_id=claim_id,
        statement=statement,
        claim_type=claim_type or CLAIM_OBSERVATION,
        object_ref=object_ref,
        window=window or "current",
        obs_ids=obs_ids,
        evidence_ids=evidence_ids,
        confidence=confidence or "unknown",
        repairs=repairs,
    )
    return claim, errors, repairs


def _compound_buckets(statement: str) -> int:
    return sum(1 for pattern in _BUCKETS.values() if pattern.search(statement or ""))


def _waiting_state_only(statement: str) -> bool:
    text = statement or ""
    return bool(_SYMPTOM_ONLY.search(text)) and not _CAUSE_MARKER.search(text)


def _bind_citations(
    draft: DiagnosisDraft,
    *,
    observations: List[Dict[str, Any]],
    evidence_ids: Set[str],
    run_id: str,
    open_locator: Optional[OpenLocator],
) -> None:
    obs_by_id = {
        str(row.get("id") or "").strip().lower(): row
        for row in observations
        if str(row.get("id") or "").strip()
    }
    allowed_ev = {e.lower() for e in evidence_ids}
    run_key = (run_id or "").lower()
    for claim in draft.claims:
        status, reason = _citation_status(
            claim,
            obs_by_id=obs_by_id,
            allowed_ev=allowed_ev,
            run_id=run_key,
            open_locator=open_locator,
        )
        claim.citation_status = status
        claim.citation_reason = reason
        _enrich_from_ledger(claim, obs_by_id)


def _citation_status(
    claim: Claim,
    *,
    obs_by_id: Dict[str, Dict[str, Any]],
    allowed_ev: Set[str],
    run_id: str,
    open_locator: Optional[OpenLocator],
) -> Tuple[str, str]:
    refs = list(claim.obs_ids) + list(claim.evidence_ids)
    if not refs:
        return CITE_MISSING, "claim cites no obs-N or ev-N from this run"
    for raw in refs:
        token = (raw or "").strip()
        if token.lower() in _SUMMARY_IDS:
            return CITE_SUMMARY, "citation is the generator summary, not evidence"
        match = _CITE.match(token)
        if match is None:
            if token.lower() in _SUMMARY_IDS or token.lower().startswith("summary"):
                return CITE_SUMMARY, "citation is the generator summary, not evidence"
            return CITE_UNKNOWN, f"unknown citation {token}"
        foreign = (match.group("run") or "").lower()
        if foreign and run_id and foreign not in {run_id, run_id[:8], run_id[:12]}:
            return CITE_CROSS_RUN, f"citation {token} is from another run"
        kind = match.group("kind").lower()
        ident = f"{kind}-{match.group('n')}".lower()
        start = match.group("a")
        end = match.group("b")
        if kind == "obs":
            if ident not in obs_by_id:
                return CITE_UNKNOWN, f"unknown observation {ident}"
        else:
            if ident not in allowed_ev:
                return CITE_UNKNOWN, f"unknown evidence {ident}"
            if start is not None and end is not None:
                if open_locator is None:
                    return CITE_RANGE, f"locator {token} cannot be opened"
                try:
                    open_locator(ident, int(start), int(end))
                except (KeyError, TypeError, ValueError):
                    return CITE_RANGE, f"invalid locator {token}"
    return CITE_VALID, "citations are in this run"


def _enrich_from_ledger(claim: Claim, obs_by_id: Dict[str, Dict[str, Any]]) -> None:
    for obs_id in claim.obs_ids:
        key = obs_id.strip().lower()
        match = _CITE.match(obs_id.strip())
        if match and match.group("kind").lower() == "obs":
            key = f"obs-{match.group('n')}".lower()
        row = obs_by_id.get(key)
        if not row:
            continue
        ref = str(row.get("object_ref") or "").strip()
        if not ref:
            kind = str(row.get("kind") or "").strip()
            name = str(row.get("name") or "").strip()
            if kind and name:
                ref = f"{kind}/{name}"
        if ref:
            claim.object_ref = ref
        window = str(row.get("window") or "").strip()
        if window:
            claim.window = iso_utc(window) or window
        return


def _as_str_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_ids(values: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in values:
        key = raw.strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append(key)
    return out
