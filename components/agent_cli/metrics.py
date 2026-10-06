"""Prometheus metrics for the agent runtime.

prometheus_client is optional: without it every helper is a no-op and
render() returns a plain-text notice. The stdio CLI path therefore gains
no mandatory dependency; install `citrus-agent-cli[metrics]` to enable.

Exposed via GET /metrics on the webhook server (the long-lived process).
"""
from __future__ import annotations

from typing import Tuple

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    _PROM = True
except ImportError:  # pragma: no cover - depends on environment
    _PROM = False

if _PROM:
    _LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0, 120.0)

    LLM_REQUEST_SECONDS = Histogram(
        "citrus_llm_request_seconds",
        "LLM request latency in seconds, per attempt.",
        ["provider", "model", "outcome"],
        buckets=_LATENCY_BUCKETS,
    )
    LLM_TOKENS_TOTAL = Counter(
        "citrus_llm_tokens_total",
        "Tokens consumed, from the API usage stats.",
        ["provider", "model", "kind"],
    )
    LLM_FAILURES_TOTAL = Counter(
        "citrus_llm_failures_total",
        "LLM request failures by reason (timeout/rate_limited/server_error/connection/fatal).",
        ["provider", "model", "reason"],
    )
    TOOL_CALLS_TOTAL = Counter(
        "citrus_tool_calls_total",
        "MCP tool calls by outcome (ok/error/denied).",
        ["tool", "outcome"],
    )
    AGENT_RUNS_TOTAL = Counter(
        "citrus_agent_runs_total",
        "Completed agent runs by evidence level (HIGH/MEDIUM/LOW).",
        ["evidence"],
    )


def observe_llm_request(provider: str, model: str, outcome: str, seconds: float) -> None:
    if _PROM:
        LLM_REQUEST_SECONDS.labels(provider=provider, model=model, outcome=outcome).observe(seconds)


def add_llm_tokens(provider: str, model: str, *, prompt: int = 0, completion: int = 0) -> None:
    if not _PROM:
        return
    if prompt > 0:
        LLM_TOKENS_TOTAL.labels(provider=provider, model=model, kind="prompt").inc(prompt)
    if completion > 0:
        LLM_TOKENS_TOTAL.labels(provider=provider, model=model, kind="completion").inc(completion)


def inc_llm_failure(provider: str, model: str, reason: str) -> None:
    if _PROM:
        LLM_FAILURES_TOTAL.labels(provider=provider, model=model, reason=reason).inc()


def inc_tool_call(tool: str, outcome: str) -> None:
    if _PROM:
        TOOL_CALLS_TOTAL.labels(tool=tool, outcome=outcome).inc()


def inc_agent_run(evidence: str) -> None:
    if _PROM:
        AGENT_RUNS_TOTAL.labels(evidence=evidence).inc()


def metrics_available() -> bool:
    return _PROM


def render() -> Tuple[bytes, str]:
    """Return (payload, content_type) for an HTTP /metrics response."""
    if not _PROM:
        return (
            b"# prometheus_client not installed; pip install 'citrus-agent-cli[metrics]'\n",
            "text/plain; charset=utf-8",
        )
    return generate_latest(), CONTENT_TYPE_LATEST
