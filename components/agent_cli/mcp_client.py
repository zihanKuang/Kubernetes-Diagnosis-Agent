"""MCP client: stdio (local) or Streamable HTTP (remote)."""

import asyncio
import copy
import os
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .exceptions import (
    MCPConnectionError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
)
from .logging_utils import log_mcp_debug, log_mcp_error
from .trace_store import parse_mcp_result
from .evidence_models import ToolPayload


class MCPClient:
    def __init__(
        self,
        command: str = "",
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout_seconds: float = 60.0,
        url: Optional[str] = None,
        auth_token: Optional[str] = None,
    ):
        self.command = command
        self.args = args or []
        self.cwd = cwd
        self.env = env
        self.timeout_seconds = timeout_seconds
        self.url = url
        self.auth_token = auth_token or os.getenv("MCP_AUTH_TOKEN")
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self._tools_cache: Dict[str, Dict] = {}

    async def connect(self):
        try:
            if self.url:
                log_mcp_debug(f"Connecting to MCP over HTTP: {self.url}")
                await self._connect_http()
            else:
                log_mcp_debug(
                    f"Connecting to MCP over stdio: "
                    f"{self.command} {' '.join(self.args)}"
                    + (f" (cwd={self.cwd})" if self.cwd else "")
                )
                await self._connect_stdio()

            await self.session.initialize()
            log_mcp_debug("Connected to MCP server")
            await self._fetch_tools()

        except Exception as e:
            log_mcp_error("Failed to connect to MCP server", error=e)
            raise MCPConnectionError(
                f"MCP connection failed: {e}", original_error=e
            )

    async def _connect_stdio(self):
        child_env = dict(os.environ)
        if self.cwd:
            existing = child_env.get("PYTHONPATH", "")
            child_env["PYTHONPATH"] = (
                self.cwd if not existing else f"{self.cwd}{os.pathsep}{existing}"
            )
        if self.env:
            child_env.update(self.env)

        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=child_env,
            cwd=self.cwd,
        )

        read_stream, write_stream = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

    async def _connect_http(self):
        headers: Dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        transport = await self.exit_stack.enter_async_context(
            streamablehttp_client(self.url, headers=headers)
        )
        # streamablehttp_client yields (read, write, get_session_id)
        read_stream, write_stream, _get_session_id = transport

        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )

    async def disconnect(self):
        try:
            await self.exit_stack.aclose()
            self.session = None
            log_mcp_debug("Disconnected from MCP server")
        except Exception as e:
            log_mcp_error("Error during disconnect", error=e)

    async def _fetch_tools(self):
        try:
            response = await self.session.list_tools()
            fresh: Dict[str, Dict] = {}
            for tool in response.tools:
                fresh[tool.name] = {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
            self._tools_cache = fresh
            log_mcp_debug(f"Fetched {len(self._tools_cache)} tools")
        except Exception as e:
            log_mcp_error("Failed to fetch tools", error=e)
            raise MCPConnectionError(
                f"Failed to fetch tools: {e}", original_error=e
            )

    async def refresh_tools(self):
        await self._fetch_tools()

    def get_tools(self, names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        if names is None:
            selected = list(self._tools_cache.values())
        else:
            selected = [
                self._tools_cache[name]
                for name in names
                if name in self._tools_cache
            ]
        return [self._as_openai_tool(copy.deepcopy(tool)) for tool in selected]

    def get_tools_by_name(self, names: Sequence[str]) -> Dict[str, Any]:
        found = []
        missing = []
        for name in names:
            if name in self._tools_cache:
                found.append(self._as_openai_tool(copy.deepcopy(self._tools_cache[name])))
            else:
                missing.append(name)
        return {"tools": found, "missing": missing}

    def _as_openai_tool(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        payload = await self.call_tool_result(tool_name, arguments)
        return payload.text

    async def call_tool_result(self, tool_name: str, arguments: Dict[str, Any]) -> ToolPayload:
        if tool_name not in self._tools_cache:
            raise ToolNotFoundError(
                f"Tool '{tool_name}' not found. "
                f"Available: {list(self._tools_cache.keys())}"
            )

        try:
            log_mcp_debug(f"Calling tool: {tool_name} with args: {arguments}")
            result = await asyncio.wait_for(
                self.session.call_tool(tool_name, arguments),
                timeout=self.timeout_seconds,
            )
            payload = parse_mcp_result(result)
            log_mcp_debug(
                f"Tool result: {payload.text[:200]}"
                f"{'...' if len(payload.text) > 200 else ''}"
            )
            return payload

        except asyncio.TimeoutError as e:
            log_mcp_error(
                f"Tool timed out after {self.timeout_seconds}s: {tool_name}",
                error=e,
            )
            raise ToolTimeoutError(
                f"Tool '{tool_name}' timed out after {self.timeout_seconds}s",
                original_error=e,
            )
        except ToolNotFoundError:
            raise
        except ToolTimeoutError:
            raise
        except Exception as e:
            log_mcp_error(f"Tool execution failed: {tool_name}", error=e)
            raise ToolExecutionError(
                f"Tool '{tool_name}' execution failed: {e}", original_error=e
            )
