"""Fingerprints for cases, arms, and shared conditions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..prompts import DEFAULT_SRE_SYSTEM_INSTRUCTION, LEGACY_SRE_SYSTEM_INSTRUCTION
from ..skills.fingerprint import fingerprint_json, fingerprint_text
from .arms import all_arms
from .catalog import all_cases
from .conditions import SHARED, WIN_RULES

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_FREEZE_PATH = PACKAGE_DIR / "FINGERPRINTS.json"


def freeze_payload() -> Dict[str, Any]:
    cases = {}
    for item in all_cases():
        body = item.as_dict()
        cases[item.case_id] = {
            "split": item.split,
            "kind": item.kind,
            "version": item.version,
            "fingerprint": fingerprint_json(body),
            "query_fingerprint": fingerprint_text(item.query),
            "truth_fingerprint": fingerprint_json(item.truth.as_dict()),
        }
    arms = {arm_id: spec.fingerprints() for arm_id, spec in all_arms().items()}
    return {
        "shared": SHARED,
        "win_rules": WIN_RULES,
        "default_sre_fp": fingerprint_text(DEFAULT_SRE_SYSTEM_INSTRUCTION),
        "ablation_legacy_fp": fingerprint_text(LEGACY_SRE_SYSTEM_INSTRUCTION),
        "a_is_not_ablation": fingerprint_text(DEFAULT_SRE_SYSTEM_INSTRUCTION)
        != fingerprint_text(LEGACY_SRE_SYSTEM_INSTRUCTION),
        "cases": cases,
        "arms": arms,
        "bundle": fingerprint_json({"cases": cases, "arms": arms, "shared": SHARED}),
    }


def write_freeze(path: Path | None = None) -> Path:
    dest = path or DEFAULT_FREEZE_PATH
    dest.write_text(json.dumps(freeze_payload(), indent=2) + "\n", encoding="utf-8")
    return dest


def load_freeze(path: Path | None = None) -> Dict[str, Any]:
    dest = path or DEFAULT_FREEZE_PATH
    return json.loads(dest.read_text(encoding="utf-8"))
