"""Summaries. No composite score. Fake-model runs are not quality results."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

QUALITY_UNPROVEN = (
    "对照实验系统已准备并完成离线验证，尚不能判断 Skills 是否改善真实模型诊断表现。"
)

DIMENSIONS = [
    "object_correct",
    "facts",
    "coverage",
    "unsupported",
    "recovery",
    "advice",
]


def load_jobs(out_dir: Path) -> List[dict]:
    root = out_dir / "jobs"
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.glob("*.json")):
        rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def summarize(rows: List[dict]) -> Dict[str, Any]:
    live = [row for row in rows if row.get("quality_valid") and not row.get("fake_model")]
    fake = [row for row in rows if row.get("fake_model")]
    by_case: Dict[str, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in live:
        by_case[row["case_id"]][row["arm"]].append(row)
    pairs = {"A_vs_B": [], "B_vs_C": []}
    for case_id, arms in sorted(by_case.items()):
        pairs["A_vs_B"].append(_pair(case_id, arms.get("A") or [], arms.get("B") or []))
        pairs["B_vs_C"].append(_pair(case_id, arms.get("B") or [], arms.get("C") or []))
    severe = []
    for row in live:
        score = row.get("score") or {}
        for item in score.get("severe") or []:
            severe.append({
                "job_id": row.get("job_id"),
                "case_id": row.get("case_id"),
                "arm": row.get("arm"),
                "kind": item,
            })
    return {
        "quality_valid_runs": len(live),
        "fake_model_runs": len(fake),
        "infra_failed": sum(1 for row in rows if row.get("status") == "infra_failed"),
        "per_case": _per_case(live),
        "pairs": pairs,
        "severe": severe,
        "cost": _cost(live or fake),
        "conclusion": QUALITY_UNPROVEN if not live else "see per-case and pairs; no default-mode change",
    }


def render_text(summary: Dict[str, Any]) -> str:
    lines = [
        "# Skill bench report",
        "",
        f"Quality-valid diagnoses: {summary['quality_valid_runs']}",
        f"Fake-model diagnoses (not quality): {summary['fake_model_runs']}",
        f"Infrastructure failures: {summary['infra_failed']}",
        "",
        summary["conclusion"],
        "",
        "Dimensions are reported separately. There is no composite score.",
        "Arm C is the Citrus-adapted external Skill, not upstream as-is.",
        "A vs B is the whole change (routing, stages, tools, text).",
        "B vs C is investigation text only.",
        "",
    ]
    if summary["per_case"]:
        lines.append("## Per case")
        for case_id, arms in summary["per_case"].items():
            lines.append(f"### {case_id}")
            for arm, stats in arms.items():
                lines.append(
                    f"- {arm}: n={stats['n']} severe={stats['severe_runs']} "
                    f"tokens={stats['prompt_tokens']}+{stats['completion_tokens']} "
                    f"llm_calls={stats['llm_calls']} duration_s={stats['duration_s']:.1f}"
                )
        lines.append("")
    if summary["severe"]:
        lines.append("## Severe failures")
        for item in summary["severe"]:
            lines.append(
                f"- {item['arm']} {item['case_id']} {item['kind']} ({item['job_id']})"
            )
        lines.append("")
    return "\n".join(lines)


def _per_case(rows: List[dict]) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["case_id"]][row["arm"]].append(row)
    out: Dict[str, Any] = {}
    for case_id, arms in grouped.items():
        out[case_id] = {}
        for arm, items in arms.items():
            scores = [item.get("score") or {} for item in items]
            out[case_id][arm] = {
                "n": len(items),
                "severe_runs": sum(1 for score in scores if score.get("severe")),
                "prompt_tokens": sum(int(score.get("prompt_tokens") or 0) for score in scores),
                "completion_tokens": sum(int(score.get("completion_tokens") or 0) for score in scores),
                "llm_calls": sum(int(score.get("llm_calls") or 0) for score in scores),
                "duration_s": sum(float(score.get("duration_s") or 0) for score in scores),
                "dimensions": {
                    name: sum(1 for score in scores if (score.get(name) or {}).get("passed"))
                    for name in DIMENSIONS
                },
            }
    return out


def _pair(case_id: str, left: List[dict], right: List[dict]) -> Dict[str, Any]:
    def quality(rows: List[dict]) -> tuple:
        severe = sum(len((row.get("score") or {}).get("severe") or []) for row in rows)
        passed = 0
        for row in rows:
            score = row.get("score") or {}
            for name in DIMENSIONS:
                if name == "advice":
                    continue
                if (score.get(name) or {}).get("passed"):
                    passed += 1
        return (-severe, passed)

    if not left or not right:
        return {"case_id": case_id, "result": "incomplete", "reason": "missing arm"}
    lv, rv = quality(left), quality(right)
    if lv > rv:
        result = "left"
    elif rv > lv:
        result = "right"
    else:
        result = "tie"
    return {
        "case_id": case_id,
        "result": result,
        "left_n": len(left),
        "right_n": len(right),
        "left_quality": list(lv),
        "right_quality": list(rv),
    }


def _cost(rows: List[dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for arm in ("A", "B", "C"):
        items = [row.get("score") or {} for row in rows if row.get("arm") == arm]
        if not items:
            continue
        out[arm] = {
            "n": len(items),
            "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in items),
            "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in items),
            "llm_calls": sum(int(item.get("llm_calls") or 0) for item in items),
            "tool_calls": sum(int(item.get("tool_calls") or 0) for item in items),
            "duration_s": sum(float(item.get("duration_s") or 0) for item in items),
        }
    return out
