"""Agent CLI configuration."""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .prompts import DEFAULT_SRE_SYSTEM_INSTRUCTION, LEGACY_SRE_SYSTEM_INSTRUCTION

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent.parent
_MCP_SERVER_DIR = _PACKAGE_DIR.parent / "mcp-server"


def default_trace_dir() -> str:
    return str(_REPO_ROOT / "data" / "runs")

load_dotenv(_PACKAGE_DIR / ".env", encoding="utf-8-sig")
load_dotenv(_REPO_ROOT / ".env", encoding="utf-8-sig")


def _default_mcp_server_args() -> list[str]:
    # Run as a module (not a bare script) since mcp_server/ uses relative
    # imports; mcp_server_cwd below puts the package on the child's path.
    return ["-m", "mcp_server.server"]


@dataclass
class ReviewerConfig:
    """Separate from the generator. Missing credentials stay not_reviewed."""

    provider: str = ""
    model_name: str = ""
    base_url: str = ""
    api_key: Optional[str] = None
    timeout_seconds: float = 120.0
    max_tokens: int = 2048

    def configured(self) -> bool:
        return bool(self.api_key) and bool(self.model_name)

    def public_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_env(cls, existing: Optional["ReviewerConfig"] = None) -> "ReviewerConfig":
        cfg = existing or cls()
        if not cfg.provider:
            cfg.provider = (os.getenv("CITRUS_REVIEWER_PROVIDER") or "").strip()
        if not cfg.model_name:
            cfg.model_name = (os.getenv("CITRUS_REVIEWER_MODEL") or "").strip()
        if not cfg.base_url:
            cfg.base_url = (os.getenv("CITRUS_REVIEWER_BASE_URL") or "").strip()
        if cfg.api_key is None:
            key = os.getenv("CITRUS_REVIEWER_API_KEY")
            cfg.api_key = key if key else None
        env_timeout = os.getenv("CITRUS_REVIEWER_TIMEOUT_SECONDS")
        if env_timeout:
            cfg.timeout_seconds = float(env_timeout)
        env_tokens = os.getenv("CITRUS_REVIEWER_MAX_TOKENS")
        if env_tokens:
            cfg.max_tokens = int(env_tokens)
        return cfg


