"""Optional OpenEvals wrapper. Tests use FakeJudge; models stay ours."""
from __future__ import annotations

from typing import Any, Optional, Protocol

from ..config import ReviewerConfig
from .prompts import REVIEW_SYSTEM


OPENEVALS_PACKAGE = "openevals"


class Judge(Protocol):
    async def complete(self, *, system: str, user: str) -> str:
        ...


class FakeJudge:
    """Test double. No network."""

    def __init__(self, text: str):
        self.text = text
        self.calls = 0
        self.last_system = ""
        self.last_user = ""

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        self.last_system = system
        self.last_user = user
        return self.text


class ModelJudge:
    def __init__(self, client: Any):
        self.client = client

    async def complete(self, *, system: str, user: str) -> str:
        previous = self.client.system_instruction
        self.client.system_instruction = system
        try:
            result = await self.client.generate_with_tools(
                messages=[{"role": "user", "content": user}],
                tools=[],
            )
        finally:
            self.client.system_instruction = previous
        return str(result.get("content") or "")


def create_model_judge(config: ReviewerConfig) -> ModelJudge:
    from .llm_client import ModelClient

    client = ModelClient(
        model_name=config.model_name,
        api_key=config.api_key or "",
        system_instruction=REVIEW_SYSTEM,
        base_url=config.base_url or "https://api.deepseek.com",
        provider=config.provider or "deepseek",
        timeout_seconds=config.timeout_seconds,
        max_tokens=config.max_tokens,
    )
    return ModelJudge(client)


def create_openevals_judge(config: ReviewerConfig) -> Optional[Judge]:
    """Return an OpenEvals-backed judge, or None if the extra is not installed."""
    try:
        import openevals  # noqa: F401
    except ImportError:
        return None
    return _OpenEvalsJudge(config)


class _OpenEvalsJudge:
    def __init__(self, config: ReviewerConfig):
        self.config = config

    async def complete(self, *, system: str, user: str) -> str:
        try:
            from openevals.llm import create_llm_as_judge
        except ImportError:
            from openevals import create_llm_as_judge  # type: ignore
        judge = create_llm_as_judge(
            prompt=system + "\n\n{inputs}\n{outputs}",
            model=self.config.model_name,
        )
        result = judge(inputs=user, outputs="")
        if isinstance(result, str):
            return result
        return str(result)
