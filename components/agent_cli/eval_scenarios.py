"""Labeled RCA scenarios. The catalog is the ground truth; the scorer reads it.

Only three scenarios on purpose:
  healthy-baseline  — no fault. Catches the agent inventing an incident.
  pod-kill-frontend — the existing demo fault.
  pod-kill-checkout — same fault class, different service (not hardcoded).

NetworkChaos / CPU stress are not here yet: current MCP tools barely see those
signals, so a labeled score would be noise. Add them when the tools exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CHAOS_DIR = _REPO_ROOT / "infra" / "chaos"


@dataclass(frozen=True)
class Scenario:
    id: str
    query: str
    # All of these phrases must appear in the answer body (case-insensitive).
    must_contain: List[str] = field(default_factory=list)
    # At least one of these must appear. Empty = skip this check.
    must_contain_any: List[str] = field(default_factory=list)
    # Any of these in the answer body = miss (false story).
    must_not_contain: List[str] = field(default_factory=list)
    # Tools we expect on a competent run. Missing ones are reported, not a miss.
    expected_tools: List[str] = field(default_factory=list)
    # Relative to infra/chaos/. None = do not inject anything.
    manifest: Optional[str] = None
    # Inject this first, wait, delete it, then apply `manifest`.
    pre_manifest: Optional[str] = None
    pre_wait_seconds: int = 0
    wait_seconds: int = 0
    notes: str = ""
    # If these phrases appear together with a kill verb, flag stale-event mix.
    # Does not flip root_cause_hit by itself.
    stale_if: List[str] = field(default_factory=list)

    def manifest_path(self) -> Optional[Path]:
        if not self.manifest:
            return None
        return _CHAOS_DIR / self.manifest

    def pre_manifest_path(self) -> Optional[Path]:
        if not self.pre_manifest:
            return None
        return _CHAOS_DIR / self.pre_manifest


SCENARIOS: List[Scenario] = [
    Scenario(
        id="healthy-baseline",
        query=(
            "Is anything unhealthy in the citrus namespace right now? "
            "If there is an incident, give a short RCA and validate_recovery. "
            "If not, say the cluster looks healthy and which pods you checked."
        ),
        must_contain_any=["healthy", "running", "ready", "no incident", "no issue"],
        must_not_contain=["chaos mesh", "podchaos", "outage"],
        expected_tools=["list_pods"],
        wait_seconds=0,
        notes="False-positive trap. Run this BEFORE any kill scenarios.",
    ),
    Scenario(
        id="pod-kill-frontend",
        query=(
            "What just happened to the frontend pods in citrus? "
            "Short RCA, then validate_recovery."
        ),
        must_contain=["frontend"],
        must_contain_any=["kill", "killed", "terminat", "deleted", "chaos"],
        expected_tools=["get_recent_events", "validate_recovery"],
        manifest="pod-kill-frontend.yaml",
        wait_seconds=25,
        notes="ReplicaSet should self-heal. Agent is read-only.",
    ),
    Scenario(
        id="pod-kill-checkout",
        query=(
            "What just happened to the checkout pods in citrus? "
            "Short RCA, then validate_recovery."
        ),
        must_contain=["checkout"],
        must_contain_any=["kill", "killed", "terminat", "deleted", "chaos"],
        expected_tools=["get_recent_events", "validate_recovery"],
        manifest="pod-kill-checkout.yaml",
        wait_seconds=25,
        notes="Same fault class as frontend. Checks the agent named the right service.",
    ),
]


# Not in the default catalog. Measures the OBJECT-column iteration:
# leftover frontend kill events sit next to a fresh checkout kill.
STALE_TRAP = Scenario(
    id="checkout-after-frontend",
    query=(
        "What just happened to the checkout pods in citrus? "
        "Short RCA, then validate_recovery."
    ),
    must_contain=["checkout"],
    must_contain_any=["kill", "killed", "terminat", "deleted", "chaos"],
    expected_tools=["get_recent_events", "validate_recovery"],
    pre_manifest="pod-kill-frontend.yaml",
    pre_wait_seconds=20,
    manifest="pod-kill-checkout.yaml",
    wait_seconds=25,
    stale_if=["frontend"],
    notes=(
        "Inject frontend kill, drop the CR, then checkout kill. "
        "Hit = named checkout. stale_mix = treated leftover frontend as this incident."
    ),
)


def scenario_by_id(scenario_id: str) -> Scenario:
    for scenario in [*SCENARIOS, STALE_TRAP]:
        if scenario.id == scenario_id:
            return scenario
    known = ", ".join(s.id for s in [*SCENARIOS, STALE_TRAP])
    raise KeyError(f"unknown scenario {scenario_id!r}. known: {known}")
