"""Targeted tools must cite a FOCUS observation. Shared by both engines."""
import asyncio
import json

from agent_cli.agent import ReActAgent
from agent_cli.config import AgentConfig


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    async def generate_with_tools(self, messages, tools, **kwargs):
        return self.responses.pop(0)


class RecordingMCP:
    def __init__(self, results):
        self.results = dict(results)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments or {})))
        return self.results[name]


def tool_call(name, args=None, cid="call_1"):
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": args or {}},
    }


def json_final(statement, *, obs=None, evidence="ev-1", claim_type="observation"):
    claim = {
        "id": "c1",
        "statement": statement,
        "type": claim_type,
        "object": "Pod/x",
        "window": "current",
        "obs": [obs] if obs else [],
        "evidence": [evidence] if evidence else [],
        "confidence": "high",
    }
    return {
        "content": json.dumps({
            "scope": "test",
            "unknowns": [],
            "claims": [claim],
        }),
        "tool_calls": [],
    }


PODS = (
    "Pods in namespace 'citrus' (2 total):\n\n"
    "- accounting-1 | phase=Running | ready=True | restarts=107 | component=accounting\n"
    "- citrus-crashloop-abc | phase=Running | ready=False | restarts=4 | "
    "component=citrus-crashloop | state=CrashLoopBackOff\n"
)


def make_agent(responses, mcp):
    config = AgentConfig(
        api_key="test-key",
        memory_enabled=False,
        writes_interactive=False,
        max_steps=6,
    )
    agent = ReActAgent(config)
    agent.llm_client = ScriptedLLM(responses)
    agent.mcp_client = mcp
    agent.tools = []
    return agent


def test_logs_before_scan_never_reach_mcp():
    mcp = RecordingMCP({"get_pod_logs": "should not run", "list_pods": PODS})
    agent = make_agent(
        [
            {
                "content": None,
                "tool_calls": [tool_call(
                    "get_pod_logs",
                    {"pod_selector": "app.kubernetes.io/component=frontend"},
                    "c1",
                )],
            },
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c2")]},
            json_final("crashloop is the incident.", evidence="ev-1"),
        ],
        mcp,
    )
    asyncio.run(agent.run("what happened to frontend?"))
    assert mcp.calls == [("list_pods", {})]
    denied = [m["content"] for m in agent.messages if m.get("role") == "tool"]
    assert any("DENIED" in text and "FOCUS" in text for text in denied)


def test_from_obs_after_scan_reaches_mcp_and_strips_ledger_id():
    mcp = RecordingMCP({"list_pods": PODS, "get_pod_logs": "crash: exit 1"})
    agent = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            {
                "content": None,
                "tool_calls": [tool_call(
                    "get_pod_logs",
                    {"from_obs": "obs-2", "lines": 20},
                    "c2",
                )],
            },
            json_final(
                "citrus-crashloop is CrashLoopBackOff.",
                obs="obs-2",
                evidence="ev-1",
                claim_type="symptom",
            ),
        ],
        mcp,
    )
    answer = asyncio.run(agent.run("why is something unhealthy?"))
    assert "citrus-crashloop" in answer
    assert mcp.calls[0] == ("list_pods", {})
    assert mcp.calls[1][0] == "get_pod_logs"
    assert mcp.calls[1][1]["pod_selector"] == "app.kubernetes.io/component=citrus-crashloop"
    assert "from_obs" not in mcp.calls[1][1]
    assert "obs-2" in agent.stats["focus"]
    assert any(row["id"] == "obs-2" for row in agent.stats["observations"])
    assert "reference_valid obs-2" in answer
    assert "diagnosis: not_reviewed" in answer
    assert "待确认 obs-2" not in answer


