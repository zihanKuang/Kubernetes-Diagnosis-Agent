"""skill_bench CLI. Only flags that exist here are documented as available."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import all_cases, by_id, dev_ids, holdout_ids
from .conditions import SHARED
from .freeze import write_freeze
from .jobs import build_jobs
from .preflight import preflight
from .report import QUALITY_UNPROVEN, load_jobs, render_text, summarize
from .runner import LIVE_BLOCKED, BudgetExceeded, run_jobs_sync

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "data" / "skill-bench"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent_cli.skill_bench",
        description="Offline A/B/C Skills bench. Replay only. Default is fake-model.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Print the 12 frozen cases")
    sub.add_parser("preflight", help="Check dataset, arms, and live blockers")
    sub.add_parser("freeze", help="Write package FINGERPRINTS.json")

    plan = sub.add_parser("plan", help="Write case×arm×repeat job list")
    plan.add_argument("--arms", default="A,B,C")
    plan.add_argument("--cases", default="all")
    plan.add_argument("--repeats", type=int, default=int(SHARED["repeats_planned"]))
    plan.add_argument("--seed", type=int, default=20260315)
    plan.add_argument("--out", default=str(DEFAULT_OUT))
    plan.add_argument("--split", choices=["all", "dev", "holdout"], default="all")

    run = sub.add_parser("run", help="Execute jobs (fake-model unless live flags are set)")
    run.add_argument("--arms", default="A,B,C")
    run.add_argument("--cases", default="all")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--seed", type=int, default=20260315)
    run.add_argument("--out", default=str(DEFAULT_OUT))
    run.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    run.add_argument("--no-resume", action="store_true")
    run.add_argument("--fake-model", action="store_true", default=True)
    run.add_argument(
        "--live",
        action="store_true",
        help="Request real model calls. Refused unless --i-accept-model-charges.",
    )
    run.add_argument("--i-accept-model-charges", action="store_true")
    run.add_argument("--budget-diagnoses", type=int, default=None)

    report = sub.add_parser("report", help="Summarize a result directory")
    report.add_argument("--out", default=str(DEFAULT_OUT))

    args = parser.parse_args(argv)
    if args.cmd == "list":
        for item in all_cases():
            print(f"{item.case_id}\t{item.split}\t{item.kind}\tv{item.version}")
        return 0
    if args.cmd == "preflight":
        payload = preflight()
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok_offline"] else 1
    if args.cmd == "freeze":
        path = write_freeze()
        print(path)
        return 0
    if args.cmd == "plan":
        jobs = _jobs_from_args(args)
        dest = Path(args.out)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "plan.json"
        path.write_text(json.dumps([job.as_dict() for job in jobs], indent=2) + "\n", encoding="utf-8")
        print(f"{len(jobs)} jobs -> {path}")
        return 0
    if args.cmd == "run":
        if args.live:
            if not args.i_accept_model_charges:
                print(LIVE_BLOCKED, file=sys.stderr)
                return 2
            if args.budget_diagnoses is None:
                print("Live run requires --budget-diagnoses N", file=sys.stderr)
                return 2
        fake = not args.live
        jobs = _jobs_from_args(args)
        dest = Path(args.out)
        dest.mkdir(parents=True, exist_ok=True)
        if args.live:
            from ..config import AgentConfig
            probe = AgentConfig(api_key=None)
            if not probe.api_key:
                print(
                    "Live run needs CITRUS_LLM_API_KEY or DEEPSEEK_API_KEY. No key printed.",
                    file=sys.stderr,
                )
                return 2
            meta = {
                "live": True,
                "budget_diagnoses": args.budget_diagnoses,
                "jobs": len(jobs),
                "model_name": SHARED["model_name"],
                "llm_provider": SHARED["llm_provider"],
                "max_steps": SHARED["max_steps"],
                "key_present": True,
                "api_key": None,
            }
            (dest / "run_meta.json").write_text(
                json.dumps(meta, indent=2) + "\n", encoding="utf-8"
            )
        try:
            rows = run_jobs_sync(
                jobs,
                out_dir=dest,
                fake_model=fake,
                resume=not args.no_resume,
                budget_diagnoses=args.budget_diagnoses,
            )
        except BudgetExceeded as exc:
            print(str(exc), file=sys.stderr)
            return 3
        summary = summarize(rows)
        (dest / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(render_text(summary))
        if fake:
            print(QUALITY_UNPROVEN)
        return 0
    if args.cmd == "report":
        dest = Path(args.out)
        summary = summarize(load_jobs(dest))
        print(render_text(summary))
        return 0
    return 2


def _jobs_from_args(args) -> list:
    wanted = [item.strip() for item in args.arms.split(",") if item.strip()]
    catalog = by_id()
    if args.cases == "all":
        case_ids = list(catalog)
    else:
        case_ids = [item.strip() for item in args.cases.split(",") if item.strip()]
    if args.split == "dev":
        allow = set(dev_ids())
        case_ids = [item for item in case_ids if item in allow]
    elif args.split == "holdout":
        allow = set(holdout_ids())
        case_ids = [item for item in case_ids if item in allow]
    splits = {item.case_id: item.split for item in all_cases()}
    return build_jobs(
        case_ids=case_ids,
        arms=wanted,
        repeats=args.repeats,
        splits=splits,
        seed=args.seed,
        interleave=True,
    )
