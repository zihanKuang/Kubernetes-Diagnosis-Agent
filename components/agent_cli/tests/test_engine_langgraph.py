"""LangGraph engine: swapped orchestration, identical harness (gates, evidence, stats)."""
import asyncio
import json

import pytest

pytest.importorskip("langgraph")

from agent_cli import agent as agent_module
from agent_cli.config import AgentConfig
from agent_cli.engine_langgraph import LangGraphAgent


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    async def generate_with_tools(self, messages, tools, **kwargs):
        return self.responses.pop(0)


class FakeMCP:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        return f"{name}: ok"


def tool_call(name, args=None, cid="call_1"):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": args or {}},
    }


def make_agent(responses, max_steps=4):
    config = AgentConfig(
        api_key="test-key",
        memory_enabled=False,
        writes_interactive=False,
        max_steps=max_steps,
    )
    agent = LangGraphAgent(config)
    agent.llm_client = ScriptedLLM(responses)
    agent.mcp_client = FakeMCP()
    agent.tools = []
    return agent


def test_tool_call_then_final_answer_with_evidence_footer():
    payload = json.dumps({
        "scope": "frontend",
        "unknowns": [],
        "claims": [{
            "id": "c1",
            "statement": "frontend pods are Ready.",
            "type": "observation",
            "object": "Pod/frontend",
            "window": "current",
            "obs": [],
            "evidence": ["ev-1"],
            "confidence": "high",
        }],
    })
    agent = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods")]},
            {"content": payload, "tool_calls": []},
        ]
    )
    answer = asyncio.run(agent.run("status of frontend pods?"))
    assert "frontend pods are Ready." in answer
    assert "Evidence check:" in answer  # deterministic stamp survives the engine swap
    assert agent.stats["tool_calls"] == {"list_pods": 1}
    assert agent.stats["total_steps"] == 2
    assert agent.mcp_client.calls == ["list_pods"]


def test_max_steps_is_enforced():
    endless = [
        {"content": None, "tool_calls": [tool_call("list_pods", cid=f"c{i}")]}
        for i in range(10)
    ]
    agent = make_agent(endless, max_steps=3)
    answer = asyncio.run(agent.run("loop forever"))
    assert "maximum reasoning steps" in answer


def test_gated_write_is_still_denied(monkeypatch):
    monkeypatch.setattr(agent_module, "audit", lambda **kwargs: None)
    agent = make_agent(
        [
            {
                "content": None,
                "tool_calls": [tool_call("restart_deployment", {"name": "frontend"})],
            },
            {"content": json.dumps({
                "scope": "frontend",
                "unknowns": [],
                "claims": [{
                    "id": "c1",
                    "statement": "write was denied; gather evidence first.",
                    "type": "observation",
                    "object": "Pod/frontend",
                    "window": "current",
                    "obs": [],
                    "evidence": ["ev-1"],
                    "confidence": "low",
                }],
            }), "tool_calls": []},
        ]
    )
    asyncio.run(agent.run("restart frontend now"))
    # the gated write never reached MCP: no live evidence + non-interactive
    assert agent.mcp_client.calls == []
    tool_messages = [m for m in agent.messages if m["role"] == "tool"]
    assert any("DENIED" in m["content"] for m in tool_messages)
