"""Runtime incident scope from the user query and the ledger.

Do not read eval scenario answers. If the query does not name a workload,
do not invent one or a precise time window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .ledger import Observation, ObservationLedger

IN_SCOPE = "in_scope"
HISTORICAL = "historical"
RELATED = "related_context"


@dataclass
class IncidentScope:
    query: str
    targets: List[str] = field(default_factory=list)
    source: str = "query"
    time_range: str = "unspecified"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "targets": list(self.targets),
            "source": self.source,
            "time_range": self.time_range,
        }


def known_names(ledger: "ObservationLedger") -> List[str]:
    names: List[str] = []
    seen: set[str] = set()
    for obs in ledger.objects:
        for raw in (
            obs.component,
            obs.name,
            _replica_set_prefix(obs.name),
            obs.owner_name,
        ):
            key = (raw or "").strip().lower()
            if not key or key in {"n/a", "unknown", "-", "pod"}:
                continue
            if key in seen:
                continue
            seen.add(key)
            names.append(key)
    return names


def scope_from_query(query: str, names: List[str]) -> IncidentScope:
    text = query or ""
    hits: List[str] = []
    for name in sorted(names, key=len, reverse=True):
        if _mentioned(text, name) and name not in hits:
            hits.append(name)
    return IncidentScope(
        query=text,
        targets=hits,
        source="query",
        time_range="unspecified",
    )


def scope_from_ledger(query: str, ledger: "ObservationLedger") -> IncidentScope:
    return scope_from_query(query, known_names(ledger))


def relation_for(obs: "Observation", scope: IncidentScope) -> str:
    if not scope.targets:
        return IN_SCOPE if obs.focus else RELATED
    if _obs_matches_targets(obs, scope.targets):
        return IN_SCOPE
    if obs.focus:
        return HISTORICAL
    return RELATED


def tagged_rows(objects: List["Observation"], scope: IncidentScope) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for obs in objects:
        row = obs.as_dict()
        row["relation"] = relation_for(obs, scope)
        rows.append(row)
    return rows


def _obs_matches_targets(obs: "Observation", targets: List[str]) -> bool:
    fields = {
        (obs.component or "").lower(),
        (obs.name or "").lower(),
        _replica_set_prefix(obs.name or "").lower(),
        (obs.owner_name or "").lower(),
    }
    fields.discard("")
    fields.discard("n/a")
    for target in targets:
        if target.lower() in fields:
            return True
    return False


def _mentioned(query: str, name: str) -> bool:
    if not query or not name:
        return False
    return re.search(
        rf"(?<![a-z0-9-]){re.escape(name)}(?![a-z0-9-])",
        query,
        re.I,
    ) is not None


def _replica_set_prefix(pod_name: str) -> str:
    if "-" not in pod_name:
        return pod_name
    return pod_name.rsplit("-", 1)[0]
