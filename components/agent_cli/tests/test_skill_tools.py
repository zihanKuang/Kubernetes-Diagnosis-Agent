"""MCP catalog vs Agent subset. Copies, unknown names, refresh replaces cache."""
import copy

from agent_cli.mcp_client import MCPClient
from agent_cli.skills.tool_policy import allowed_tools, deny_unknown_or_hidden


def test_get_tools_returns_copies_and_subset():
    client = MCPClient()
    client._tools_cache = {
        "list_pods": {"name": "list_pods", "description": "pods", "input_schema": {"type": "object"}},
        "restart_deployment": {
            "name": "restart_deployment",
            "description": "write",
            "input_schema": {"type": "object"},
        },
    }
    all_tools = client.get_tools()
    all_tools[0]["function"]["name"] = "mutated"
    again = client.get_tools()
    assert again[0]["function"]["name"] == "list_pods"
    subset = client.get_tools(["list_pods"])
    assert len(subset) == 1
    view = client.get_tools_by_name(["list_pods", "nope"])
    assert view["missing"] == ["nope"]
    assert view["tools"][0]["function"]["name"] == "list_pods"


def test_refresh_drops_removed_tools():
    client = MCPClient()
    client._tools_cache = {"old": {"name": "old", "description": "", "input_schema": {}}}
    client._tools_cache = {"list_pods": {"name": "list_pods", "description": "", "input_schema": {}}}
    names = [t["function"]["name"] for t in client.get_tools()]
    assert names == ["list_pods"]


def test_skill_cannot_grant_write_by_declaring_it():
    granted = allowed_tools(
        catalog_names=["restart_deployment", "list_pods"],
        requested=["restart_deployment", "list_pods"],
        policy_capabilities=["scan", "inspect", "logs", "verify"],
        allow_writes=False,
    )
    assert "list_pods" in granted
    assert "restart_deployment" not in granted
    assert deny_unknown_or_hidden("made_up_tool", granted).startswith("DENIED")
