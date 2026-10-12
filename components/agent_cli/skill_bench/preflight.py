"""Block a live quality run when comparison conditions are not frozen."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..prompts import DEFAULT_SRE_SYSTEM_INSTRUCTION, LEGACY_SRE_SYSTEM_INSTRUCTION
from .arms import ADAPTED_DIR, all_arms
from .candidate import CANDIDATE
from .catalog import all_cases, dev_ids, holdout_ids
from .conditions import SHARED
from .freeze import freeze_payload

BLOCKERS_LIVE = [
    "github_license_field is null and LICENSE is missing; C is a Citrus-authored adaptation, not a redistributed upstream file",
]


def preflight() -> Dict[str, Any]:
    cases = all_cases()
    issues: List[str] = []
    if len(cases) != 12:
        issues.append(f"expected 12 cases, got {len(cases)}")
    if len(dev_ids()) != 8:
        issues.append(f"expected 8 dev cases, got {len(dev_ids())}")
    if len(holdout_ids()) != 4:
        issues.append(f"expected 4 holdout cases, got {len(holdout_ids())}")
    if DEFAULT_SRE_SYSTEM_INSTRUCTION.strip() == LEGACY_SRE_SYSTEM_INSTRUCTION.strip():
        issues.append("arm A collapsed onto the ablation prompt")
    if not ADAPTED_DIR.is_dir():
        issues.append(f"missing adapted instruction dir {ADAPTED_DIR}")
    md = list(ADAPTED_DIR.glob("*/SKILL.md"))
    if len(md) < 8:
        issues.append(f"expected adapted SKILL.md files, found {len(md)}")
    leaks = [item.case_id for item in cases if "citrus-oom" in item.query or item.case_id == "citrus-oom"]
    if leaks:
        issues.append(f"answer-leaking names in queries: {leaks}")
    kinds = {item.kind for item in cases}
    if kinds - {"synthetic", "captured"}:
        issues.append(f"unexpected kind values {kinds}")
    freeze = freeze_payload()
    if not freeze.get("a_is_not_ablation"):
        issues.append("frozen A matches ablation prompt")
    live_ready = not issues and not BLOCKERS_LIVE
    return {
        "ok_offline": not issues,
        "ok_live_quality": False,
        "issues": issues,
        "live_blockers": BLOCKERS_LIVE,
        "reason_live_blocked": (
            "No paid-model authorization in this session; also "
            + "; ".join(BLOCKERS_LIVE)
        ),
        "cases": [item.case_id for item in cases],
        "dev": dev_ids(),
        "holdout": holdout_ids(),
        "shared": SHARED,
        "candidate": {
            "commit": CANDIDATE["commit"],
            "redistribute_original": CANDIDATE["redistribute_original"],
            "label": CANDIDATE["label"],
        },
        "arms": {arm: spec.fingerprints() for arm, spec in all_arms().items()},
        "live_ready": live_ready,
    }
