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

load_dotenv(_PACKAGE_DIR / ".env", encoding="utf-8-sig")
load_dotenv(_REPO_ROOT / ".env", encoding="utf-8-sig")


def _default_mcp_server_args() -> list[str]:
    # Run as a module (not a bare script) since mcp_server/ uses relative
    # imports; mcp_server_cwd below puts the package on the child's path.
    return ["-m", "mcp_server.server"]


@dataclass
class AgentConfig:
    model_name: str = "deepseek-v4-flash"
    api_key: Optional[str] = None
    llm_base_url: str = "https://api.deepseek.com"
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

    def __post_init__(self):
        if self.api_key is None:
            self.api_key = os.getenv("DEEPSEEK_API_KEY")

        env_base = os.getenv("DEEPSEEK_BASE_URL")
        if env_base:
            self.llm_base_url = env_base

        env_model = os.getenv("MODEL_NAME")
        if env_model:
            self.model_name = env_model

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

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            model_name=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            max_steps=int(os.getenv("MAX_STEPS", "10")),
        )
