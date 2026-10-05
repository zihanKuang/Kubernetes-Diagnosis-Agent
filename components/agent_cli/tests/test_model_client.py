"""ModelClient reliability: timeout, retry, concurrency cap, provider switch."""
import asyncio
from types import SimpleNamespace

import pytest

from agent_cli.config import AgentConfig
from agent_cli.exceptions import LLMError
from agent_cli.llm_client import LLMClient, ModelClient, retryable_reason


def make_client(**overrides) -> ModelClient:
    kwargs = dict(
        model_name="test-model",
        api_key="test-key",
        provider="vllm",
        timeout_seconds=5.0,
        max_concurrency=4,
        max_retries=2,
        base_retry_delay_ms=1,
        max_retry_delay_ms=2,
        retry_jitter_factor=0.0,
    )
    kwargs.update(overrides)
    return ModelClient(**kwargs)


def inject_fake_api(client: ModelClient, create_fn) -> None:
    """Bypass the real AsyncOpenAI: _ensure_client sees a ready client."""
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn))
    )


def fake_response(content="ok"):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class FakeAPIError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def test_timeout_is_retried_then_fails():
    client = make_client(timeout_seconds=0.01)
    calls = {"n": 0}

    async def slow_create(**kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.5)
        return fake_response()

    inject_fake_api(client, slow_create)
    with pytest.raises(LLMError, match="after 2 attempts"):
        asyncio.run(client.generate_with_tools([{"role": "user", "content": "q"}], []))
    assert calls["n"] == 2


def test_server_error_is_retried_then_succeeds():
    client = make_client()
    calls = {"n": 0}

    async def flaky_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FakeAPIError(503)
        return fake_response("recovered")

    inject_fake_api(client, flaky_create)
    result = asyncio.run(
        client.generate_with_tools([{"role": "user", "content": "q"}], [])
    )
    assert result["content"] == "recovered"
    assert calls["n"] == 2


def test_fatal_client_error_is_not_retried():
    client = make_client()
    calls = {"n": 0}

    async def bad_request(**kwargs):
        calls["n"] += 1
        raise FakeAPIError(400)

    inject_fake_api(client, bad_request)
    with pytest.raises(LLMError, match="generation failed"):
        asyncio.run(client.generate_with_tools([{"role": "user", "content": "q"}], []))
    assert calls["n"] == 1


def test_semaphore_caps_concurrent_requests():
    client = make_client(max_concurrency=1)
    state = {"current": 0, "max_seen": 0}

    async def tracked_create(**kwargs):
        state["current"] += 1
        state["max_seen"] = max(state["max_seen"], state["current"])
        await asyncio.sleep(0.02)
        state["current"] -= 1
        return fake_response()

    inject_fake_api(client, tracked_create)

    async def run_three():
        msgs = [{"role": "user", "content": "q"}]
        await asyncio.gather(*(client.generate_with_tools(msgs, []) for _ in range(3)))

    asyncio.run(run_three())
    assert state["max_seen"] == 1


def test_vllm_can_require_tool_choice():
    vllm = make_client(provider="vllm", max_tokens=1024)
    msgs = [{"role": "user", "content": "q"}]
    tools = [{"type": "function", "function": {"name": "list_pods", "parameters": {}}}]
    kwargs = vllm._request_kwargs(msgs, tools, tool_choice="required")
    assert kwargs["tool_choice"] == "required"
    assert kwargs["tools"] == tools


def test_deepseek_thinking_knob_is_provider_gated():
    deepseek = make_client(provider="deepseek")
    vllm = make_client(provider="vllm", max_tokens=1024)
    msgs = [{"role": "user", "content": "q"}]
    ds = deepseek._request_kwargs(msgs, [])
    vl = vllm._request_kwargs(msgs, [])
    assert "extra_body" in ds
    assert ds["max_tokens"] == 8192
    assert "extra_body" not in vl
    assert vl["max_tokens"] == 1024


def test_retryable_reason_classification():
    assert retryable_reason(asyncio.TimeoutError()) == "timeout"
    assert retryable_reason(FakeAPIError(429)) == "rate_limited"
    assert retryable_reason(FakeAPIError(500)) == "server_error"
    assert retryable_reason(FakeAPIError(404)) is None
    assert retryable_reason(ValueError("nope")) is None


def test_legacy_llmclient_alias():
    assert LLMClient is ModelClient


def test_citrus_env_overrides_win(monkeypatch):
    monkeypatch.setenv("CITRUS_LLM_PROVIDER", "vllm")
    monkeypatch.setenv("CITRUS_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("CITRUS_LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
    monkeypatch.setenv("CITRUS_LLM_MAX_CONCURRENCY", "2")
    monkeypatch.delenv("CITRUS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # legacy vars present but must lose to CITRUS_LLM_*
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")

    config = AgentConfig()
    assert config.llm_provider == "vllm"
    assert config.llm_base_url == "http://127.0.0.1:8000/v1"
    assert config.model_name == "Qwen/Qwen2.5-1.5B-Instruct"
    assert config.llm_max_concurrency == 2
    # non-deepseek provider without a key falls back to vLLM's EMPTY convention
    assert config.api_key == "EMPTY"
    assert config.llm_max_tokens == 256
    assert config.max_content_length == 800


def test_deepseek_stays_default(monkeypatch):
    for var in (
        "CITRUS_LLM_PROVIDER",
        "CITRUS_LLM_BASE_URL",
        "CITRUS_LLM_MODEL",
        "CITRUS_LLM_API_KEY",
        "DEEPSEEK_BASE_URL",
        "MODEL_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    config = AgentConfig()
    assert config.llm_provider == "deepseek"
    assert config.llm_base_url == "https://api.deepseek.com"
    assert config.api_key == "sk-test"
