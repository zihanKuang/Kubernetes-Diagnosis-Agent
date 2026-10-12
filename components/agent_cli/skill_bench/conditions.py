"""Shared run conditions. Arms may differ only on declared experimental factors."""

from __future__ import annotations

from typing import Any, Dict

from .clock import COLLECTED_AT, WINDOW_START

SHARED: Dict[str, Any] = {
    "engine": "react",
    "model_name": "deepseek-v4-flash",
    "llm_provider": "deepseek",
    "max_steps": 10,
    "max_tool_calls": 40,
    "max_retries": 3,
    "llm_timeout_seconds": 120.0,
    "tool_timeout_seconds": 60.0,
    "llm_max_tokens": 8192,
    "max_content_length": 4000,
    "memory_enabled": False,
    "writes_interactive": False,
    "repeats_planned": 5,
    "concurrency": 1,
    "collected_at": COLLECTED_AT,
    "window_start": WINDOW_START,
}

EXPERIMENTAL_FACTORS = {
    "A_vs_B": [
        "skill_routing",
        "stage_tool_allowlist",
        "on_demand_instructions",
        "prompt_assembly",
    ],
    "B_vs_C": [
        "skill_instruction_dir",
        "loaded SKILL.md investigation text",
        "source_label",
    ],
}

WIN_RULES = {
    "composite_score": False,
    "dimensions": [
        "object_correct",
        "facts",
        "coverage",
        "unsupported",
        "recovery",
        "advice",
        "cost_time",
    ],
    "severe_never_offset_by_cost": True,
    "phrase_hit_is_accuracy": False,
}
