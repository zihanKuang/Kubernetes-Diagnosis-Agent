"""Both eval engines write duration and tokens into one result schema."""
import asyncio
import importlib.util
import json

import pytest

from agent_cli.agent import ReActAgent
from agent_cli.config import AgentConfig
from agent_cli.eval_rca import _save, build_eval_agent
from agent_cli.eval_scenarios import scenario_by_id
from agent_cli.eval_score import score


class MeteredLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.reset_usage()

    def reset_usage(self):
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}

    async def generate_with_tools(self, messages, tools, **kwargs):
        self.usage["llm_calls"] += 1
        self.usage["prompt_tokens"] += 11
        self.usage["completion_tokens"] += 3
        return self.responses.pop(0)


class FakeMCP:
    async def call_tool(self, name, arguments):
        return f"{name}: ok"


def _turn(cid):
    return {
        "content": None,
        "tool_calls": [{
            "id": cid,
            "type": "function",
            "function": {"name": "list_pods", "arguments": {}},
        }],
    }


def _answer():
    return {
        "content": json.dumps({
            "scope": "frontend",
            "unknowns": [],
            "claims": [{
                "id": "c1",
                "statement": "Chaos killed the frontend pod.",
                "type": "observation",
                "object": "Pod/frontend",
                "window": "current",
                "obs": [],
                "evidence": ["ev-1"],
                "confidence": "high",
            }],
        }),
        "tool_calls": [],
    }


def _bind(agent, responses):
    agent.llm_client = MeteredLLM(responses)
    agent.mcp_client = FakeMCP()
    agent.tools = []
    return agent


def test_unknown_engine_is_rejected():
    config = AgentConfig(api_key="test-key", memory_enabled=False)
    with pytest.raises(ValueError):
        build_eval_agent(config, "other")


def test_react_builder_keeps_the_pinned_step_budget():
    config = AgentConfig(api_key="test-key", memory_enabled=False, max_steps=10)
    agent = build_eval_agent(config, "react")
    assert isinstance(agent, ReActAgent)
    assert type(agent).__name__ == "ReActAgent"
    assert agent.config.max_steps == 10


@pytest.mark.parametrize("engine", ["react", "langgraph"])
def test_each_engine_records_duration_and_tokens_on_the_same_fields(engine):
    if engine == "langgraph" and importlib.util.find_spec("langgraph") is None:
        pytest.skip("langgraph extra is not installed")
    config = AgentConfig(
        api_key="test-key",
        memory_enabled=False,
        writes_interactive=False,
        max_steps=4,
    )
    agent = _bind(
        build_eval_agent(config, engine),
        [_turn("c1"), _answer(), _turn("c2"), _answer()],
    )
    scenario = scenario_by_id("pod-kill-frontend")
    query = "What just happened to the frontend pods?"

    first = asyncio.run(agent.run(query))
    scored = score(scenario, first, agent.stats)
    assert scored.duration_s is not None and scored.duration_s >= 0
    assert scored.prompt_tokens == 22
    assert scored.completion_tokens == 6
    assert scored.llm_calls == 2
    assert scored.root_cause_hit is True

    asyncio.run(agent.run(query))
    again = score(scenario, first, agent.stats)
    assert again.prompt_tokens == 22
    assert again.llm_calls == 2


def test_saved_eval_json_keeps_engine_budget_duration_and_tokens(tmp_path):
    scenario = scenario_by_id("pod-kill-frontend")
    result = score(
        scenario,
        "Chaos killed the frontend pod.",
        {
            "tool_calls": {"list_pods": 1},
            "errors": 0,
            "total_steps": 2,
            "start_time": 0.0,
            "end_time": 1.5,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "llm_calls": 2,
        },
    )
    path = _save(
        tmp_path,
        [result],
        extra={
            "engine": "langgraph",
            "model_name": "deepseek-v4-flash",
            "llm_provider": "deepseek",
            "max_steps": 10,
        },
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["engine"] == "langgraph"
    assert payload["model_name"] == "deepseek-v4-flash"
    assert payload["llm_provider"] == "deepseek"
    assert payload["max_steps"] == 10
    assert payload["prompt_tokens"] == 100
    assert payload["completion_tokens"] == 20
    assert payload["llm_calls"] == 2
    row = payload["results"][0]
    assert row["duration_s"] == 1.5
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 20
    assert payload["scoring_rule"] == "phrase_v1"
    assert "not RCA accuracy" in payload["scoring_note"]
    assert payload["phrase_hit_rate"] == payload["hit_rate"]
    assert row["phrase_hit"] is True
    assert row["root_cause_hit_is_legacy"] is True
    assert row["diagnosis_status"] == "not_reviewed"

