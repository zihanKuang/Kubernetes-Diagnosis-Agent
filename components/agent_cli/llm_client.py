"""
Model client for any OpenAI-compatible Chat Completions endpoint.

Providers: DeepSeek (hosted, default) and vLLM / other self-hosted
OpenAI-compatible servers. The agent stays provider-agnostic: messages are
OpenAI-shaped, and switching providers is a config/env change, not a code
change (CITRUS_LLM_PROVIDER / CITRUS_LLM_BASE_URL / CITRUS_LLM_MODEL).

Reliability lives here, outside the ReAct loop:
  - concurrency semaphore (protects a small self-hosted endpoint),
  - per-request timeout (asyncio.wait_for),
  - bounded retries with exponential backoff + jitter (retry_utils),
  - Prometheus metrics per attempt (latency, tokens, failures).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from .exceptions import LLMError
from .logging_utils import log_llm_debug, log_llm_error
from .metrics import add_llm_tokens, inc_llm_failure, observe_llm_request
from .retry_utils import get_retry_delay

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_PROVIDER = "deepseek"


def _import_openai():
    from openai import AsyncOpenAI
    return AsyncOpenAI


def arguments_as_dict(raw: Any) -> Dict[str, Any]:
    """OpenAI returns arguments as a JSON string; the agent needs a dict."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def arguments_as_json(raw: Any) -> str:
    """The API expects tool-call arguments as a JSON object string."""
    if isinstance(raw, str):
        return raw if raw else "{}"
    if raw is None:
        return "{}"
    return json.dumps(raw)


def messages_for_api(
    messages: List[Dict[str, Any]],
    system_instruction: str = "",
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if system_instruction:
        out.append({"role": "system", "content": system_instruction})

    for msg in messages:
        role = msg["role"]
        if role == "assistant" and msg.get("tool_calls"):
            tool_calls = []
            for tc in msg["tool_calls"]:
                tool_calls.append(
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": arguments_as_json(tc["function"].get("arguments")),
                        },
                    }
                )
            out.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or None,
                    "tool_calls": tool_calls,
                }
            )
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id") or "",
                    "content": msg.get("content") or "",
                }
            )
        else:
            out.append({"role": role, "content": msg.get("content") or ""})
    return out


def retryable_reason(error: Exception) -> Optional[str]:
    """Reason label if the error is worth retrying, else None (fatal)."""
    if isinstance(error, asyncio.TimeoutError):
        return "timeout"
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        if status == 429:
            return "rate_limited"
        if status >= 500:
            return "server_error"
        return None
    try:
        from openai import APIConnectionError

        if isinstance(error, APIConnectionError):
            return "connection"
    except ImportError:  # pragma: no cover - openai is a hard dependency
        if isinstance(error, ConnectionError):
            return "connection"
    return None


class ModelClient:
    """Provider-agnostic chat client with timeout, retry, and concurrency cap."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        system_instruction: str = "",
        base_url: str = DEFAULT_BASE_URL,
        provider: str = DEFAULT_PROVIDER,
        timeout_seconds: float = 120.0,
        max_concurrency: int = 4,
        max_retries: int = 3,
        base_retry_delay_ms: int = 500,
        max_retry_delay_ms: int = 32000,
        retry_jitter_factor: float = 0.25,
        max_tokens: int = 8192,
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.system_instruction = system_instruction
        self.base_url = base_url.rstrip("/")
        self.provider = (provider or DEFAULT_PROVIDER).strip().lower()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, int(max_retries))
        self.base_retry_delay_ms = base_retry_delay_ms
        self.max_retry_delay_ms = max_retry_delay_ms
        self.retry_jitter_factor = retry_jitter_factor
        self.max_tokens = max(1, int(max_tokens))
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        if not self.api_key:
            raise LLMError(
                "LLM API key is not set (set CITRUS_LLM_API_KEY, or DEEPSEEK_API_KEY "
                "for the default DeepSeek provider; self-hosted vLLM accepts EMPTY)"
            )
        AsyncOpenAI = _import_openai()
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        log_llm_debug(
            f"Model client ready: provider={self.provider} model={self.model_name} @ {self.base_url}"
        )

    def _request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str | None = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages_for_api(messages, self.system_instruction),
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
        }
        if self.provider == "deepseek":
            # DeepSeek-only knob. Non-thinking: cheaper, and ReAct already
            # does the reasoning loop. vLLM would reject this extra_body.
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        return kwargs

    async def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str | None = None,
    ) -> Dict[str, Any]:
        self._ensure_client()
        kwargs = self._request_kwargs(messages, tools, tool_choice=tool_choice)
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            start = time.monotonic()
            try:
                async with self._semaphore:
                    response = await asyncio.wait_for(
                        self._client.chat.completions.create(**kwargs),
                        timeout=self.timeout_seconds,
                    )
            except Exception as e:
                elapsed = time.monotonic() - start
                reason = retryable_reason(e)
                observe_llm_request(self.provider, self.model_name, "error", elapsed)
                inc_llm_failure(self.provider, self.model_name, reason or "fatal")
                if reason is None:
                    log_llm_error("LLM generation failed", error=e)
                    raise LLMError(f"LLM generation failed: {e}", original_error=e)
                last_error = e
                if attempt < self.max_retries:
                    delay = get_retry_delay(
                        attempt=attempt,
                        base_delay_ms=self.base_retry_delay_ms,
                        max_delay_ms=self.max_retry_delay_ms,
                        jitter_factor=self.retry_jitter_factor,
                    )
                    log_llm_error(
                        f"LLM {reason}, retrying in {delay:.2f}s "
                        f"(attempt {attempt}/{self.max_retries})",
                        error=e,
                    )
                    await asyncio.sleep(delay)
            else:
                elapsed = time.monotonic() - start
                observe_llm_request(self.provider, self.model_name, "ok", elapsed)
                usage = getattr(response, "usage", None)
                if usage is not None:
                    add_llm_tokens(
                        self.provider,
                        self.model_name,
                        prompt=getattr(usage, "prompt_tokens", 0) or 0,
                        completion=getattr(usage, "completion_tokens", 0) or 0,
                    )
                return self._parse_response(response)

        raise LLMError(
            f"LLM request failed after {self.max_retries} attempts: {last_error}",
            original_error=last_error,
        )

    def _parse_response(self, response) -> Dict[str, Any]:
        try:
            message = response.choices[0].message
        except (IndexError, AttributeError) as e:
            raise LLMError(f"Empty LLM response: {e}", original_error=e)

        result: Dict[str, Any] = {"content": message.content, "tool_calls": []}
        for tc in message.tool_calls or []:
            result["tool_calls"].append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": arguments_as_dict(tc.function.arguments),
                    },
                }
            )
        return result


# Backwards-compatible alias (pre-vLLM name).
LLMClient = ModelClient
