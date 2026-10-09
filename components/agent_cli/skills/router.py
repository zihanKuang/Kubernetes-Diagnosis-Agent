"""Deterministic skill routing from request metadata and this-run observations."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from .conditions import eval_all, eval_condition
from .errors import SkillError
from .models import (
    ROUTE_FALLBACK,
    ROUTE_OVERRIDE,
    ROUTE_POST_SCAN,
    ROUTE_PRE_SCAN,
    ROUTE_REROUTE,
    ObservationSnapshot,
    RequestContext,
    RouteDecision,
    SCAN_FAILED,
    SCAN_NOT_SCANNED,
    SCAN_SUCCESS,
    SCAN_EMPTY,
)
from .registry import RegisteredSkill, SkillRegistry

GENERIC_ID = "generic-triage"
HEALTHY_ID = "healthy"
MAX_SWITCHES = 1

FALLBACK_GENERIC = "no matching skill; using generic-triage"
FALLBACK_NO_SCAN = "no valid scan; cannot conclude healthy"
FALLBACK_UNKNOWN = "observations not covered by a specific skill"


def route_pre_scan(
    request: RequestContext,
    registry: SkillRegistry,
    *,
    catalog_names: Sequence[str] = (),
) -> RouteDecision:
    if request.skill_override:
        return override_decision(request, registry, catalog_names=catalog_names)
    candidates = _candidates(request, registry, catalog_names=catalog_names, require_obs=False)
    generic = _require_generic(registry)
    ranked = _rank(candidates) or [generic]
    chosen = ranked[0]
    return RouteDecision(
        skill_id=GENERIC_ID if chosen.meta.id != HEALTHY_ID else GENERIC_ID,
        skill_version=_require_generic(registry).meta.version,
        workflow_id=_require_generic(registry).workflow.id,
        stage=ROUTE_PRE_SCAN,
        candidates=[row.meta.id for row in ranked],
        matched_rules=["pre_scan:basic-triage"],
        fallback_reason="scan required before a specific inspect skill",
        observation_ids=[],
        object_bindings={},
    )


def route_post_scan(
    request: RequestContext,
    snapshot: ObservationSnapshot,
    registry: SkillRegistry,
    *,
    catalog_names: Sequence[str] = (),
    previous: Optional[RouteDecision] = None,
) -> RouteDecision:
    if request.skill_override:
        decision = override_decision(request, registry, catalog_names=catalog_names)
        decision.observation_ids = list(snapshot.observation_ids)
        decision.stage = ROUTE_OVERRIDE
        return decision

    if snapshot.scan_status in {SCAN_NOT_SCANNED, SCAN_FAILED}:
        generic = _require_generic(registry)
        return RouteDecision(
            skill_id=generic.meta.id,
            skill_version=generic.meta.version,
            workflow_id=generic.workflow.id,
            stage=ROUTE_FALLBACK,
            candidates=[generic.meta.id],
            matched_rules=["scan_status!=success"],
            fallback_reason=FALLBACK_NO_SCAN,
            observation_ids=list(snapshot.observation_ids),
        )

    if snapshot.valid_scan() and not snapshot.focus:
        healthy = _get(registry, HEALTHY_ID)
        return RouteDecision(
            skill_id=healthy.meta.id,
            skill_version=healthy.meta.version,
            workflow_id=healthy.workflow.id,
            stage=ROUTE_POST_SCAN,
            candidates=[healthy.meta.id],
            matched_rules=["valid_scan && focus_count==0"],
            observation_ids=list(snapshot.observation_ids),
        )

    bindings: Dict[str, str] = {}
    matched: List[str] = []
    chosen_rows: List[RegisteredSkill] = []
    for facts in snapshot.focus:
        base = facts.routing_facts(snapshot.scan_status, len(snapshot.focus))
        base["intent"] = request.intent
        base["entrypoint"] = request.entrypoint
        options = []
        for row in _candidates(request, registry, catalog_names=catalog_names, require_obs=True):
            if row.meta.routing.fallback:
                continue
            if row.meta.routing.observations and not _match_obs(row.meta.routing, base):
                continue
            if row.meta.routing.exclude and eval_all(row.meta.routing.exclude, base):
                continue
            options.append(row)
        if not options:
            generic = _require_generic(registry)
            bindings[facts.obs_id] = generic.meta.id
            matched.append(f"{facts.obs_id}->generic-triage")
            chosen_rows.append(generic)
            continue
        best = _rank(options)[0]
        bindings[facts.obs_id] = best.meta.id
        matched.append(f"{facts.obs_id}->{best.meta.id}")
        chosen_rows.append(best)

    unique_ids = list(dict.fromkeys(row.meta.id for row in chosen_rows))
    if len(unique_ids) > 1:
        # Keep per-object bindings. Primary skill is the highest-ranked binding.
        primary = _rank(chosen_rows)[0]
    elif unique_ids:
        primary = chosen_rows[0]
    else:
        primary = _require_generic(registry)
        return RouteDecision(
            skill_id=primary.meta.id,
            skill_version=primary.meta.version,
            workflow_id=primary.workflow.id,
            stage=ROUTE_FALLBACK,
            candidates=[primary.meta.id],
            matched_rules=matched,
            fallback_reason=FALLBACK_UNKNOWN,
            observation_ids=list(snapshot.observation_ids),
            object_bindings=bindings,
        )

    stage = ROUTE_POST_SCAN
    if previous and previous.skill_id != primary.meta.id:
        stage = ROUTE_REROUTE
    return RouteDecision(
        skill_id=primary.meta.id,
        skill_version=primary.meta.version,
        workflow_id=primary.workflow.id,
        stage=stage,
        candidates=unique_ids,
        matched_rules=matched,
        observation_ids=list(snapshot.observation_ids),
        object_bindings=bindings,
    )


def maybe_reroute(
    request: RequestContext,
    snapshot: ObservationSnapshot,
    registry: SkillRegistry,
    *,
    previous: RouteDecision,
    switches: Dict[str, int],
    last_key: str,
    catalog_names: Sequence[str] = (),
) -> RouteDecision:
    key = _obs_key(snapshot)
    if key == last_key:
        return previous
    nxt = route_post_scan(
        request,
        snapshot,
        registry,
        catalog_names=catalog_names,
        previous=previous,
    )
    if nxt.skill_id == previous.skill_id and nxt.object_bindings == previous.object_bindings:
        return previous
    for obs_id, skill_id in nxt.object_bindings.items():
        old = previous.object_bindings.get(obs_id, previous.skill_id)
        if skill_id == old:
            continue
        used = switches.get(obs_id, 0)
        if used >= MAX_SWITCHES:
            nxt.object_bindings[obs_id] = old
            nxt.fallback_reason = f"switch cap reached for {obs_id}"
        else:
            switches[obs_id] = used + 1
    # Recompute primary after cap.
    if nxt.object_bindings:
        counts: Dict[str, int] = {}
        for skill_id in nxt.object_bindings.values():
            counts[skill_id] = counts.get(skill_id, 0) + 1
        nxt.skill_id = sorted(counts, key=lambda sid: (-counts[sid], sid))[0]
        row = registry.get(nxt.skill_id)
        nxt.skill_version = row.meta.version
        nxt.workflow_id = row.workflow.id
    nxt.stage = ROUTE_REROUTE
    return nxt


def override_decision(
    request: RequestContext,
    registry: SkillRegistry,
    *,
    catalog_names: Sequence[str] = (),
) -> RouteDecision:
    skill_id = (request.skill_override or "").strip()
    row = registry.get(skill_id)
    if row.disabled or not row.meta.enabled:
        raise SkillError(f"skill {skill_id!r} is disabled", skill_id=skill_id)
    if request.entrypoint not in row.meta.entrypoints:
        raise SkillError(
            f"skill {skill_id!r} does not support entrypoint {request.entrypoint}",
            skill_id=skill_id,
            field="entrypoints",
        )
    missing = [cap for cap in row.meta.required_capabilities if cap not in set(catalog_names)]
    # required_capabilities are names of tools or capability tags; skip if empty.
    return RouteDecision(
        skill_id=row.meta.id,
        skill_version=row.meta.version,
        workflow_id=row.workflow.id,
        stage=ROUTE_OVERRIDE,
        candidates=[row.meta.id],
        matched_rules=[f"override:{skill_id}"],
        fallback_reason="",
        override=True,
    )


def _candidates(
    request: RequestContext,
    registry: SkillRegistry,
    *,
    catalog_names: Sequence[str],
    require_obs: bool,
) -> List[RegisteredSkill]:
    catalog = set(catalog_names)
    out = []
    for row in registry.list(include_disabled=False):
        if row.meta.id.startswith("fragment:"):
            continue
        if request.entrypoint not in row.meta.entrypoints:
            continue
        intents = row.meta.routing.intents
        if intents and request.intent not in intents and "unknown" not in intents:
            continue
        if row.meta.required_capabilities and catalog:
            needed = set(row.meta.required_capabilities)
            if not needed.issubset(catalog) and not needed.issubset(_caps_from_tools(catalog)):
                continue
        if require_obs and not row.meta.routing.observations and not row.meta.routing.fallback:
            continue
        out.append(row)
    return out


def _caps_from_tools(catalog: Iterable[str]) -> set[str]:
    from .tool_policy import KNOWN_TOOLS
    return {KNOWN_TOOLS[name].capability for name in catalog if name in KNOWN_TOOLS}


def _rank(rows: Sequence[RegisteredSkill]) -> List[RegisteredSkill]:
    return sorted(
        rows,
        key=lambda row: (
            1 if row.meta.routing.fallback else 0,
            -int(row.meta.routing.specificity or 0),
            -int(row.meta.routing.priority or 0),
            _version_key(row.meta.version),
            row.meta.id,
        ),
    )


def _version_key(version: str) -> tuple:
    parts = []
    for bit in version.split("."):
        try:
            parts.append((-int(bit), ""))
        except ValueError:
            parts.append((0, bit))
    return tuple(parts)


def _require_generic(registry: SkillRegistry) -> RegisteredSkill:
    return registry.available(GENERIC_ID)


def _get(registry: SkillRegistry, skill_id: str) -> RegisteredSkill:
    return registry.available(skill_id)


def _match_obs(rule, facts) -> bool:
    if not rule.observations:
        return True
    if rule.match == "any":
        return any(eval_condition(item, facts) for item in rule.observations)
    return eval_all(rule.observations, facts)


def _obs_key(snapshot: ObservationSnapshot) -> str:
    bits = [snapshot.scan_status]
    for item in snapshot.focus:
        bits.append(
            f"{item.obs_id}:{item.state}:{item.last_termination}:{item.waiting_reason}:"
            f"{','.join(item.event_reasons)}"
        )
    return "|".join(bits)
