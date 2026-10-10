"""Labeled RCA scenarios. The catalog is the ground truth; the scorer reads it.

Fault classes the tools can actually name:
  healthy-baseline     — no fault. Catches the agent inventing an incident.
  pod-kill-*           — Chaos Mesh PodChaos. ReplicaSet replaces the pod.
  crashloop-workload   — process exits 1. Stuck until the fixture is deleted.
  imagepull-workload   — image never starts.
  oomkilled-workload   — memory limit kill. Waiting reason may be CrashLoopBackOff.
  probe-not-ready      — process stays up, readiness fails.
  pod-unschedulable    — Pending, never placed on a node.

frontend and checkout kills are the same class on purpose (not hardcoded to one
service). The fixtures under infra/faults/ are different classes.

Network delay and CPU stress are still absent: query_prometheus can see them
only with an unpinned PromQL guess, so a phrase score would be noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INFRA_DIR = _REPO_ROOT / "infra"
_CHAOS_DIR = _INFRA_DIR / "chaos"


def _resolve_manifest(name: Optional[str]) -> Optional[Path]:
    """Bare names live in infra/chaos/. Paths with a directory live under infra/."""
    if not name:
        return None
    path = Path(name)
    if path.is_absolute():
        return path
    if len(path.parts) > 1:
        return _INFRA_DIR / path
    return _CHAOS_DIR / path


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
        return _resolve_manifest(self.manifest)

    def pre_manifest_path(self) -> Optional[Path]:
        return _resolve_manifest(self.pre_manifest)


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
    Scenario(
        id="crashloop-workload",
        query=(
            "Why is the citrus-crashloop workload in the citrus namespace unhealthy? "
            "Short RCA from live pod state and events, then validate_recovery."
        ),
        must_contain=["citrus-crashloop"],
        # "crashloop" is inside the workload name, so it does not count alone.
        must_contain_any=["crashloopbackoff", "crash loop", "backoff", "back-off"],
        expected_tools=["get_pod_status", "get_recent_events", "validate_recovery"],
        manifest="faults/crashloop.yaml",
        wait_seconds=35,
        notes="Process exits 1. Needs a pull of busybox. Does not need Chaos Mesh. validate_recovery should FAIL.",
    ),
    Scenario(
        id="imagepull-workload",
        query=(
            "Why is the citrus-imagepull workload in citrus not starting? "
            "Short RCA, then validate_recovery."
        ),
        must_contain=["citrus-imagepull"],
        # "imagepull" is inside the workload name, so it does not count alone.
        must_contain_any=["imagepullbackoff", "errimagepull", "image pull", "failed to pull", "pulling image"],
        expected_tools=["get_pod_status", "get_recent_events"],
        manifest="faults/imagepull.yaml",
        wait_seconds=25,
        notes="Bad image reference. Does not need Chaos Mesh. Pod stays Pending.",
    ),
    Scenario(
        id="oomkilled-workload",
        query=(
            "What is wrong with the citrus-oom workload in citrus? "
            "Short RCA, then validate_recovery."
        ),
        must_contain=["citrus-oom"],
        must_contain_any=["oomkill", "out of memory", "exit code 137", "exit 137"],
        expected_tools=["get_pod_status", "get_recent_events", "validate_recovery"],
        manifest="faults/oomkilled.yaml",
        wait_seconds=40,
        notes="Memory-limit kill. Waiting reason is often CrashLoopBackOff; the hit requires OOM.",
    ),
    Scenario(
        id="probe-not-ready",
        query=(
            "Why is the citrus-probe workload in citrus not Ready? "
            "Short RCA, then validate_recovery."
        ),
        must_contain=["citrus-probe"],
        # Bare "probe" is inside the workload name, so it does not count alone.
        must_contain_any=["readiness", "liveness", "probe fail", "probe failed", "failing probe"],
        expected_tools=["get_pod_status", "get_recent_events", "validate_recovery"],
        manifest="faults/probe-not-ready.yaml",
        wait_seconds=20,
        notes="Container stays Running with zero restarts. Readiness exec probe exits 1.",
    ),
    Scenario(
        id="pod-unschedulable",
        query=(
            "Why is the citrus-unschedulable workload in citrus not running? "
            "Short RCA, then validate_recovery."
        ),
        must_contain=["citrus-unschedulable"],
        # "unschedulable" is inside the workload name, so it does not count alone.
        must_contain_any=[
            "failedscheduling",
            "node selector",
            "nodeselector",
            "didn't match",
            "did not match",
            "nodes are available",
            "not schedul",
            "cannot be schedul",
            "unable to schedul",
        ],
        expected_tools=["get_pod_status", "get_recent_events"],
        manifest="faults/unschedulable.yaml",
        wait_seconds=15,
        notes="nodeSelector matches nothing. Phase stays Pending. No image pull.",
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
