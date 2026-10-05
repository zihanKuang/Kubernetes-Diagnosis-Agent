"""LangGraph orchestration engine. Same harness, different loop.

The hand-written ReAct loop (agent.py) stays the default engine. This module
rebuilds only the orchestration as a LangGraph StateGraph:

    reason -> (tool calls?) -> act -> reason -> ... -> END

Tool execution with retries, gated writes + audit, evidence stamping, memory,
and stats are inherited unchanged from ReActAgent. That is the point of the
V3 step: the deterministic harness lives outside the orchestration layer, so
swapping the engine is cheap and the guardrails do not move.

"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, TypedDict

from .agent import ReActAgent
from .config import AgentConfig
from .exceptions import LLMError, MaxStepsExceededError
from .logging_utils import log_agent_error, log_agent_info

try:
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    LANGGRAPH_AVAILABLE = False


class EngineState(TypedDict, total=False):
    response: Dict[str, Any]
    final_answer: Optional[str]
    llm_errors: int


class LangGraphAgent(ReActAgent):
    """ReActAgent with the hand-written loop replaced by a LangGraph StateGraph."""

    def __init__(self, config: AgentConfig):
        if not LANGGRAPH_AVAILABLE:
            raise ImportError(
                "langgraph is not installed — pip install -e \".[langgraph]\" "
                "(the default engine, --engine react, needs nothing extra)"
            )
        super().__init__(config)
        self._graph = self._build_graph()
        log_agent_info("LangGraph engine active (StateGraph orchestration)")

    def _build_graph(self):
        graph = StateGraph(EngineState)
        graph.add_node("reason", self._node_reason)
        graph.add_node("act", self._node_act)
        graph.add_edge(START, "reason")
        graph.add_conditional_edges(
            "reason",
            self._route,
            {"act": "act", "reason": "reason", "end": END},
        )
        graph.add_edge("act", "reason")
        return graph.compile()

    async def _node_reason(self, state: EngineState) -> EngineState:
        if self.stats["total_steps"] >= self.config.max_steps:
            raise MaxStepsExceededError(f"Exceeded {self.config.max_steps} steps")
        self.stats["total_steps"] += 1
        step = self.stats["total_steps"]
        log_agent_info(f"[langgraph] reason step {step}/{self.config.max_steps}")

        try:
            force_tools = self.tools and not (self.stats.get("tool_calls") or {})
            response = await self.llm_client.generate_with_tools(
                messages=self.messages,
                tools=self.tools,
                tool_choice="required" if force_tools else None,
            )
        except LLMError as e:
            log_agent_error("LLM error", error=e)
            self.stats["errors"] += 1
            errors = state.get("llm_errors", 0) + 1
            if errors >= 2:
                return {
                    "response": {},
                    "final_answer": f"Agent stopped after repeated LLM errors: {e}",
                }
            await asyncio.sleep(0.5)
            return {"response": {}, "llm_errors": errors}

        if not response.get("tool_calls"):
            content = (response.get("content") or "").strip()
            if content and (self.stats.get("tool_calls") or {}):
                log_agent_info(f"\n[langgraph] Final Answer:\n{content}")
                return {"response": response, "final_answer": content}
            if content:
                log_agent_info("Rejected ungrounded final answer (0 tool calls); retrying")
                self.messages.append({"role": "assistant", "content": content})
                self.messages.append({
                    "role": "user",
                    "content": (
                        "Rejected: you answered without calling any MCP tools. "
                        "Call list_pods first. Do not invent cluster state."
                    ),
                })
            else:
                log_agent_error("LLM returned empty response")
        return {"response": response}

    def _route(self, state: EngineState) -> str:
        if state.get("final_answer"):
            return "end"
        if (state.get("response") or {}).get("tool_calls"):
            return "act"
        return "reason"

    async def _node_act(self, state: EngineState) -> EngineState:
        # Inherited from ReActAgent: gated writes, audit, retry, truncation,
        # tool metrics — the harness is identical under both engines.
        await self._handle_tool_calls(state["response"]["tool_calls"])
        return {}

    async def _react_loop(self) -> str:
        try:
            final_state = await self._graph.ainvoke(
                {"llm_errors": 0},
                config={"recursion_limit": self.config.max_steps * 2 + 2},
            )
        except GraphRecursionError as e:
            raise MaxStepsExceededError(
                f"Exceeded {self.config.max_steps} steps (graph recursion limit)"
            ) from e
        answer = (final_state or {}).get("final_answer")
        if not answer:
            raise MaxStepsExceededError(f"Exceeded {self.config.max_steps} steps")
        return answer
