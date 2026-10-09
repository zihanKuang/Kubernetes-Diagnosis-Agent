"""Offline Skill runtime paths through ReActAgent. Fake LLM + fake MCP."""
import asyncio
import json
from pathlib import Path

import pytest

from agent_cli.agent import ReActAgent
from agent_cli.config import AgentConfig
from agent_cli.engine_langgraph import LANGGRAPH_AVAILABLE
from agent_cli.skills.registry import SkillRegistry
from agent_cli.skills.workflows import compile_workflow

FIXTURES = Path(__file__).parent / "fixtures" / "skills"

HEALTHY_PODS = (
    "Pods in namespace 'citrus' (2 total):\n\n"
    "- accounting-1 | phase=Running | ready=True | restarts=1 | component=accounting\n"
    "- frontend-1 | phase=Running | ready=True | restarts=0 | component=frontend\n"
)

CRASH_PODS = (
    "Pods in namespace 'citrus' (2 total):\n\n"
    "- accounting-1 | phase=Running | ready=True | restarts=1 | component=accounting\n"
    "- citrus-crashloop-abc | phase=Running | ready=False | restarts=4 | "
    "component=citrus-crashloop | state=CrashLoopBackOff\n"
)

OOM_PODS = (
    "Pods in namespace 'citrus' (1 total):\n\n"
    "- citrus-oom-abc | phase=Running | ready=False | restarts=3 | "
    "component=citrus-oom | state=CrashLoopBackOff\n"
)

OOM_STATUS = (
    "Pod: citrus-oom-abc\n"
    "  Status: Running\n"
    "  Ready: False\n"
    "  State: OOMKilled\n"
    "  Container main: waiting=CrashLoopBackOff last_termination=OOMKilled(exit=137)\n"
)

IMAGE_PODS = (
    "Pods in namespace 'citrus' (1 total):\n\n"
    "- citrus-imagepull-abc | phase=Pending | ready=False | restarts=0 | "
    "component=citrus-imagepull | state=ImagePullBackOff\n"
)

PROBE_PODS = (
    "Pods in namespace 'citrus' (1 total):\n\n"
    "- citrus-probe-abc | phase=Running | ready=False | restarts=0 | "
    "component=citrus-probe | state=RunningNotReady\n"
)

SCHED_PODS = (
    "Pods in namespace 'citrus' (1 total):\n\n"
    "- citrus-unschedulable-abc | phase=Pending | ready=False | restarts=0 | "
    "component=citrus-unschedulable | state=Unschedulable\n"
)


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_tools = []
        self.seen_instructions = []

    async def generate_with_tools(self, messages, tools, **kwargs):
        names = [
            (item.get("function") or {}).get("name")
            for item in (tools or [])
        ]
        self.seen_tools.append(names)
        self.seen_instructions.append(kwargs.get("system_instruction"))
        return self.responses.pop(0)


class RecordingMCP:
    def __init__(self, results):
        self.results = dict(results)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments or {})))
        if name not in self.results:
            return f"ERROR: unexpected {name}"
        value = self.results[name]
        if callable(value):
            return value(arguments)
        return value


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
        "content": json.dumps({"scope": "test", "unknowns": [], "claims": [claim]}),
        "tool_calls": [],
    }


EVENTS_NONE = (
    "Events in last 10m (namespace=citrus, "
    "collected_at=2026-03-15T12:00:00+00:00, "
    "window_start=2026-03-15T11:50:00+00:00):\n"
    "TIME\tTYPE\tREASON\tOBJECT\tMESSAGE\tFIRST\tCOUNT\n"
)


