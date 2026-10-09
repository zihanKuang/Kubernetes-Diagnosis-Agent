"""Regressions that would pollute Skills A/B/C comparison (T009–T019)."""
import asyncio

from agent_cli.agent import ReActAgent
from agent_cli.config import AgentConfig
from agent_cli.skills.models import ObservationSnapshot, ObjectFacts, SCAN_SUCCESS
from agent_cli.skills.runtime import SkillRuntime
from agent_cli.skills.snapshots import enrich_from_status
from agent_cli.tests.test_skill_paths import (
    CRASH_PODS,
    EVENTS_NONE,
    HEALTHY_PODS,
    IMAGE_PODS,
    OOM_STATUS,
    RecordingMCP,
    ScriptedLLM,
    catalog,
    json_final,
    make_agent,
    rca_turns,
    tool_call,
)


def test_rca_scan_requires_events_before_health_or_inspect():
    mcp = RecordingMCP({"list_pods": HEALTHY_PODS, "get_recent_events": EVENTS_NONE})
    agent, llm = make_agent(
        [
            {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
            {
                "content": None,
                "tool_calls": [tool_call("get_recent_events", {}, "c2")],
            },
            json_final("recent kill then replacement; current pods Ready."),
        ],
        mcp,
    )
    asyncio.run(agent.run("What just happened to the storefront pods? Short RCA.", entrypoint="cli"))
    names = [call[0] for call in mcp.calls]
    assert names[:2] == ["list_pods", "get_recent_events"]
    assert "get_pod_status" not in names
    scan_tools = llm.seen_tools[0]
    assert "list_pods" in scan_tools
    assert "get_recent_events" in scan_tools


def test_inspect_progress_is_per_object():
    mixed = (
        "Pods in namespace 'citrus' (2 total):\n\n"
        "- worker-a | phase=Running | ready=False | restarts=3 | "
        "component=worker-a | state=CrashLoopBackOff\n"
        "- worker-b | phase=Pending | ready=False | restarts=0 | "
        "component=worker-b | state=ImagePullBackOff\n"
    )
    mcp = RecordingMCP({
        "list_pods": mixed,
        "get_recent_events": EVENTS_NONE,
        "get_pod_status": (
            "Pod: worker-a\n  State: CrashLoopBackOff\n\n"
            "Pod: worker-b\n  State: ImagePullBackOff\n"
        ),
        "get_pod_logs": "log",
        "validate_recovery": "status=fail",
    })
    agent, llm = make_agent(
        rca_turns("two faults", obs="obs-1")[:1]
        + [
            {
                "content": None,
                "tool_calls": [tool_call("get_pod_status", {"from_obs": "obs-1"}, "c2")],
            },
            {
                "content": None,
                "tool_calls": [
                    tool_call("get_pod_status", {"from_obs": "obs-2"}, "c3"),
                    tool_call("get_pod_logs", {"from_obs": "obs-1"}, "c4"),
                ],
            },
            {
                "content": None,
                "tool_calls": [tool_call("validate_recovery", {"from_obs": "obs-1"}, "c5")],
            },
            json_final("two objects"),
        ],
        mcp,
    )
    asyncio.run(agent.run("Why are the workloads unhealthy? RCA.", entrypoint="cli"))
    status_ids = [
        row.obs_id
        for row in agent.skill.state.outcomes
        if row.tool_name == "get_pod_status" and row.status == "success"
    ]
    assert "obs-1" in status_ids
    assert "obs-2" in status_ids
    loaded = agent.stats["skill"]["loaded_instructions"]
    assert "crashloop" in loaded
    assert "imagepull" in loaded
    inspect_turns = [names for names in llm.seen_tools if "get_pod_status" in names]
    assert inspect_turns
    assert any("get_pod_logs" in names for names in inspect_turns)


def test_status_blocks_do_not_attach_to_first_focus():
    text = (
        "Pod: worker-a\n  State: CrashLoopBackOff\n"
        "  Container main: last_termination=OOMKilled(exit=137)\n\n"
        "Pod: worker-b\n  State: ImagePullBackOff\n"
    )
    blocks = enrich_from_status(text)
    assert [item["name"] for item in blocks] == ["worker-a", "worker-b"]
    assert blocks[0]["last_termination"] == "OOMKilled"
    assert "OOMKilled" not in (blocks[1].get("last_termination") or "")
    snap = ObservationSnapshot(
        scan_status=SCAN_SUCCESS,
        focus=[
            ObjectFacts(obs_id="obs-1", name="worker-a", state="CrashLoopBackOff"),
            ObjectFacts(obs_id="obs-2", name="worker-b", state="ImagePullBackOff"),
        ],
    )
    config = AgentConfig(api_key="k", memory_enabled=False, skill_routing="auto")
    runtime = SkillRuntime(config)
    runtime.begin("why", entrypoint="cli", catalog_names=["get_pod_status"])
    runtime.state.outcomes = []
    runtime.record_outcome("get_pod_status", "success", text=text, obs_id="")
    runtime._enrich_status(snap)
    assert "OOMKilled" in runtime.state.object_facts["obs-1"].last_termination
    assert "OOMKilled" not in runtime.state.object_facts["obs-2"].last_termination


def test_initialize_connects_once():
    agent = ReActAgent(AgentConfig(api_key="k", memory_enabled=False))

    class FakeMCP:
        def __init__(self):
            self.connects = 0

        async def connect(self):
            self.connects += 1

        def get_tools(self):
            return catalog()

    mcp = FakeMCP()
    agent.mcp_client = mcp
    asyncio.run(agent.initialize())
    assert mcp.connects == 1
    assert any(
        "from_obs" in ((item.get("function") or {}).get("parameters") or {}).get("properties", {})
        for item in agent.tool_catalog
        if ((item.get("function") or {}).get("name") == "get_pod_status")
    )


def test_model_schema_includes_from_obs_legacy_and_auto():
    captured = []

    class CaptureLLM(ScriptedLLM):
        async def generate_with_tools(self, messages, tools, **kwargs):
            captured.append(tools)
            return await super().generate_with_tools(messages, tools, **kwargs)

    for routing in ("legacy", "auto"):
        captured.clear()
        mcp = RecordingMCP({"list_pods": HEALTHY_PODS, "get_recent_events": EVENTS_NONE})
        agent, _ = make_agent(
            [
                {"content": None, "tool_calls": [tool_call("list_pods", {}, "c1")]},
                json_final("ok"),
            ],
            mcp,
            routing=routing,
        )
        agent.llm_client = CaptureLLM(agent.llm_client.responses)
        asyncio.run(agent.run("Is anything unhealthy?", entrypoint="cli"))
        status = [
            item for item in captured[0]
            if ((item.get("function") or {}).get("name") == "get_pod_status")
        ]
        if routing == "legacy":
            assert status
            props = (status[0]["function"].get("parameters") or {}).get("properties") or {}
            assert "from_obs" in props
        else:
            assert "list_pods" in [
                ((item.get("function") or {}).get("name")) for item in captured[0]
            ]


def test_budget_blocks_excess_calls_before_mcp():
    mcp = RecordingMCP({"list_pods": HEALTHY_PODS, "get_recent_events": EVENTS_NONE})
    agent, _ = make_agent(
        [
            {
                "content": None,
                "tool_calls": [
                    tool_call("list_pods", {}, "c1"),
                    tool_call("get_recent_events", {}, "c2"),
                    tool_call("list_pods", {}, "c3"),
                ],
            },
            json_final("ok"),
        ],
        mcp,
        routing="legacy",
    )
    agent.config.max_tool_calls = 1
    asyncio.run(agent.run("Is anything unhealthy?", entrypoint="cli"))
    assert len(mcp.calls) == 1
    assert (agent.stats.get("tool_denied") or {}).get("get_recent_events", 0) >= 1 or (
        agent.stats.get("tool_denied") or {}
    )


def test_inspect_failure_goes_unknown_not_max_steps():
    mcp = RecordingMCP({
        "list_pods": CRASH_PODS,
        "get_recent_events": EVENTS_NONE,
        "get_pod_status": "ERROR: timed out talking to apiserver",
    })
    agent, _ = make_agent(
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
            json_final("status unknown"),
        ],
        mcp,
    )
    asyncio.run(agent.run("Why is the workload unhealthy? RCA.", entrypoint="cli"))
    results = {row["stage_id"]: row["status"] for row in agent.stats["skill"]["stage_results"]}
    assert results.get("inspect") == "failed"
    assert agent.stats["skill"].get("unknown_reason")
    assert agent.stats["total_steps"] < 8


def test_status_then_logs_two_turn_inspect():
    mcp = RecordingMCP({
        "list_pods": CRASH_PODS,
        "get_recent_events": EVENTS_NONE,
        "get_pod_status": "Pod: citrus-crashloop-abc\n  State: CrashLoopBackOff\n",
        "get_pod_logs": "process exited 1",
        "validate_recovery": "status=fail",
    })
    agent, llm = make_agent(rca_turns("crash loop symptom", obs="obs-2"), mcp)
    asyncio.run(agent.run("Why is the workload unhealthy? RCA.", entrypoint="cli"))
    names = [call[0] for call in mcp.calls]
    assert names.index("get_pod_status") < names.index("get_pod_logs")
    status_turn = next(i for i, tools in enumerate(llm.seen_tools) if "get_pod_status" in tools)
    logs_turn = next(i for i, tools in enumerate(llm.seen_tools) if "get_pod_logs" in tools)
    assert logs_turn >= status_turn
    assert "get_pod_logs" in llm.seen_tools[status_turn] or logs_turn > status_turn
