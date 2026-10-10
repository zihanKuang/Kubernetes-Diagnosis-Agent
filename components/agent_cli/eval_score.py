"""Hard phrase scorer. No LLM judge.

phrase_hit means: the answer body names the labeled service / failure mode.
It is not RCA accuracy. root_cause_hit is a compatibility alias of phrase_hit
for old eval JSON. Evidence level is a process stamp, not semantic review.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .eval_scenarios import Scenario
from .evidence import assess, split_answer


SCORING_RULE = "phrase_v1"


def _haystack(body: str) -> str:
    return body.lower()


def _missing(phrases: List[str], hay: str) -> List[str]:
    return [p for p in phrases if p.lower() not in hay]


def _hits(phrases: List[str], hay: str) -> List[str]:
    return [p for p in phrases if p.lower() in hay]


@dataclass
class Score:
    scenario_id: str
    phrase_hit: bool = False
    root_cause_hit: bool = False  # compatibility alias of phrase_hit
    scoring_rule: str = SCORING_RULE
    missing_required: List[str] = field(default_factory=list)
    missing_any: List[str] = field(default_factory=list)
    forbidden_hits: List[str] = field(default_factory=list)
    evidence_level: str = ""
    evidence_reasons: List[str] = field(default_factory=list)
    diagnosis_status: str = "not_reviewed"
    recovery_status: str = "unknown"
    expected_tools_missing: List[str] = field(default_factory=list)
    duration_s: Optional[float] = None
    steps: int = 0
    tool_calls: int = 0
    tools_used: Dict[str, int] = field(default_factory=dict)
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    answer_body: str = ""
    stale_mix: bool = False
    stale_hits: List[str] = field(default_factory=list)
    claims: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["root_cause_hit_is_legacy"] = True
        return payload


_KILL = ("kill", "killed", "killing", "terminat", "deleted", "chaos")
_PRIOR = ("earlier", "previous", "prior", "history", "stale", "already recovered", "note:")


def _stale_mix(scenario: Scenario, hay: str) -> List[str]:
    if not scenario.stale_if:
        return []
    if not any(k in hay for k in _KILL):
        return []
    hits = []
    for marker in scenario.stale_if:
        if marker.lower() not in hay:
            continue
        # Mentioning leftover as labeled history is allowed.
        if any(p in hay for p in _PRIOR):
            continue
        hits.append(marker)
    return hits


def score(scenario: Scenario, answer: str, stats: Dict[str, Any]) -> Score:
    body, _footer = split_answer(answer)
    hay = _haystack(body)

    missing_required = _missing(scenario.must_contain, hay)
    any_hits = _hits(scenario.must_contain_any, hay)
    missing_any = [] if (not scenario.must_contain_any or any_hits) else list(scenario.must_contain_any)
    forbidden_hits = _hits(scenario.must_not_contain, hay)
    stale_hits = _stale_mix(scenario, hay)

    hit = not missing_required and not missing_any and not forbidden_hits

    check = assess(scenario.query, answer, stats)
    tools_used = dict(stats.get("tool_calls") or {})
    expected_missing = [t for t in scenario.expected_tools if tools_used.get(t, 0) == 0]

    start = stats.get("start_time")
    end = stats.get("end_time")
    duration = None
    if start is not None and end is not None:
        duration = round(float(end) - float(start), 2)

    return Score(
        scenario_id=scenario.id,
        phrase_hit=hit,
        root_cause_hit=hit,
        scoring_rule=SCORING_RULE,
        missing_required=missing_required,
        missing_any=missing_any,
        forbidden_hits=forbidden_hits,
        evidence_level=check.level,
        evidence_reasons=list(check.reasons),
        diagnosis_status=check.diagnosis_status,
        recovery_status=check.recovery_status,
        expected_tools_missing=expected_missing,
        duration_s=duration,
        steps=int(stats.get("total_steps") or 0),
        tool_calls=sum(tools_used.values()),
        tools_used=tools_used,
        errors=int(stats.get("errors") or 0),
        prompt_tokens=int(stats.get("prompt_tokens") or 0),
        completion_tokens=int(stats.get("completion_tokens") or 0),
        llm_calls=int(stats.get("llm_calls") or 0),
        answer_body=body,
        stale_mix=bool(stale_hits),
        stale_hits=stale_hits,
        claims=[c.as_dict() for c in check.claims],
    )