def rca_turns(statement, *, obs="obs-1"):
    return [
        {
            "content": None,
            "tool_calls": [
                tool_call("list_pods", {}, "c1"),
                tool_call("get_recent_events", {}, "c1e"),
            ],
        },
        {
            "content": None,
            "tool_calls": [tool_call("get_pod_status", {"from_obs": obs}, "c2")],
        },
        {
            "content": None,
            "tool_calls": [tool_call("get_pod_logs", {"from_obs": obs}, "c3")],
        },
        {
            "content": None,
            "tool_calls": [tool_call("validate_recovery", {"from_obs": obs}, "c4")],
        },
        json_final(statement, obs=obs),
    ]


def catalog():
    names = [
        "list_pods", "get_recent_events", "get_pod_status", "get_pod_logs",
        "validate_recovery", "query_prometheus", "restart_deployment",
        "scale_deployment", "rollback_deployment",
    ]
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def make_agent(responses, mcp, *, routing="auto", extra_dirs=None, engine="react"):
    config = AgentConfig(
        api_key="test-key",
        memory_enabled=False,
        writes_interactive=False,
        max_steps=8,
        skill_routing=routing,
        skill_extra_dirs=extra_dirs,
    )
    if engine == "langgraph":
        from agent_cli.engine_langgraph import LangGraphAgent
        agent = LangGraphAgent(config)
    else:
        agent = ReActAgent(config)
    llm = ScriptedLLM(responses)
    agent.llm_client = llm
    agent.mcp_client = mcp
    agent.tool_catalog = catalog()
    agent.tools = catalog()
    agent._llm = llm
    return agent, llm


def test_legacy_does_not_load_unselected_skill_text():
    mcp = RecordingMCP({"list_pods": HEALTHY_PODS})
    agent, llm = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("frontend pods are Ready."),
        ],
        mcp,
        routing="legacy",
    )
    asyncio.run(agent.run("Is anything unhealthy?", entrypoint="cli"))
    assert agent.skill.loader.reads == {}
    assert llm.seen_instructions[0] is None


def test_healthy_path_no_deep_inspect_or_writes():
    mcp = RecordingMCP({"list_pods": HEALTHY_PODS, "get_pod_status": "nope", "restart_deployment": "nope"})
    agent, llm = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("No current FOCUS; pods in this scan look Ready."),
        ],
        mcp,
    )
    answer = asyncio.run(agent.run("Is anything unhealthy in citrus right now?", entrypoint="cli"))
    assert mcp.calls == [("list_pods", {})]
    assert agent.stats["skill"]["skill_id"] == "healthy"
    assert "restart_deployment" not in (llm.seen_tools[0] or [])
    assert "Evidence check:" in answer


def test_hidden_write_never_reaches_mcp():
    mcp = RecordingMCP({"list_pods": CRASH_PODS, "restart_deployment": "should not run"})
    agent, llm = make_agent(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call("list_pods", {}, "c1"),
                    tool_call("restart_deployment", {"name": "frontend"}, "c2"),
                ],
            },
            json_final("scan only"),
        ],
        mcp,
    )
    asyncio.run(agent.run("what happened?", entrypoint="cli"))
    assert mcp.calls == [("list_pods", {})]
    assert agent.stats["tool_denied"].get("restart_deployment", 0) >= 1
    assert "restart_deployment" not in (agent.stats.get("tool_ok") or {})


def test_same_batch_cannot_gain_permission_from_first_call():
    mcp = RecordingMCP({
        "list_pods": CRASH_PODS,
        "get_pod_status": "State: CrashLoopBackOff",
    })
    agent, _llm = make_agent(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call("list_pods", {}, "c1"),
                    tool_call("get_pod_status", {"from_obs": "obs-2"}, "c2"),
                ],
            },
            json_final("still scanning"),
        ],
        mcp,
    )
    asyncio.run(agent.run("why is it unhealthy?", entrypoint="cli"))
    assert mcp.calls == [("list_pods", {})]
    assert agent.stats["tool_denied"].get("get_pod_status", 0) >= 1