@dataclass
class AgentConfig:
    model_name: str = "deepseek-v4-flash"
    api_key: Optional[str] = None
    llm_base_url: str = "https://api.deepseek.com"
    # "deepseek" (hosted default) or "vllm" / any OpenAI-compatible server.
    llm_provider: str = "deepseek"
    llm_timeout_seconds: float = 120.0
    llm_max_concurrency: int = 4
    # DeepSeek can take 8k; Colab T4 vLLM is served at max_model_len=4096.
    llm_max_tokens: int = 8192
    system_instruction: str = DEFAULT_SRE_SYSTEM_INSTRUCTION

    mcp_server_command: str = field(default_factory=lambda: sys.executable)
    mcp_server_args: list[str] = field(default_factory=_default_mcp_server_args)
    mcp_server_cwd: Optional[str] = None
    tool_timeout_seconds: float = 60.0
    # set mcp_url to use HTTP instead of spawning a local stdio server
    mcp_url: Optional[str] = None
    mcp_auth_token: Optional[str] = None

    max_steps: int = 10

    max_retries: int = 3
    base_retry_delay_ms: int = 500
    max_retry_delay_ms: int = 32000
    retry_jitter_factor: float = 0.25

    max_content_length: int = 4000

    log_level: str = "INFO"
    log_to_file: bool = False

    memory_enabled: bool = True
    memory_path: Optional[str] = None
    # None = follow stdin.isatty(). False in eval/webhook. True in interactive CLI.
    writes_interactive: Optional[bool] = None
    # None = in-memory only. CLI/eval/webhook set data/runs.
    trace_dir: Optional[str] = None
    reviewer: ReviewerConfig = field(default_factory=ReviewerConfig)
    skill_routing: Optional[str] = None
    skill_id: Optional[str] = None
    skill_extra_dirs: Optional[list[str]] = None
    skill_disabled: Optional[list[str]] = None
    skill_writes: Optional[bool] = None
    skill_instruction_dir: Optional[str] = None
    max_tool_calls: Optional[int] = None
    skill_settings: Optional[object] = field(default=None, repr=False)

    def __post_init__(self):
        env_provider = os.getenv("CITRUS_LLM_PROVIDER")
        if env_provider:
            self.llm_provider = env_provider.strip().lower()

        if self.api_key is None:
            self.api_key = os.getenv("CITRUS_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        if self.api_key is None and self.llm_provider != "deepseek":
            # Self-hosted OpenAI-compatible servers (vLLM without --api-key)
            # ignore the key, but the openai client requires a non-empty one.
            self.api_key = "EMPTY"

        # CITRUS_LLM_* wins; DEEPSEEK_* / MODEL_NAME kept as fallback.
        env_base = os.getenv("CITRUS_LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL")
        if env_base:
            self.llm_base_url = env_base

        env_model = os.getenv("CITRUS_LLM_MODEL") or os.getenv("MODEL_NAME")
        if env_model:
            self.model_name = env_model

        env_llm_timeout = os.getenv("CITRUS_LLM_TIMEOUT_SECONDS")
        if env_llm_timeout:
            self.llm_timeout_seconds = float(env_llm_timeout)

        env_llm_concurrency = os.getenv("CITRUS_LLM_MAX_CONCURRENCY")
        if env_llm_concurrency:
            self.llm_max_concurrency = int(env_llm_concurrency)

        env_llm_max_tokens = os.getenv("CITRUS_LLM_MAX_TOKENS")
        if env_llm_max_tokens:
            self.llm_max_tokens = int(env_llm_max_tokens)
        elif self.llm_provider != "deepseek":
            # Stay under typical self-hosted --max-model-len 4096.
            # Tool dumps (list_pods of a full namespace) eat the prompt; keep
            # completion short and truncate tool output aggressively.
            self.llm_max_tokens = 256
            self.max_content_length = 800

        if self.mcp_server_cwd is None:
            self.mcp_server_cwd = str(_MCP_SERVER_DIR)

        if self.mcp_url is None:
            self.mcp_url = os.getenv("MCP_URL")

        if self.mcp_auth_token is None:
            self.mcp_auth_token = os.getenv("MCP_AUTH_TOKEN")

        env_timeout = os.getenv("TOOL_TIMEOUT_SECONDS")
        if env_timeout:
            self.tool_timeout_seconds = float(env_timeout)

        env_prompt = os.getenv("AGENT_SYSTEM_INSTRUCTION")
        if env_prompt:
            self.system_instruction = env_prompt

        env_mem = os.getenv("CITRUS_MEMORY")
        if env_mem is not None and env_mem.strip() != "":
            self.memory_enabled = env_mem.strip().lower() not in {"0", "false", "off", "no"}

        env_mem_path = os.getenv("CITRUS_MEMORY_PATH")
        if env_mem_path:
            self.memory_path = env_mem_path

        if os.getenv("CITRUS_ABLATION", "").strip().lower() == "legacy":
            self.system_instruction = LEGACY_SRE_SYSTEM_INSTRUCTION

        self.reviewer = ReviewerConfig.from_env(self.reviewer)
        self._apply_skill_settings()

    def _apply_skill_settings(self) -> None:
        from .skills.settings import resolve_skill_settings

        settings = resolve_skill_settings(
            cli_routing=self.skill_routing,
            cli_skill=self.skill_id,
            cli_dirs=self.skill_extra_dirs,
            cli_disabled=self.skill_disabled,
            cli_writes=self.skill_writes,
            cli_instruction_dir=self.skill_instruction_dir,
        )
        self.skill_settings = settings
        self.skill_routing = settings.routing
        self.skill_id = settings.skill_id or None
        self.skill_extra_dirs = list(settings.extra_dirs)
        self.skill_disabled = list(settings.disabled)
        self.skill_writes = settings.writes
        self.skill_instruction_dir = settings.instruction_dir or None

    @classmethod
    def from_env(cls) -> "AgentConfig":
        # model/provider/api_key env handling lives in __post_init__.
        return cls(max_steps=int(os.getenv("MAX_STEPS", "10")))
