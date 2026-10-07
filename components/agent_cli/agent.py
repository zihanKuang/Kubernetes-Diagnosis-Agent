"""ReAct agent: LLM reasoning loop + MCP tool calls."""
import asyncio
import copy
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from .config import AgentConfig
from .diagnosis import publish_diagnosis
from .evidence import assess, attach_footer, parse_recovery_verdict, split_answer
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
from .observations.scope import scope_from_ledger, tagged_rows
from .observations import (
    SCAN_TOOLS,
    ObservationLedger,
    annotate_target_tools,
)
from .retry_utils import get_retry_delay
from .logging_utils import log_agent_debug, log_agent_info, log_agent_error
from .exceptions import (
    MaxStepsExceededError,
    ToolExecutionError,
    ToolTimeoutError,
    ToolNotFoundError,
    LLMError
)
from .evidence_models import ToolPayload
from .skills.runtime import SkillRuntime
from .trace_store import (
    CALL_DENIED,
    CALL_ERROR,
    CALL_SUCCESS,
    CALL_TIMEOUT,
    TraceSession,
    save_run,
    utc_now,
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
        self.tool_catalog: List[Dict[str, Any]] = []
        self.ledger = ObservationLedger()
        self.stats = self._empty_stats()
        self.last_check = None
        self.trace = None
        self.last_run = None
        # Tests inject a fake approver. Interactive CLI uses stdin y/n.
        self.approve_writes = None
        self.engine_name = "react"
        self.skill = SkillRuntime(config)
        self._busy = threading.Lock()
        self._cancelled = False

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
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "llm_calls": 0,
            "focus": [],
            "query": "",
            "diagnosis": {},
            "tool_ok": {},
            "tool_denied": {},
            "tool_error": {},
            "skill": {},
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
        
        await self.mcp_client.connect()
        self.tool_catalog = self.mcp_client.get_tools()
        annotate_target_tools(self.tool_catalog)
        self.tools = copy.deepcopy(self.tool_catalog)
        log_agent_info(f"Agent ready with {len(self.tool_catalog)} tools")
    
    async def cleanup(self):
        """Cleanup resources"""
        await self.mcp_client.disconnect()
    
    def cancel(self) -> None:
        self._cancelled = True
        self.skill.cancel()

    async def run(
        self,
        user_query: str,
        *,
        scenario: str = "unknown",
        entrypoint: str = "cli",
        request_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Run ReAct loop for user query.

        `scenario` is an eval/fault label stored with the postmortem only.
        It is not used for skill routing.
        """
        if not self._busy.acquire(blocking=False):
            raise RuntimeError(
                "ReActAgent.run is not concurrent on one instance; serialize callers"
            )
        try:
            return await self._run_query(
                user_query,
                scenario=scenario,
                entrypoint=entrypoint,
                request_meta=request_meta,
            )
        finally:
            self._busy.release()

    async def _run_query(
        self,
        user_query: str,
        *,
        scenario: str,
        entrypoint: str,
        request_meta: Optional[Dict[str, Any]],
    ) -> str:
        # fresh stats each query (interactive mode)
        self.stats = self._empty_stats()
        self.stats["start_time"] = time.time()
        self.stats["query"] = user_query
        self.last_check = None
        self.ledger.reset()
        self.ledger.query = user_query
        self.trace = TraceSession(user_query, self.config)
        self.last_run = self.trace.run
        self.stats["run_id"] = self.trace.run.run_id
        reset_usage = getattr(self.llm_client, "reset_usage", None)
        if reset_usage is not None:
            reset_usage()

        self._cancelled = False
        meta = dict(request_meta or {})
        self.skill.begin(
            user_query,
            entrypoint=entrypoint,
            engine=self.engine_name,
            named_workloads=meta.get("named_workloads"),
            alert_labels=meta.get("alert_labels"),
            catalog_names=self._catalog_names(),
        )

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
            self._snapshot_llm_usage()
            self._snapshot_ledger()
            self.stats["skill"] = self.skill.trace_payload()
            self._print_stats()

        evidence_ids = []
        opener = None
        run_id = ""
        if self.trace is not None:
            evidence_ids = [item.evidence_id for item in self.trace.run.evidence]
            opener = self.trace.open_locator
            run_id = self.trace.run.run_id
        diagnosis, published = publish_diagnosis(
            result,
            observations=self.stats.get("observations") or [],
            evidence_ids=evidence_ids,
            run_id=run_id,
            open_locator=opener,
        )
        self.stats["diagnosis"] = diagnosis.as_dict()
        check = assess(user_query, published, self.stats)
        self.last_check = check
        inc_agent_run(check.level)
        log_agent_info(f"Evidence check: {check.level}")
        stamped = attach_footer(published, check)
        if self.trace is not None:
            self.last_run = self.trace.finalize(
                draft=result,
                final=stamped,
                check=check,
                stats=self.stats,
                diagnosis=diagnosis.as_dict(),
            )
            if self.config.trace_dir:
                path = save_run(self.last_run, Path(self.config.trace_dir))
                log_agent_info(f"Run package {self.last_run.run_id} → {path}")

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

    def _snapshot_llm_usage(self) -> None:
        """Copy this run's model usage into stats. Both engines share run()."""
        usage = getattr(self.llm_client, "usage", None) or {}
        self.stats["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
        self.stats["completion_tokens"] = int(usage.get("completion_tokens") or 0)
        self.stats["llm_calls"] = int(usage.get("llm_calls") or 0)

    def _snapshot_ledger(self) -> None:
        """FOCUS ids plus serializable rows so assess() can bind citations."""
        query = self.stats.get("query") or self.ledger.query
        scope = scope_from_ledger(query, self.ledger)
        focus = self.ledger.focus_objects()
        self.stats["focus"] = [obs.id for obs in focus]
        self.stats["incident_scope"] = scope.as_dict()
        self.stats["observations"] = tagged_rows(focus, scope)

    def _memory_path(self) -> Path:
        if self.config.memory_path:
            return Path(self.config.memory_path)
        return MEMORY_DEFAULT_PATH

    def _catalog_names(self) -> List[str]:
        names = []
        for item in self.tool_catalog or self.tools or []:
            fn = item.get("function") or {}
            name = fn.get("name") or item.get("name") or ""
            if name:
                names.append(name)
        return names

    def _model_call_args(self) -> tuple[list, Optional[str], Optional[str]]:
        catalog = copy.deepcopy(self.tool_catalog or self.tools)
        annotate_target_tools(catalog)
        if self.skill.auto():
            tools = self.skill.tools_for_model(catalog)
            force = bool(tools) and self.skill.force_tools(bool(self.stats.get("tool_calls")))
            instruction = self.skill.system_instruction()
        else:
            tools = catalog
            force = bool(tools) and not (self.stats.get("tool_calls") or {})
            instruction = None
        self.tools = tools
        return tools, ("required" if force else None), instruction

    def _count_status(self, bucket: str, tool_name: str) -> None:
        store = self.stats.setdefault(bucket, {})
        store[tool_name] = store.get(tool_name, 0) + 1

    async def _react_loop(self) -> str:
        for step in range(self.config.max_steps):
            if self._cancelled or self.skill.cancelled():
                return "Agent run cancelled."
            self.stats["total_steps"] = step + 1
            self.skill.note_step()

            log_agent_info(f"\n{'='*80}")
            log_agent_info(f"Step {step + 1}/{self.config.max_steps}")
            log_agent_info(f"{'='*80}")

            try:
                tools, tool_choice, instruction = self._model_call_args()
                response = await self.llm_client.generate_with_tools(
                    messages=self.messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    system_instruction=instruction,
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

            if self.skill.auto():
                if not self.skill.allow_final_answer() and step < self.config.max_steps - 1:
                    log_agent_info("Rejected early final answer; current skill stage still requires tools")
                    self.messages.append({"role": "assistant", "content": final_answer})
                    self.messages.append({
                        "role": "user",
                        "content": (
                            "Rejected: the current investigation stage still requires tool evidence. "
                            "Call the provided tools. Do not invent cluster state."
                        ),
                    })
                    continue
            elif not (self.stats.get("tool_calls") or {}) and step < self.config.max_steps - 1:
                log_agent_info("Rejected ungrounded final answer (0 tool calls); retrying")
                self.messages.append({"role": "assistant", "content": final_answer})
                self.messages.append({
                    "role": "user",
                    "content": (
                        "Rejected: you answered without calling any MCP tools. "
                        "Call list_pods first. Targeted logs/status are locked "
                        "until a FOCUS object exists. Do not invent cluster state."
                    ),
                })
                continue

            log_agent_info(f"\nFinal Answer:\n{final_answer}")
            return final_answer

        raise MaxStepsExceededError(f"Exceeded {self.config.max_steps} steps")
    
    async def _handle_tool_calls(self, tool_calls: List[Dict[str, Any]]):
        log_agent_info(f"Executing {len(tool_calls)} tool call(s)")
        self.skill.begin_batch()

        self.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls
        })

        for tool_call in tool_calls:
            if self._cancelled or self.skill.cancelled():
                break
            tool_name = tool_call["function"]["name"]
            arguments = dict(tool_call["function"].get("arguments") or {})

            log_agent_debug(f"  -> {tool_name}({arguments})")

            skill_denied = self.skill.authorize(tool_name)
            if skill_denied:
                self._append_denied(tool_call, tool_name, arguments, skill_denied)
                continue

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
                    evidence_id = ""
                    if self.trace is not None:
                        record = self.trace.record_denied(tool_name, arguments, denied)
                        evidence_id = record.evidence_id
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": _with_evidence(denied, evidence_id),
                    })
                    self.stats["tool_calls"][tool_name] = \
                        self.stats["tool_calls"].get(tool_name, 0) + 1
                    self._count_status("tool_denied", tool_name)
                    inc_tool_call(tool_name, "denied")
                    self.skill.record_outcome(tool_name, "denied", evidence_id=evidence_id, text=denied)
                    log_agent_info(f"  <- gated write blocked: {denied}")
                    continue

            target_denied = self.ledger.authorize(tool_name, arguments)
            if target_denied:
                evidence_id = ""
                if self.trace is not None:
                    record = self.trace.record_denied(tool_name, arguments, target_denied)
                    evidence_id = record.evidence_id
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": _with_evidence(target_denied, evidence_id),
                })
                inc_tool_call(tool_name, "denied")
                self._count_status("tool_denied", tool_name)
                self.skill.record_outcome(tool_name, "denied", evidence_id=evidence_id, text=target_denied)
                log_agent_info(f"  <- targeted tool blocked: {target_denied}")
                continue

            call_args = self.ledger.bind_arguments(arguments)
            started = utc_now()
            payload = await self._execute_tool_with_retry(tool_name, call_args)
            ended = utc_now()
            ok = payload.call_status == CALL_SUCCESS and not payload.is_error
            if ok and (payload.text or "").lstrip().lower().startswith(("error", "unexpected error")):
                ok = False
            if ok and tool_name == "validate_recovery":
                self.stats["recovery"] = parse_recovery_verdict(payload.text)
            if ok and tool_name in SCAN_TOOLS:
                self.ledger.ingest(tool_name, payload.text)
                self._snapshot_ledger()
            visible, ranges = self._truncate_with_ranges(payload.text)
            to_model = visible
            if ok and tool_name in SCAN_TOOLS:
                to_model = visible.rstrip() + "\n\n" + self.ledger.catalog()
            evidence_id = ""
            if self.trace is not None:
                record = self.trace.record_payload(
                    tool_name=tool_name,
                    requested=arguments,
                    bound=call_args,
                    payload=payload,
                    visible_text=visible,
                    visible_ranges=ranges,
                    started_at=started,
                    ended_at=ended,
                )
                evidence_id = record.evidence_id
            to_model = _with_evidence(to_model, evidence_id)

            if tool_name in GATED_TOOLS:
                audit(
                    tool_name=tool_name,
                    arguments=arguments,
                    decision="approved",
                    result=to_model,
                )

            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": to_model,
            })

            self.stats["tool_calls"][tool_name] = \
                self.stats["tool_calls"].get(tool_name, 0) + 1
            if payload.call_status == CALL_DENIED:
                status = "denied"
                self._count_status("tool_denied", tool_name)
            elif ok:
                status = "success"
                self._count_status("tool_ok", tool_name)
            else:
                status = "timeout" if payload.call_status == CALL_TIMEOUT else "error"
                self._count_status("tool_error", tool_name)
            obs_id = str(arguments.get("from_obs") or "")
            object_name = str(call_args.get("pod_selector") or "")
            if obs_id:
                observed = self.ledger.by_id(obs_id)
                if observed is not None:
                    object_name = observed.name
            self.skill.record_outcome(
                tool_name,
                status,
                evidence_id=evidence_id,
                text=payload.text,
                obs_id=obs_id,
                object_name=object_name,
            )

            log_agent_debug(
                f"  <- Result: {to_model[:200]}{'...' if len(to_model) > 200 else ''}"
            )

        self.skill.after_batch(self.ledger, catalog_names=self._catalog_names())

    def _append_denied(self, tool_call, tool_name, arguments, denied: str) -> None:
        evidence_id = ""
        if self.trace is not None:
            record = self.trace.record_denied(tool_name, arguments, denied)
            evidence_id = record.evidence_id
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": _with_evidence(denied, evidence_id),
        })
        self.stats["tool_calls"][tool_name] = \
            self.stats["tool_calls"].get(tool_name, 0) + 1
        self._count_status("tool_denied", tool_name)
        inc_tool_call(tool_name, "denied")
        self.skill.record_outcome(tool_name, "denied", evidence_id=evidence_id, text=denied)
        log_agent_info(f"  <- skill tool blocked: {denied}") 
    async def _execute_tool_with_retry(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> ToolPayload:
        last_error = None

        for attempt in range(self.config.max_retries):
            try:
                payload = await self._invoke_mcp(tool_name, arguments)
                if payload.is_error or payload.call_status != CALL_SUCCESS:
                    self.stats["errors"] += 1
                    inc_tool_call(tool_name, "error")
                    return payload
                inc_tool_call(tool_name, "ok")
                return payload

            except ToolNotFoundError as e:
                log_agent_error("Tool not found", error=e)
                self.stats["errors"] += 1
                inc_tool_call(tool_name, "error")
                return ToolPayload(
                    text=f"ERROR: {str(e)}",
                    call_status=CALL_ERROR,
                    is_error=True,
                )

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
                    return ToolPayload(
                        text=f"ERROR: Tool timed out after {self.config.max_retries} attempts",
                        call_status=CALL_TIMEOUT,
                        is_error=True,
                    )

            except ToolExecutionError as e:
                log_agent_error("Tool execution error", error=e)
                self.stats["errors"] += 1
                inc_tool_call(tool_name, "error")
                return ToolPayload(
                    text=f"ERROR: {str(e)}",
                    call_status=CALL_ERROR,
                    is_error=True,
                )

            except Exception as e:
                log_agent_error("Unexpected error during tool execution", error=e)
                self.stats["errors"] += 1
                inc_tool_call(tool_name, "error")
                return ToolPayload(
                    text=f"ERROR: Unexpected error: {str(e)}",
                    call_status=CALL_ERROR,
                    is_error=True,
                )

        return ToolPayload(
            text=f"ERROR: Max retries exceeded. Last error: {last_error}",
            call_status=CALL_ERROR,
            is_error=True,
        )

    async def _invoke_mcp(self, tool_name: str, arguments: Dict[str, Any]) -> ToolPayload:
        if self._cancelled or self.skill.cancelled():
            raise ToolExecutionError("run cancelled")
        budget_denied = self.skill.consume_tool_budget()
        if budget_denied:
            return ToolPayload(text=budget_denied, call_status=CALL_DENIED, is_error=True)
        detailed = getattr(self.mcp_client, "call_tool_result", None)
        if detailed is not None:
            return await detailed(tool_name, arguments)
        text = await self.mcp_client.call_tool(tool_name, arguments)
        return ToolPayload(text=text, call_status=CALL_SUCCESS)

    def _truncate_if_needed(self, content: str) -> str:
        return self._truncate_with_ranges(content)[0]

    def _truncate_with_ranges(self, content: str) -> tuple[str, list[tuple[int, int]]]:
        max_length = self.config.max_content_length
        if len(content) <= max_length:
            return content, [(0, len(content))]

        marker = f"\n\n... [TRUNCATED {len(content) - max_length} characters] ...\n\n"
        budget = max(0, max_length - len(marker))
        head_size = budget // 2
        tail_size = budget - head_size
        tail = content[-tail_size:] if tail_size > 0 else ""
        truncated = content[:head_size] + marker + tail
        ranges: list[tuple[int, int]] = []
        if head_size > 0:
            ranges.append((0, head_size))
        if tail_size > 0:
            ranges.append((len(content) - tail_size, len(content)))
        log_agent_debug(f"Truncated content from {len(content)} to {len(truncated)} chars")
        return truncated, ranges
    
    def _print_stats(self):
        duration = self.stats["end_time"] - self.stats["start_time"]
        
        print(f"\n{'='*80}")
        print("Agent Statistics")
        print(f"{'='*80}")
        prompt = int(self.stats.get("prompt_tokens") or 0)
        completion = int(self.stats.get("completion_tokens") or 0)
        print(f"Duration:     {duration:.2f}s")
        print(f"Total steps:  {self.stats['total_steps']}")
        print(f"Tool calls:   {sum(self.stats['tool_calls'].values())}")
        print(f"Tokens:       {prompt} prompt + {completion} completion")
        
        if self.stats['tool_calls']:
            print(f"\n  Tool usage:")
            for tool, count in sorted(self.stats['tool_calls'].items()):
                print(f"    - {tool}: {count}x")
        
        print(f"\n  Errors:       {self.stats['errors']}")
        print(f"{'='*80}\n")


def _with_evidence(text: str, evidence_id: str) -> str:
    if not evidence_id:
        return text
    return f"[evidence {evidence_id}]\n{text}"