def test_crashloop_then_oom_status_switches_skill():
    mcp = RecordingMCP({
        "list_pods": OOM_PODS,
        "get_recent_events": EVENTS_NONE,
        "get_pod_status": OOM_STATUS,
        "get_pod_logs": "no prior logs",
        "validate_recovery": "status=fail\nreason=not_ready\n",
    })
    agent, llm = make_agent(
        rca_turns(
            "citrus-oom last_termination is OOMKilled.",
            obs="obs-1",
        ),
        mcp,
    )
    asyncio.run(agent.run("What is wrong with the citrus-oom workload?", entrypoint="cli"))
    history = [item["skill_id"] for item in agent.stats["skill"]["route_history"]]
    assert "generic-triage" in history
    assert agent.stats["skill"]["skill_id"] == "oom"
    assert "get_pod_status" in (llm.seen_tools[1] or [])
    loaded = agent.stats["skill"]["loaded_instructions"]
    assert "oom" in loaded
    assert "imagepull" not in loaded


@pytest.mark.parametrize("pods,expected", [
    (CRASH_PODS, "crashloop"),
    (IMAGE_PODS, "imagepull"),
    (PROBE_PODS, "readiness"),
    (SCHED_PODS, "scheduling"),
])
def test_five_classes_select_inspect_skill(pods, expected):
    obs = "obs-2" if expected == "crashloop" else "obs-1"
    mcp = RecordingMCP({
        "list_pods": pods,
        "get_recent_events": EVENTS_NONE,
        "get_pod_status": "Pod: x\n  State: x\n",
        "get_pod_logs": "log",
        "validate_recovery": "status=fail",
    })
    agent, _llm = make_agent(
        rca_turns("investigating", obs=obs),
        mcp,
    )
    asyncio.run(agent.run("Why is the workload unhealthy? Short RCA.", entrypoint="cli"))
    assert agent.stats["skill"]["skill_id"] == expected


def test_scan_timeout_is_unknown_not_healthy():
    mcp = RecordingMCP({"list_pods": "ERROR: timed out talking to apiserver"})
    agent, _llm = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("scan failed; state unknown."),
        ],
        mcp,
    )
    asyncio.run(agent.run("Is anything unhealthy?", entrypoint="cli"))
    assert agent.stats["skill"]["skill_id"] != "healthy"
    assert agent.stats["skill"]["unknown_reason"] or agent.stats["skill"]["skill_id"] == "generic-triage"


def test_replace_example_changes_inspect_tools():
    registry = SkillRegistry(extra_dirs=[str(FIXTURES / "crashloop-status-only")])
    builtin = compile_workflow(registry.get("crashloop").workflow, registry, skill_id="crashloop")
    replaced = compile_workflow(
        registry.get("crashloop-status-only").workflow,
        registry,
        skill_id="crashloop-status-only",
    )
    b_inspect = next(s for s in builtin.stages if s.spec.id == "inspect")
    r_inspect = next(s for s in replaced.stages if s.spec.id == "inspect")
    assert "get_pod_logs" in (b_inspect.spec.optional_tools + b_inspect.spec.tools)
    assert r_inspect.spec.tools == ["get_pod_status"]
    mcp = RecordingMCP({
        "list_pods": CRASH_PODS,
        "get_recent_events": EVENTS_NONE,
        "get_pod_status": "Pod: citrus-crashloop-abc\n  State: CrashLoopBackOff\n",
        "validate_recovery": "status=fail",
    })
    agent, llm = make_agent(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call("list_pods", {}, "c1"),
                    tool_call("get_recent_events", {}, "c1e"),
                ],
            },
            {
                "content": None,
                "tool_calls": [tool_call("get_pod_status", {"from_obs": "obs-2"}, "c2")],
            },
            {
                "content": None,
                "tool_calls": [tool_call("get_pod_status", {"from_obs": "obs-2"}, "c3")],
            },
            {
                "content": None,
                "tool_calls": [tool_call("validate_recovery", {"from_obs": "obs-2"}, "c4")],
            },
            json_final("status only inspect"),
        ],
        mcp,
        extra_dirs=[str(FIXTURES / "crashloop-status-only")],
    )
    asyncio.run(agent.run("Why is citrus-crashloop unhealthy?", entrypoint="cli"))
    inspect_turns = [names for names in llm.seen_tools if "get_pod_status" in names]
    assert inspect_turns
    assert all("get_pod_logs" not in names for names in inspect_turns)


