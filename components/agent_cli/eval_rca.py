"""Run labeled RCA scenarios against a live cluster, then score with hard rules.

  python -m agent_cli.eval_rca --list
  python -m agent_cli.eval_rca --dry-run
  python -m agent_cli.eval_rca --scenario healthy-baseline
  python -m agent_cli.eval_rca --all

Needs: cluster + citrus namespace + DEEPSEEK_API_KEY.
Chaos Mesh is required for the pod-kill scenarios only.
infra/faults/ fixtures (crash loop, image pull, OOM, probe, unschedulable)
are ordinary Deployments and do not need Chaos Mesh.
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
from .config import AgentConfig, default_trace_dir
from .eval_scenarios import SCENARIOS, STALE_TRAP, Scenario, scenario_by_id
from .eval_score import Score, score
from .memory import annotate_latest_hit
from .skills.errors import SkillError


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


def _token_total(prompt: int, completion: int) -> int:
    return int(prompt) + int(completion)


def _print_score(result: Score) -> None:
    mark = "PHRASE HIT " if result.phrase_hit else "PHRASE MISS"
    tokens = _token_total(result.prompt_tokens, result.completion_tokens)
    print(f"  {mark} evidence={result.evidence_level}  "
          f"diagnosis={result.diagnosis_status}  recovery={result.recovery_status}  "
          f"{result.duration_s}s  steps={result.steps}  tools={result.tool_calls}  "
          f"tokens={tokens}")
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
    hits = sum(1 for r in results if r.phrase_hit)
    print("\n" + "=" * 72)
    print(f"{'scenario':<24} {'phrase':<8} {'evidence':<8} {'sec':<8} {'tools':<8} {'tokens'}")
    print("-" * 72)
    for r in results:
        tokens = _token_total(r.prompt_tokens, r.completion_tokens)
        print(f"{r.scenario_id:<24} {str(r.phrase_hit):<8} "
              f"{r.evidence_level:<8} {str(r.duration_s):<8} {r.tool_calls:<8} {tokens}")
    print("-" * 72)
    print(f"phrase hit rate: {hits}/{len(results)} (legacy phrase rule, not RCA accuracy)")
    print("=" * 72)


def _save(out_dir: Path, results: List[Score], extra: dict | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"rca_eval_{stamp}.json"
    phrase_hits = sum(1 for r in results if r.phrase_hit)
    payload = {
        "created_utc": stamp,
        "scoring_rule": "phrase_v1",
        "scoring_note": "phrase_hit is the legacy phrase matcher, not RCA accuracy",
        "phrase_hit_rate": (phrase_hits / len(results) if results else 0.0),
        "hit_rate": (phrase_hits / len(results) if results else 0.0),
        "results": [r.as_dict() for r in results],
    }
    if extra:
        payload.update(extra)
    payload["prompt_tokens"] = sum(r.prompt_tokens for r in results)
    payload["completion_tokens"] = sum(r.completion_tokens for r in results)
    payload["llm_calls"] = sum(r.llm_calls for r in results)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {path}")
    return path


def _int_delta(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return int(right) - int(left)
    return None


def _token_pair_total(row: dict):
    prompt = row.get("prompt_tokens")
    completion = row.get("completion_tokens")
    if isinstance(prompt, bool) or isinstance(completion, bool):
        return None
    if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
        return int(prompt) + int(completion)
    return None


_BUDGET_KEYS = ("model_name", "llm_provider", "max_steps")
_COMPARE_META = ("engine", "skill_routing", "skill_id", "model_name", "llm_provider", "max_steps")


def _same_meta(baseline: dict, current: dict, keys=_COMPARE_META):
    missing = any(baseline.get(key) is None or current.get(key) is None for key in keys if key in _BUDGET_KEYS)
    # skill_routing may be absent on old files
    for key in keys:
        if key in {"skill_routing", "skill_id"} and (key not in baseline or key not in current):
            continue
        if baseline.get(key) != current.get(key) and key in baseline and key in current:
            return False
    if any(baseline.get(key) is None or current.get(key) is None for key in _BUDGET_KEYS):
        return None
    return all(baseline.get(key) == current.get(key) for key in _BUDGET_KEYS)


def _phrase_hit(row: dict) -> bool:
    """New reports store phrase_hit; old reports only have root_cause_hit."""
    if "phrase_hit" in row:
        return bool(row.get("phrase_hit"))
    return bool(row.get("root_cause_hit"))


def _same_budget(baseline: dict, current: dict):
    """True when model, provider, and step budget match. None if either side omitted them."""
    if any(baseline.get(key) is None or current.get(key) is None for key in _BUDGET_KEYS):
        return None
    return all(baseline.get(key) == current.get(key) for key in _BUDGET_KEYS)


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
        delta_tools = _int_delta(tools_b, tools_c)
        tokens_b = _token_pair_total(b)
        tokens_c = _token_pair_total(c)
        rows.append({
            "scenario_id": sid,
            "hit_baseline": _phrase_hit(b),
            "hit_current": _phrase_hit(c),
            "evidence_baseline": b.get("evidence_level"),
            "evidence_current": c.get("evidence_level"),
            "duration_s_baseline": dur_b,
            "duration_s_current": dur_c,
            "delta_duration_s": delta_dur,
            "tool_calls_baseline": tools_b,
            "tool_calls_current": tools_c,
            "delta_tool_calls": delta_tools,
            "prompt_tokens_baseline": b.get("prompt_tokens"),
            "prompt_tokens_current": c.get("prompt_tokens"),
            "delta_prompt_tokens": _int_delta(b.get("prompt_tokens"), c.get("prompt_tokens")),
            "completion_tokens_baseline": b.get("completion_tokens"),
            "completion_tokens_current": c.get("completion_tokens"),
            "delta_completion_tokens": _int_delta(
                b.get("completion_tokens"), c.get("completion_tokens")
            ),
            "tokens_baseline": tokens_b,
            "tokens_current": tokens_c,
            "delta_tokens": _int_delta(tokens_b, tokens_c),
        })
    hits_b = sum(1 for r in by_b.values() if _phrase_hit(r))
    hits_c = sum(1 for sid in by_b if _phrase_hit(by_c.get(sid) or {}))
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
    same_budget = _same_budget(baseline, current)
    if same_budget is False:
        verdict = f"budgets differ (model or max_steps) — {verdict}"
    skill_mode_b = baseline.get("skill_routing")
    skill_mode_c = current.get("skill_routing")
    comparable = True
    if skill_mode_b and skill_mode_c and skill_mode_b != skill_mode_c:
        comparable = False
        verdict = f"skill routing differs ({skill_mode_b} vs {skill_mode_c}) — not directly comparable — {verdict}"
    if baseline.get("engine") and current.get("engine") and baseline.get("engine") != current.get("engine"):
        # engines may still be compared; keep phrase_hit meaning
        pass
    return {
        "hit_rate_baseline": baseline.get("hit_rate"),
        "hit_rate_current": current.get("hit_rate"),
        "hits_baseline": f"{hits_b}/{n}",
        "hits_current": f"{hits_c}/{n}",
        "scenarios_with_fewer_tools": fewer_tools,
        "engine_baseline": baseline.get("engine"),
        "engine_current": current.get("engine"),
        "skill_routing_baseline": skill_mode_b,
        "skill_routing_current": skill_mode_c,
        "skill_id_baseline": baseline.get("skill_id"),
        "skill_id_current": current.get("skill_id"),
        "model_name_baseline": baseline.get("model_name"),
        "model_name_current": current.get("model_name"),
        "llm_provider_baseline": baseline.get("llm_provider"),
        "llm_provider_current": current.get("llm_provider"),
        "max_steps_baseline": baseline.get("max_steps"),
        "max_steps_current": current.get("max_steps"),
        "same_budget": same_budget,
        "comparable": comparable,
        "verdict": verdict,
        "rows": rows,
        "phrase_hit_note": "phrase_hit is the legacy phrase matcher, not RCA accuracy or skill-routing hit rate",
    }


def _print_compare(cmp: dict) -> None:
    print("\n" + "=" * 72)
    print("eval compare (baseline -> current)")
    print("-" * 72)
    print(f"hit rate: {cmp['hits_baseline']} -> {cmp['hits_current']}")
    print(f"engines: {cmp.get('engine_baseline')} -> {cmp.get('engine_current')}")
    if cmp.get("same_budget") is True:
        print(
            f"budget: same model={cmp.get('model_name_current')} "
            f"provider={cmp.get('llm_provider_current')} "
            f"max_steps={cmp.get('max_steps_current')}"
        )
    elif cmp.get("same_budget") is False:
        print(
            "budget differs: "
            f"{cmp.get('llm_provider_baseline')}/{cmp.get('model_name_baseline')} "
            f"max_steps={cmp.get('max_steps_baseline')} -> "
            f"{cmp.get('llm_provider_current')}/{cmp.get('model_name_current')} "
            f"max_steps={cmp.get('max_steps_current')}"
        )
    else:
        print("budget: not recorded (older eval JSON)")
    print(f"{'scenario':<24} {'hit':<12} {'ev':<12} {'sec d':<10} {'tools d':<8} {'tok d'}")
    print("-" * 72)
    for r in cmp["rows"]:
        hit = f"{r['hit_baseline']}->{r['hit_current']}"
        ev = f"{r['evidence_baseline']}->{r['evidence_current']}"
        tok = r.get("delta_tokens")
        tok_cell = "-" if tok is None else str(tok)
        print(f"{r['scenario_id']:<24} {hit:<12} {ev:<12} "
              f"{str(r['delta_duration_s']):<10} {str(r['delta_tool_calls']):<8} {tok_cell}")
    print("-" * 72)
    print(f"verdict: {cmp['verdict']}")
    print("=" * 72)


def build_eval_agent(config: AgentConfig, engine: str):
    """Same harness, chosen loop. Unknown engines fail before any cluster call."""
    if engine == "react":
        return ReActAgent(config)
    if engine == "langgraph":
        try:
            from .engine_langgraph import LangGraphAgent
        except ImportError as exc:
            raise ImportError(
                "langgraph is not installed — pip install -e \".[langgraph]\""
            ) from exc
        return LangGraphAgent(config)
    raise ValueError(f"unknown engine {engine!r}")


async def _run_scenarios(
    scenarios: Iterable[Scenario],
    *,
    skip_inject: bool,
    no_cleanup: bool,
    out_dir: Path,
    memory_enabled: bool,
    memory_path: Path | None,
    engine: str = "react",
    max_steps: int | None = None,
    model_name: str | None = None,
    skill_routing: str | None = None,
    skill_id: str | None = None,
) -> List[Score]:
    try:
        config = AgentConfig(
            memory_enabled=memory_enabled,
            memory_path=str(memory_path) if memory_path else None,
            writes_interactive=False,
            trace_dir=str(default_trace_dir()),
            skill_routing=skill_routing,
            skill_id=skill_id,
            **({"max_steps": max_steps} if max_steps is not None else {}),
        )
    except SkillError as exc:
        print(f"Error: {exc}")
        sys.exit(2)
    if model_name:
        config.model_name = model_name
    if not config.api_key:
        print("Error: no LLM API key configured (DEEPSEEK_API_KEY or CITRUS_LLM_*)")
        sys.exit(1)

    try:
        agent = build_eval_agent(config, engine)
    except (ImportError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    print(
        f"engine={engine}  model={config.model_name}  "
        f"provider={config.llm_provider}  max_steps={config.max_steps}"
    )
    await agent.initialize()
    results: List[Score] = []
    try:
        for scenario in scenarios:
            print(f"\n=== {scenario.id} ===")
            print(f"  {scenario.notes}")
            try:
                if not skip_inject:
                    _inject(scenario)
                answer = await agent.run(scenario.query, scenario=scenario.id, entrypoint="eval")
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
                    phrase_hit=False,
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
        "engine": engine,
        "model_name": config.model_name,
        "llm_provider": config.llm_provider,
        "max_steps": config.max_steps,
        "memory_enabled": memory_enabled,
        "memory_path": str(memory_path) if memory_path else None,
        "ablation": os.getenv("CITRUS_ABLATION") or "current",
        "skill_routing": config.skill_routing,
        "skill_id": config.skill_id or "",
        "scoring_note": "phrase_hit is the legacy phrase matcher, not RCA accuracy or skill-routing hit rate",
    })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Labeled RCA eval (hard-rule scorer)")
    parser.add_argument("--list", action="store_true", help="Print the catalog and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show inject plan, do not call the LLM")
    parser.add_argument("--scenario", help="Run one scenario id")
    parser.add_argument("--all", action="store_true", help="Run every scenario in catalog order")
    parser.add_argument("--skip-inject", action="store_true", help="Do not apply/delete scenario manifests")
    parser.add_argument("--no-cleanup", action="store_true", help="Leave the injected manifest in the cluster")
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
        "--engine",
        choices=["react", "langgraph"],
        default="react",
        help="Orchestration engine. Both write duration and tokens into the same JSON schema. "
             "langgraph needs the langgraph extra.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Pin the step budget so two engine runs are comparable (default: 10).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Pin the model name for this run (default: CITRUS_LLM_MODEL / MODEL_NAME).",
    )
    parser.add_argument(
        "--skill-routing",
        choices=["legacy", "auto"],
        default=None,
        help="Skill routing mode recorded with the eval JSON. Default legacy.",
    )
    parser.add_argument(
        "--skill",
        default=None,
        help="Lock a Skill id for this eval (implies auto). Scenario labels still are not routing inputs.",
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

    if args.max_steps is not None and args.max_steps < 1:
        print("Error: --max-steps must be >= 1", file=sys.stderr)
        sys.exit(1)

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
        engine=args.engine,
        max_steps=args.max_steps,
        model_name=args.model,
        skill_routing=args.skill_routing,
        skill_id=args.skill,
    ))


if __name__ == "__main__":
    main()
