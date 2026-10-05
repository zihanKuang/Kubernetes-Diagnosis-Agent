"""ReAct agent: LLM reasoning loop + MCP tool calls."""
import asyncio
import logging
import time
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

from .config import AgentConfig
from .evidence import assess, attach_footer
from .eval_score import split_answer
from .gated_writes import GATED_TOOLS, audit, decide, stdin_is_tty
from .mcp_client import MCPClient
from .memory import (
    DEFAULT_PATH as MEMORY_DEFAULT_PATH,
    append_postmortem,
    format_prefix,
    retrieve,
)
from .llm_client import ModelClient
from .metrics import inc_agent_run, inc_tool_call
from .retry_utils import get_retry_delay
from .logging_utils import log_agent_debug, log_agent_info, log_agent_error
from .exceptions import (
    MaxStepsExceededError,
    ToolExecutionError,
    ToolTimeoutError,
    ToolNotFoundError,
    LLMError
)


class ReActAgent:
    """Reason -> call tools -> observe, until a final answer or max_steps."""

    def __init__(self, config: AgentConfig):
        self.config = config

        if config.mcp_url:
            self.mcp_client = MCPClient(
                url=config.mcp_url,
                auth_token=config.mcp_auth_token,
                timeout_seconds=config.tool_timeout_seconds,
            )
        else:
            self.mcp_client = MCPClient(
                command=config.mcp_server_command,
                args=config.mcp_server_args,
                cwd=config.mcp_server_cwd,
                timeout_seconds=config.tool_timeout_seconds,
            )
        self.llm_client = ModelClient(
            model_name=config.model_name,
            api_key=config.api_key,
            system_instruction=config.system_instruction,
            base_url=config.llm_base_url,
            provider=config.llm_provider,
            timeout_seconds=config.llm_timeout_seconds,
            max_concurrency=config.llm_max_concurrency,
            max_retries=config.max_retries,
            base_retry_delay_ms=config.base_retry_delay_ms,
            max_retry_delay_ms=config.max_retry_delay_ms,
            retry_jitter_factor=config.retry_jitter_factor,
            max_tokens=config.llm_max_tokens,
        )

        self.messages: List[Dict[str, Any]] = []
        self.tools: List[Dict[str, Any]] = []
        self.stats = self._empty_stats()
        self.last_check = None
        # Tests inject a fake approver. Interactive CLI uses stdin y/n.
        self.approve_writes = None

        self._setup_logging()
        log_agent_info("Agent initialized")

    @staticmethod
    def _empty_stats() -> Dict[str, Any]:
        return {
            "total_steps": 0,
            "tool_calls": {},
            "errors": 0,
            "start_time": None,
            "end_time": None,
        }
    
    def _setup_logging(self):
        """Setup logging configuration"""
        level = getattr(logging, self.config.log_level)
        
        handlers = [logging.StreamHandler()]
        
        if self.config.log_to_file:
            log_file = f"agent_{datetime.now():%Y%m%d_%H%M%S}.log"
            handlers.append(logging.FileHandler(log_file))
        
        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=handlers,
            force=True
        )
    
    async def initialize(self):
        """Initialize agent (connect to MCP server, fetch tools)"""
        log_agent_info("Initializing agent...")
        
        # Connect to MCP server
        await self.mcp_client.connect()
        
        # Get available tools
        self.tools = self.mcp_client.get_tools()
        log_agent_info(f"Agent ready with {len(self.tools)} tools")
    
    async def cleanup(self):
        """Cleanup resources"""
        await self.mcp_client.disconnect()
    
    async def run(self, user_query: str, *, scenario: str = "unknown") -> str:
        """
        Run ReAct loop for user query
        
        Args:
            user_query: User's question or request
            scenario: Optional eval/fault label stored with the postmortem
            
        Returns:
            Final answer from agent
        """
        # fresh stats each query (interactive mode)
        self.stats = self._empty_stats()
        self.stats["start_time"] = time.time()
        self.last_check = None

        prompt = user_query
        if self.config.memory_enabled:
            hits = retrieve(user_query, path=self._memory_path())
            prefix = format_prefix(hits)
            if prefix:
                prompt = prefix + user_query
                log_agent_info(f"Injected {len(hits)} postmortem hint(s)")
        
        self.messages = [
            {"role": "user", "content": prompt}
        ]
        
        log_agent_info(f"Starting ReAct loop for: {user_query}")
        
        try:
            result = await self._react_loop()
        except MaxStepsExceededError:
            log_agent_error(f"Exceeded max steps ({self.config.max_steps})")
            result = "Agent exceeded maximum reasoning steps without reaching a conclusion."
        except Exception as e:
            log_agent_error("Agent error", error=e)
            result = f"Agent encountered an error: {str(e)}"
        finally:
            self.stats["end_time"] = time.time()
            self._print_stats()

        check = assess(user_query, result, self.stats)
        self.last_check = check
        inc_agent_run(check.level)
        log_agent_info(f"Evidence check: {check.level}")
        stamped = attach_footer(result, check)

        if self.config.memory_enabled:
            body, _footer = split_answer(stamped)
            written = append_postmortem(
                query=user_query,
                answer=body,
                tools_used=self.stats.get("tool_calls") or {},
                evidence_level=check.level,
                scenario=scenario,
                path=self._memory_path(),
            )
            if written:
                log_agent_info(f"Postmortem written ({check.level}) → {written}")
            else:
                log_agent_info("Postmortem skipped (evidence LOW)")

        return stamped

    def _memory_path(self) -> Path:
        if self.config.memory_path:
            return Path(self.config.memory_path)
        return MEMORY_DEFAULT_PATH
    
    async def _react_loop(self) -> str:
        for step in range(self.config.max_steps):
            self.stats["total_steps"] = step + 1
            
            log_agent_info(f"\n{'='*80}")
            log_agent_info(f"Step {step + 1}/{self.config.max_steps}")
            log_agent_info(f"{'='*80}")
            
            try:
                # Small / self-hosted models skip the OpenAI tool-call schema and
                # dump XML instead. Until we have live evidence, require a tool.
                force_tools = self.tools and not (self.stats.get("tool_calls") or {})
                response = await self.llm_client.generate_with_tools(
                    messages=self.messages,
                    tools=self.tools,
                    tool_choice="required" if force_tools else None,
                )
            except LLMError as e:
                log_agent_error("LLM error", error=e)
                self.stats["errors"] += 1
                if self.stats["errors"] >= 2:
                    return f"Agent stopped after repeated LLM errors: {e}"
                await asyncio.sleep(0.5)
                continue
            
            if response.get("tool_calls"):
                await self._handle_tool_calls(response["tool_calls"])
                continue
            
            final_answer = response.get("content", "").strip()
            if not final_answer:
                log_agent_error("LLM returned empty response")
                continue

            if not (self.stats.get("tool_calls") or {}) and step < self.config.max_steps - 1:
                log_agent_info("Rejected ungrounded final answer (0 tool calls); retrying")
                self.messages.append({"role": "assistant", "content": final_answer})
                self.messages.append({
                    "role": "user",
                    "content": (
                        "Rejected: you answered without calling any MCP tools. "
                        "Call list_pods first, then get_recent_events / get_pod_logs "
                        "as needed. Do not invent cluster state."
                    ),
                })
                continue

            log_agent_info(f"\nFinal Answer:\n{final_answer}")
            return final_answer
        
        raise MaxStepsExceededError(f"Exceeded {self.config.max_steps} steps")
    
    async def _handle_tool_calls(self, tool_calls: List[Dict[str, Any]]):
        log_agent_info(f"Executing {len(tool_calls)} tool call(s)")
        
        self.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls
        })
        
        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            arguments = tool_call["function"]["arguments"]
            
            log_agent_debug(f"  -> {tool_name}({arguments})")

            if tool_name in GATED_TOOLS:
                interactive = (
                    self.config.writes_interactive
                    if self.config.writes_interactive is not None
                    else stdin_is_tty()
                )
                denied = decide(
                    tool_name,
                    arguments,
                    self.stats,
                    interactive=interactive,
                    approve_fn=self.approve_writes,
                )
                if denied:
                    audit(
                        tool_name=tool_name,
                        arguments=arguments,
                        decision="denied",
                        result=denied,
                    )
                    result = denied
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result,
                    })
                    self.stats["tool_calls"][tool_name] = \
                        self.stats["tool_calls"].get(tool_name, 0) + 1
                    inc_tool_call(tool_name, "denied")
                    log_agent_info(f"  <- gated write blocked: {denied}")
                    continue
            
            result = await self._execute_tool_with_retry(tool_name, arguments)
            result = self._truncate_if_needed(result)

            if tool_name in GATED_TOOLS:
                audit(
                    tool_name=tool_name,
                    arguments=arguments,
                    decision="approved",
                    result=result,
                )
            
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result
            })
            
            self.stats["tool_calls"][tool_name] = \
                self.stats["tool_calls"].get(tool_name, 0) + 1
            
            log_agent_debug(f"  <- Result: {result[:200]}{'...' if len(result) > 200 else ''}")
    
    async def _execute_tool_with_retry(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> str:
        last_error = None
        
        for attempt in range(self.config.max_retries):
            try:
                result = await self.mcp_client.call_tool(tool_name, arguments)
                inc_tool_call(tool_name, "ok")
                return result
                
            except ToolNotFoundError as e:
                log_agent_error("Tool not found", error=e)
                self.stats["errors"] += 1
                inc_tool_call(tool_name, "error")
                return f"ERROR: {str(e)}"
            
            except ToolTimeoutError as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    delay = get_retry_delay(
                        attempt=attempt + 1,
                        base_delay_ms=self.config.base_retry_delay_ms,
                        max_delay_ms=self.config.max_retry_delay_ms,
                        jitter_factor=self.config.retry_jitter_factor
                    )
                    log_agent_error(
                        f"Tool timeout, retrying in {delay:.2f}s... (attempt {attempt + 1}/{self.config.max_retries})",
                        error=e
                    )
                    await asyncio.sleep(delay)
                else:
                    log_agent_error(f"Tool timeout after {self.config.max_retries} attempts")
                    self.stats["errors"] += 1
                    inc_tool_call(tool_name, "error")
                    return f"ERROR: Tool timed out after {self.config.max_retries} attempts"
            
            except ToolExecutionError as e:
                log_agent_error("Tool execution error", error=e)
                self.stats["errors"] += 1
                inc_tool_call(tool_name, "error")
                return f"ERROR: {str(e)}"
            
            except Exception as e:
                log_agent_error("Unexpected error during tool execution", error=e)
                self.stats["errors"] += 1
                inc_tool_call(tool_name, "error")
                return f"ERROR: Unexpected error: {str(e)}"
        
        return f"ERROR: Max retries exceeded. Last error: {last_error}"
    
    def _truncate_if_needed(self, content: str) -> str:
        max_length = self.config.max_content_length
        if len(content) <= max_length:
            return content

        marker = f"\n\n... [TRUNCATED {len(content) - max_length} characters] ...\n\n"
        # Budget the head/tail split around the marker's real length instead of a
        # fixed guess — a fixed guess goes negative for small max_length, which used
        # to make content[-tail_size:] wrap around and return content LONGER than
        # the original (defeating the whole point of truncating it).
        budget = max(0, max_length - len(marker))
        head_size = budget // 2
        tail_size = budget - head_size

        tail = content[-tail_size:] if tail_size > 0 else ""
        truncated = content[:head_size] + marker + tail
        log_agent_debug(f"Truncated content from {len(content)} to {len(truncated)} chars")
        return truncated
    
    def _print_stats(self):
        duration = self.stats["end_time"] - self.stats["start_time"]
        
        print(f"\n{'='*80}")
        print("Agent Statistics")
        print(f"{'='*80}")
        print(f"Duration:     {duration:.2f}s")
        print(f"Total steps:  {self.stats['total_steps']}")
        print(f"Tool calls:   {sum(self.stats['tool_calls'].values())}")
        
        if self.stats['tool_calls']:
            print(f"\n  Tool usage:")
            for tool, count in sorted(self.stats['tool_calls'].items()):
                print(f"    - {tool}: {count}x")
        
        print(f"\n  Errors:       {self.stats['errors']}")
        print(f"{'='*80}\n")