def test_auto_then_legacy_is_a_config_switch():
    mcp_auto = RecordingMCP({"list_pods": HEALTHY_PODS})
    auto, _llm_auto = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("No current FOCUS; pods in this scan look Ready."),
        ],
        mcp_auto,
        routing="auto",
    )
    asyncio.run(auto.run("Is anything unhealthy?", entrypoint="cli"))
    assert auto.stats["skill"]["skill_id"] == "healthy"
    assert auto.stats["skill"]["routing_mode"] == "auto"

    mcp_legacy = RecordingMCP({"list_pods": HEALTHY_PODS})
    legacy, llm_legacy = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("frontend pods are Ready."),
        ],
        mcp_legacy,
        routing="legacy",
    )
    asyncio.run(legacy.run("Is anything unhealthy?", entrypoint="cli"))
    assert legacy.config.skill_routing == "legacy"
    assert llm_legacy.seen_instructions[0] is None
    assert legacy.skill.loader.reads == {}
    assert SkillRegistry().get("healthy").meta.id == "healthy"


def test_second_run_does_not_keep_previous_skill():
    mcp = RecordingMCP({
        "list_pods": CRASH_PODS,
        "get_recent_events": EVENTS_NONE,
        "get_pod_status": "Pod: citrus-crashloop-abc\n  State: CrashLoopBackOff\n",
        "get_pod_logs": "exit 1",
        "validate_recovery": "status=fail\n",
    })
    agent, llm = make_agent(
        rca_turns("crashloop", obs="obs-2") + [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c9")]},
            json_final("healthy now"),
        ],
        mcp,
    )
    asyncio.run(agent.run("Why is citrus-crashloop unhealthy?", entrypoint="cli"))
    assert agent.stats["skill"]["skill_id"] == "crashloop"
    mcp.results["list_pods"] = HEALTHY_PODS
    asyncio.run(agent.run("Is anything unhealthy?", entrypoint="interactive"))
    assert agent.stats["skill"]["skill_id"] == "healthy"
    assert agent.stats["query"] == "Is anything unhealthy?"
    assert llm.seen_instructions[-1] is not None


def test_concurrent_run_raises():
    mcp = RecordingMCP({"list_pods": HEALTHY_PODS})
    agent, _llm = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("ok"),
        ],
        mcp,
        routing="legacy",
    )
    agent._busy.acquire()
    with pytest.raises(RuntimeError, match="not concurrent"):
        asyncio.run(agent.run("x"))
    agent._busy.release()


def test_cancel_blocks_mcp():
    mcp = RecordingMCP({"list_pods": HEALTHY_PODS})
    agent, _llm = make_agent([], mcp, routing="legacy")
    agent.cancel()
    with pytest.raises(Exception):
        asyncio.run(agent._invoke_mcp("list_pods", {}))
    assert mcp.calls == []


@pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="langgraph extra not installed")
def test_langgraph_auto_healthy_matches_react_skill():
    mcp = RecordingMCP({"list_pods": HEALTHY_PODS})
    agent, _llm = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            json_final("No current FOCUS; pods in this scan look Ready."),
        ],
        mcp,
        engine="langgraph",
    )
    answer = asyncio.run(agent.run("Is anything unhealthy?", entrypoint="cli"))
    assert agent.stats["skill"]["skill_id"] == "healthy"
    assert mcp.calls == [("list_pods", {})]
    assert "Evidence check:" in answer
