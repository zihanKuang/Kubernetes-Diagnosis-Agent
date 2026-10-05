"""Metrics module: works with prometheus_client installed or falls back to no-op."""
from agent_cli.metrics import (
    add_llm_tokens,
    inc_agent_run,
    inc_llm_failure,
    inc_tool_call,
    metrics_available,
    observe_llm_request,
    render,
)


def test_helpers_never_raise():
    observe_llm_request("vllm", "m", "ok", 0.42)
    add_llm_tokens("vllm", "m", prompt=10, completion=5)
    inc_llm_failure("vllm", "m", "timeout")
    inc_tool_call("list_pods", "ok")
    inc_agent_run("HIGH")


def test_render_returns_scrapeable_payload():
    inc_tool_call("get_pod_logs", "ok")
    data, content_type = render()
    assert isinstance(data, bytes)
    assert content_type.startswith("text/plain")
    if metrics_available():
        assert b"citrus_tool_calls_total" in data
        assert b"citrus_llm_request_seconds" in data
    else:
        assert b"prometheus_client not installed" in data