def test_question_named_frontend_cannot_log_it_when_focus_is_crashloop():
    mcp = RecordingMCP({"list_pods": PODS, "get_pod_logs": "nope"})
    agent = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            {
                "content": None,
                "tool_calls": [tool_call(
                    "get_pod_logs",
                    {"pod_selector": "app.kubernetes.io/component=frontend"},
                    "c2",
                )],
            },
            json_final("FOCUS is citrus-crashloop, not frontend.", evidence="ev-1"),
        ],
        mcp,
    )
    asyncio.run(agent.run("what happened to the frontend pods?"))
    assert mcp.calls == [("list_pods", {})]
    denied = [c for c in agent.last_run.tool_calls if c.call_status == "denied"]
    assert denied
    assert denied[0].tool_name == "get_pod_logs"
    assert agent.last_run.tool_calls[0].call_id == "call-1"


def test_consecutive_runs_get_fresh_run_ids_and_reset_evidence():
    mcp = RecordingMCP({"list_pods": PODS})
    agent = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("healthy enough.", evidence="ev-1"),
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c2")]},
            json_final("still looking.", evidence="ev-1"),
        ],
        mcp,
    )
    asyncio.run(agent.run("first"))
    first_id = agent.last_run.run_id
    first_calls = [c.call_id for c in agent.last_run.tool_calls]
    asyncio.run(agent.run("second"))
    assert agent.last_run.run_id != first_id
    assert agent.stats["run_id"] == agent.last_run.run_id
    assert first_calls == ["call-1"]
    assert [c.call_id for c in agent.last_run.tool_calls] == ["call-1"]


def test_from_obs_trace_keeps_requested_id_and_bound_selector():
    mcp = RecordingMCP({"list_pods": PODS, "get_pod_logs": "crash: exit 1"})
    agent = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            {
                "content": None,
                "tool_calls": [tool_call(
                    "get_pod_logs",
                    {"from_obs": "obs-2", "lines": 20},
                    "c2",
                )],
            },
            json_final(
                "citrus-crashloop is CrashLoopBackOff.",
                obs="obs-2",
                evidence="ev-1",
                claim_type="symptom",
            ),
        ],
        mcp,
    )
    asyncio.run(agent.run("why is something unhealthy?"))
    logs = next(c for c in agent.last_run.tool_calls if c.tool_name == "get_pod_logs")
    assert logs.requested_arguments["from_obs"] == "obs-2"
    assert logs.bound_arguments["pod_selector"] == (
        "app.kubernetes.io/component=citrus-crashloop"
    )
    assert "from_obs" not in logs.bound_arguments
    assert logs.call_status == "success"


def test_truncated_tool_output_is_still_in_the_run_package():
    noisy = "HEAD-MARKER" + ("x" * 400) + "NEEDLE-IN-THE-MIDDLE" + ("y" * 400) + "TAIL-MARKER"
    mcp = RecordingMCP({"list_pods": PODS, "get_pod_logs": noisy})
    config = AgentConfig(
        api_key="test-key",
        memory_enabled=False,
        writes_interactive=False,
        max_steps=6,
        max_content_length=80,
    )
    agent = ReActAgent(config)
    agent.llm_client = ScriptedLLM([
        {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
        {
            "content": None,
            "tool_calls": [tool_call(
                "get_pod_logs",
                {"from_obs": "obs-2"},
                "c2",
            )],
        },
        json_final("crashloop.", obs="obs-2", evidence="ev-1", claim_type="symptom"),
    ])
    agent.mcp_client = mcp
    agent.tools = []
    asyncio.run(agent.run("why unhealthy?"))
    logs = next(c for c in agent.last_run.tool_calls if c.tool_name == "get_pod_logs")
    item = agent.last_run.evidence_by_id(logs.evidence_id)
    assert "NEEDLE-IN-THE-MIDDLE" in item.text
    model_msg = [m["content"] for m in agent.messages if m.get("role") == "tool"][-1]
    assert "NEEDLE-IN-THE-MIDDLE" not in model_msg
    assert agent.last_run.generator_seen[-1].truncated_for_viewer is True
