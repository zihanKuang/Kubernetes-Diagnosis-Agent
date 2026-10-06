"""Run labeled RCA scenarios against a live cluster, then score with hard rules.

  python -m agent_cli.eval_rca --list
  python -m agent_cli.eval_rca --dry-run
  python -m agent_cli.eval_rca --scenario healthy-baseline
  python -m agent_cli.eval_rca --all

Needs: cluster + citrus namespace + Chaos Mesh (for kill scenarios) + DEEPSEEK_API_KEY.
Does not need Chaos Mesh for healthy-baseline.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from .agent import ReActAgent
from .config import AgentConfig
from .eval_scenarios import SCENARIOS, STALE_TRAP, Scenario, scenario_by_id
from .eval_score import Score, score
from .memory import annotate_latest_hit


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OUT = _REPO_ROOT / "data" / "eval"


def _kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _apply_manifest(path: Path, wait_seconds: int, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"chaos manifest missing: {path}")
    delete = _kubectl("delete", "-f", str(path), "--ignore-not-found")
    if delete.returncode != 0:
        raise RuntimeError(delete.stderr or delete.stdout)
    time.sleep(2)
    apply = _kubectl("apply", "-f", str(path))
    if apply.returncode != 0:
        raise RuntimeError(apply.stderr or apply.stdout)
    print(f"  injected {label} ({path.name}), waiting {wait_seconds}s...")
    time.sleep(wait_seconds)


def _inject(scenario: Scenario) -> None:
    pre = scenario.pre_manifest_path()
    if pre is not None:
        _apply_manifest(pre, scenario.pre_wait_seconds, "pre")
        _kubectl("delete", "-f", str(pre), "--ignore-not-found")
        time.sleep(3)
    path = scenario.manifest_path()
    if path is None:
        return
    _inject_wait = scenario.wait_seconds
    _apply_manifest(path, _inject_wait, "main")


def _cleanup(scenario: Scenario) -> None:
    path = scenario.manifest_path()
    if path is not None:
        _kubectl("delete", "-f", str(path), "--ignore-not-found")
    pre = scenario.pre_manifest_path()
    if pre is not None:
        _kubectl("delete", "-f", str(pre), "--ignore-not-found")


def _print_score(result: Score) -> None:
    mark = "HIT " if result.root_cause_hit else "MISS"
    print(f"  {mark}  evidence={result.evidence_level}  "
          f"{result.duration_s}s  steps={result.steps}  tools={result.tool_calls}")
    if result.missing_required:
        print(f"    missing required: {result.missing_required}")
    if result.missing_any:
        print(f"    needed one of: {result.missing_any}")
    if result.forbidden_hits:
        print(f"    forbidden phrases: {result.forbidden_hits}")
    if result.stale_mix:
        print(f"    stale_mix: {result.stale_hits}")
    if result.expected_tools_missing:
        print(f"    expected tools not called: {result.expected_tools_missing}")


def _print_table(results: List[Score]) -> None:
    hits = sum(1 for r in results if r.root_cause_hit)
    print("\n" + "=" * 72)
    print(f"{'scenario':<24} {'hit':<6} {'evidence':<8} {'sec':<8} {'tools'}")
    print("-" * 72)
    for r in results:
        print(f"{r.scenario_id:<24} {str(r.root_cause_hit):<6} "
              f"{r.evidence_level:<8} {str(r.duration_s):<8} {r.tool_calls}")
    print("-" * 72)
    print(f"root-cause hit rate: {hits}/{len(results)}")
    print("=" * 72)


def _save(out_dir: Path, results: List[Score], extra: dict | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"rca_eval_{stamp}.json"
    payload = {
        "created_utc": stamp,
        "hit_rate": (
            sum(1 for r in results if r.root_cause_hit) / len(results)
            if results else 0.0
        ),
        "results": [r.as_dict() for r in results],
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {path}")
    return path


def compare_runs(baseline: dict, current: dict) -> dict:
    """Side-by-side of two eval JSON files. Honest deltas, no spin."""
    by_b = {r["scenario_id"]: r for r in baseline.get("results") or []}
    by_c = {r["scenario_id"]: r for r in current.get("results") or []}
    rows = []
    for sid in by_b:
        b = by_b[sid]
        c = by_c.get(sid) or {}
        dur_b = b.get("duration_s")
        dur_c = c.get("duration_s")
        tools_b = b.get("tool_calls")
        tools_c = c.get("tool_calls")
        delta_dur = None
        if isinstance(dur_b, (int, float)) and isinstance(dur_c, (int, float)):
            delta_dur = round(float(dur_c) - float(dur_b), 2)
        delta_tools = None
        if isinstance(tools_b, (int, float)) and isinstance(tools_c, (int, float)):
            delta_tools = int(tools_c) - int(tools_b)
        rows.append({
            "scenario_id": sid,
            "hit_baseline": b.get("root_cause_hit"),
            "hit_current": c.get("root_cause_hit"),
            "evidence_baseline": b.get("evidence_level"),
            "evidence_current": c.get("evidence_level"),
            "duration_s_baseline": dur_b,
            "duration_s_current": dur_c,
            "delta_duration_s": delta_dur,
            "tool_calls_baseline": tools_b,
            "tool_calls_current": tools_c,
            "delta_tool_calls": delta_tools,
        })
    hits_b = sum(1 for r in by_b.values() if r.get("root_cause_hit"))
    hits_c = sum(1 for sid in by_b if (by_c.get(sid) or {}).get("root_cause_hit"))
    n = len(by_b)
    fewer_tools = sum(
        1 for r in rows
        if isinstance(r["delta_tool_calls"], int) and r["delta_tool_calls"] < 0
    )
    verdict = (
        "no lift on n=3"
        if hits_c <= hits_b and fewer_tools == 0
        else "hit rate or tool-call count moved — see rows"
    )
    if n != 3:
        verdict = f"compared {n} overlapping scenarios — {verdict}"
    return {
        "hit_rate_baseline": baseline.get("hit_rate"),
        "hit_rate_current": current.get("hit_rate"),
        "hits_baseline": f"{hits_b}/{n}",
        "hits_current": f"{hits_c}/{n}",
        "scenarios_with_fewer_tools": fewer_tools,
        "verdict": verdict,
        "rows": rows,
    }


def _print_compare(cmp: dict) -> None:
    print("\n" + "=" * 72)
    print("eval compare (baseline -> current)")
    print("-" * 72)
    print(f"hit rate: {cmp['hits_baseline']} -> {cmp['hits_current']}")
    print(f"{'scenario':<24} {'hit':<12} {'ev':<12} {'sec d':<10} {'tools d'}")
    print("-" * 72)
    for r in cmp["rows"]:
        hit = f"{r['hit_baseline']}->{r['hit_current']}"
        ev = f"{r['evidence_baseline']}->{r['evidence_current']}"
        print(f"{r['scenario_id']:<24} {hit:<12} {ev:<12} "
              f"{str(r['delta_duration_s']):<10} {r['delta_tool_calls']}")
    print("-" * 72)
    print(f"verdict: {cmp['verdict']}")
    print("=" * 72)


async def _run_scenarios(
    scenarios: Iterable[Scenario],
    *,
    skip_inject: bool,
    no_cleanup: bool,
    out_dir: Path,
    memory_enabled: bool,
    memory_path: Path | None,
) -> List[Score]:
    config = AgentConfig(
        memory_enabled=memory_enabled,
        memory_path=str(memory_path) if memory_path else None,
        writes_interactive=False,
    )
    if not config.api_key:
        print("Error: DEEPSEEK_API_KEY is not set")
        sys.exit(1)

    agent = ReActAgent(config)
    await agent.initialize()
    results: List[Score] = []
    try:
        for scenario in scenarios:
            print(f"\n=== {scenario.id} ===")
            print(f"  {scenario.notes}")
            try:
                if not skip_inject:
                    _inject(scenario)
                answer = await agent.run(scenario.query, scenario=scenario.id)
                result = score(scenario, answer, agent.stats)
                if memory_enabled:
                    annotate_latest_hit(
                        scenario.id,
                        result.root_cause_hit,
                        path=memory_path,
                    )
                results.append(result)
                _print_score(result)
            except Exception as exc:
                print(f"  ERROR: {exc}")
                results.append(Score(
                    scenario_id=scenario.id,
                    root_cause_hit=False,
                    answer_body=f"ERROR: {exc}",
                    evidence_level="LOW",
                    evidence_reasons=[str(exc)],
                ))
            finally:
                if not skip_inject and not no_cleanup:
                    _cleanup(scenario)
    finally:
        await agent.cleanup()

    _print_table(results)
    _save(out_dir, results, extra={
        "memory_enabled": memory_enabled,
        "memory_path": str(memory_path) if memory_path else None,
        "ablation": os.getenv("CITRUS_ABLATION") or "current",
    })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Labeled RCA eval (hard-rule scorer)")
    parser.add_argument("--list", action="store_true", help="Print the catalog and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show inject plan, do not call the LLM")
    parser.add_argument("--scenario", help="Run one scenario id")
    parser.add_argument("--all", action="store_true", help="Run every scenario in catalog order")
    parser.add_argument("--skip-inject", action="store_true", help="Do not apply/delete Chaos Mesh")
    parser.add_argument("--no-cleanup", action="store_true", help="Leave the Chaos CR in the cluster")
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    parser.add_argument(
        "--stale-trap",
        action="store_true",
        help="Run checkout-after-frontend (leftover frontend events + fresh checkout kill)",
    )
    parser.add_argument(
        "--ablation",
        choices=["current", "legacy"],
        default="current",
        help="current = OBJECT column + correlation prompt; legacy = strip both",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Disable postmortem read/write (baseline runs)",
    )
    parser.add_argument(
        "--memory-path",
        type=Path,
        default=None,
        help="JSONL path for postmortems (default: data/memory/postmortems.jsonl)",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASELINE_JSON", "CURRENT_JSON"),
        help="Diff two eval JSON files and exit (no cluster)",
    )
    args = parser.parse_args()

    if args.compare:
        left = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        right = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        cmp = compare_runs(left, right)
        _print_compare(cmp)
        out = Path(args.compare[1]).with_name("ablation_compare.json")
        if args.out_dir:
            out = Path(args.out_dir) / "ablation_compare.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cmp, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {out}")
        return

    if args.list or args.dry_run:
        for s in SCENARIOS:
            inj = s.manifest or "(no inject)"
            print(f"{s.id:<24} wait={s.wait_seconds:<4} {inj}")
            print(f"  query: {s.query}")
            print(f"  {s.notes}")
        if args.dry_run:
            print("\nCatalog order is the run order. healthy-baseline is first on purpose.")
        return

    if args.stale_trap:
        chosen = [STALE_TRAP]
    elif args.scenario:
        chosen = [scenario_by_id(args.scenario)]
    elif args.all:
        chosen = list(SCENARIOS)
    else:
        parser.print_help()
        sys.exit(1)

    os.environ["CITRUS_ABLATION"] = "legacy" if args.ablation == "legacy" else ""
    print(f"ablation={args.ablation or 'current'}  CITRUS_ABLATION={os.environ.get('CITRUS_ABLATION')!r}")

    asyncio.run(_run_scenarios(
        chosen,
        skip_inject=args.skip_inject,
        no_cleanup=args.no_cleanup,
        out_dir=args.out_dir,
        memory_enabled=not args.no_memory,
        memory_path=args.memory_path,
    ))


if __name__ == "__main__":
    main()
