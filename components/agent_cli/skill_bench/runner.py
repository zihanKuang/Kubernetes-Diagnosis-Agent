"""Run one bench job through ReActAgent + ReplayToolProvider."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..agent import ReActAgent
from .arms import get_arm
from .catalog import by_id
from .fake_llm import FakeInvestigator
from .jobs import BenchJob
from .replay import ReplayToolProvider
from .scorer import score_run

LIVE_BLOCKED = (
    "Live model runs are blocked until --i-accept-model-charges and a budget "
    "are set. Fake-model scores are not quality results."
)


class BudgetExceeded(RuntimeError):
    pass


async def run_job(
    job: BenchJob,
    *,
    out_dir: Path,
    fake_model: bool = True,
    resume: bool = True,
    model_overrides: Optional[dict] = None,
) -> Dict[str, Any]:
    dest = out_dir / "jobs" / f"{job.job_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if resume and dest.is_file():
        existing = json.loads(dest.read_text(encoding="utf-8"))
        if existing.get("status") in {"scored", "diagnosis_complete"}:
            return existing
    case = by_id()[job.case_id]
    arm = get_arm(job.arm)
    replay = ReplayToolProvider(case)
    trace_dir = out_dir / "traces" / job.job_id
    trace_dir.mkdir(parents=True, exist_ok=True)
    memory_path = str(out_dir / "memory-disabled.jsonl")
    overrides = dict(model_overrides or {})
    config = arm.config(
        trace_dir=str(trace_dir),
        memory_path=memory_path,
        **overrides,
    )
    agent = ReActAgent(config)
    agent.mcp_client = replay
    investigator = None
    if fake_model:
        investigator = FakeInvestigator()
        agent.llm_client = investigator
    started = time.time()
    record: Dict[str, Any] = {
        "job_id": job.job_id,
        "case_id": job.case_id,
        "arm": job.arm,
        "arm_label": arm.label,
        "repeat": job.repeat,
        "split": job.split,
        "fake_model": fake_model,
        "quality_valid": False if fake_model else True,
        "status": "running",
        "query": case.query,
        "model": {
            "name": config.model_name,
            "provider": config.llm_provider,
            "base_url": config.llm_base_url,
            "max_steps": config.max_steps,
            "max_tool_calls": config.max_tool_calls,
            "max_tokens": config.llm_max_tokens,
        },
    }
    print(
        f"skill-bench {job.job_id} arm={job.arm} case={job.case_id} "
        f"repeat={job.repeat} fake={fake_model}",
        flush=True,
    )
    try:
        await agent.initialize()
        answer = await agent.run(case.query, scenario="skill-bench", entrypoint="eval")
        stats = dict(agent.stats)
        stats["skill_outcomes"] = ((stats.get("skill") or {}).get("outcomes")) or []
        score = score_run(
            case,
            arm=job.arm,
            repeat=job.repeat,
            stats=stats,
            answer=answer,
            unmatched=list(replay.unmatched),
        )
        record.update({
            "status": "scored",
            "answer": answer,
            "stats": {
                "tool_calls": stats.get("tool_calls"),
                "tool_ok": stats.get("tool_ok"),
                "tool_denied": stats.get("tool_denied"),
                "tool_error": stats.get("tool_error"),
                "prompt_tokens": stats.get("prompt_tokens"),
                "completion_tokens": stats.get("completion_tokens"),
                "llm_calls": stats.get("llm_calls"),
                "total_steps": stats.get("total_steps"),
                "skill": stats.get("skill"),
                "recovery": stats.get("recovery"),
                "diagnosis": stats.get("diagnosis"),
                "start_time": stats.get("start_time"),
                "end_time": stats.get("end_time"),
            },
            "score": score.as_dict(),
            "replay": {
                "calls": replay.calls,
                "unmatched": replay.unmatched,
                "write_attempts": replay.write_attempts,
            },
            "seen_tools": investigator.seen_tools if investigator else None,
            "instruction_present": [
                bool(item) for item in (investigator.seen_instructions if investigator else [])
            ],
            "wall_s": time.time() - started,
            "quality_valid": (not fake_model),
        })
    except Exception as exc:
        record.update({
            "status": "infra_failed",
            "quality_valid": False,
            "error": f"{type(exc).__name__}: {exc}",
            "wall_s": time.time() - started,
            "replay": {
                "calls": replay.calls,
                "unmatched": replay.unmatched,
                "write_attempts": replay.write_attempts,
            },
        })
    finally:
        try:
            await agent.cleanup()
        except Exception:
            pass
    dest.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return record


async def run_jobs(
    jobs: Iterable[BenchJob],
    *,
    out_dir: Path,
    fake_model: bool = True,
    resume: bool = True,
    budget_diagnoses: Optional[int] = None,
    model_overrides: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    results = []
    used = 0
    for job in jobs:
        if budget_diagnoses is not None and used >= budget_diagnoses:
            raise BudgetExceeded(
                f"stopped after {used} diagnoses (budget {budget_diagnoses})"
            )
        results.append(await run_job(
            job,
            out_dir=out_dir,
            fake_model=fake_model,
            resume=resume,
            model_overrides=model_overrides,
        ))
        used += 1
    return results


def run_jobs_sync(*args, **kwargs) -> List[Dict[str, Any]]:
    return asyncio.run(run_jobs(*args, **kwargs))
