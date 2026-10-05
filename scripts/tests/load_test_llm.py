#!/usr/bin/env python3
"""Concurrency sweep load test for any OpenAI-compatible chat endpoint (vLLM, DeepSeek, ...).

Per concurrency level it fires N chat completions and reports wall-clock
P50/P95 latency, request throughput, and output token rate. Continuous
batching shows up as superlinear throughput from level 1 -> 4 on vLLM.

Usage (needs the `openai` package; the components venv has it):
  python scripts/tests/load_test_llm.py \
      --base-url http://127.0.0.1:8000/v1 \
      --model Qwen/Qwen2.5-1.5B-Instruct \
      --concurrency 1 4 16 --requests-per-level 24 --max-tokens 128 \
      --output data/eval/load_test_vllm.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_PROMPT = (
    "You are a Kubernetes SRE. In 3 sentences, list the first checks you run "
    "when a Deployment's pods are stuck in CrashLoopBackOff."
)


def percentile(samples: List[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[rank]


async def one_request(client, args, sem: asyncio.Semaphore) -> Dict[str, Any]:
    async with sem:
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=args.model,
                    messages=[{"role": "user", "content": args.prompt}],
                    max_tokens=args.max_tokens,
                    temperature=0.2,
                ),
                timeout=args.timeout,
            )
        except Exception as e:
            return {"ok": False, "seconds": time.monotonic() - start, "error": repr(e)}
        usage = getattr(response, "usage", None)
        return {
            "ok": True,
            "seconds": time.monotonic() - start,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }


async def run_level(client, args, level: int) -> Dict[str, Any]:
    sem = asyncio.Semaphore(level)
    wall_start = time.monotonic()
    results = await asyncio.gather(
        *(one_request(client, args, sem) for _ in range(args.requests_per_level))
    )
    wall = time.monotonic() - wall_start

    ok = [r for r in results if r["ok"]]
    errors = [r for r in results if not r["ok"]]
    latencies = [r["seconds"] for r in ok]
    tokens = sum(r["completion_tokens"] for r in ok)
    summary = {
        "concurrency": level,
        "requests": len(results),
        "succeeded": len(ok),
        "failed": len(errors),
        "wall_seconds": round(wall, 2),
        "p50_seconds": round(percentile(latencies, 50), 3),
        "p95_seconds": round(percentile(latencies, 95), 3),
        "mean_seconds": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "throughput_rps": round(len(ok) / wall, 3) if wall > 0 else 0.0,
        "output_tokens_per_s": round(tokens / wall, 1) if wall > 0 else 0.0,
        "sample_errors": [e["error"] for e in errors[:3]],
    }
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible base URL ending in /v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default="EMPTY", help="vLLM without --api-key accepts anything")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 16])
    parser.add_argument("--requests-per-level", type=int, default=24)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--output", default=None, help="Optional JSON summary path")
    args = parser.parse_args()

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=args.api_key, base_url=args.base_url)

    print(f"endpoint : {args.base_url}")
    print(f"model    : {args.model}")
    print(f"requests : {args.requests_per_level} per level, max_tokens={args.max_tokens}\n")

    header = f"{'conc':>4} | {'ok/all':>7} | {'P50 s':>7} | {'P95 s':>7} | {'req/s':>7} | {'tok/s':>8}"
    print(header)
    print("-" * len(header))

    levels: List[Dict[str, Any]] = []
    for level in args.concurrency:
        summary = await run_level(client, args, level)
        levels.append(summary)
        print(
            f"{summary['concurrency']:>4} | "
            f"{summary['succeeded']:>3}/{summary['requests']:<3} | "
            f"{summary['p50_seconds']:>7.3f} | "
            f"{summary['p95_seconds']:>7.3f} | "
            f"{summary['throughput_rps']:>7.3f} | "
            f"{summary['output_tokens_per_s']:>8.1f}"
        )
        if summary["sample_errors"]:
            print(f"     errors: {summary['sample_errors']}")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base_url": args.base_url,
            "model": args.model,
            "requests_per_level": args.requests_per_level,
            "max_tokens": args.max_tokens,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "levels": levels,
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nsummary written to {out}")


if __name__ == "__main__":
    asyncio.run(main())
