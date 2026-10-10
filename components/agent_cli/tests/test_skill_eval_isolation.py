"""Eval labels must not leak into routing; skills package must not import eval_scenarios."""
import ast
from pathlib import Path

from agent_cli.skills.context import build_request_context
from agent_cli.skills.models import ObjectFacts, ObservationSnapshot, SCAN_SUCCESS
from agent_cli.skills.registry import SkillRegistry
from agent_cli.skills.router import route_post_scan
from agent_cli.eval_scenarios import scenario_by_id

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"
CATALOG = ["list_pods", "get_recent_events", "get_pod_status", "get_pod_logs", "validate_recovery"]


def test_skills_package_does_not_import_eval_scenarios():
    for path in SKILLS_ROOT.rglob("*.py"):
        raw = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(raw)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "eval_scenarios" not in alias.name
                    assert "eval_score" not in alias.name
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "eval_scenarios" not in mod
                assert "eval_score" not in mod


def test_relabeled_scenario_same_query_same_route():
    original = scenario_by_id("crashloop-workload")
    registry = SkillRegistry()
    focus = [ObjectFacts(
        obs_id="obs-1",
        name="citrus-crashloop-abc",
        component="citrus-crashloop",
        state="CrashLoopBackOff",
        waiting_reason="CrashLoopBackOff",
    )]
    snap = ObservationSnapshot(
        scan_status=SCAN_SUCCESS,
        focus=focus,
        observation_ids=["obs-1"],
        list_pods_ok=True,
    )
    ctx_a = build_request_context(original.query, entrypoint="eval", routing_mode="auto")
    ctx_b = build_request_context(original.query, entrypoint="eval", routing_mode="auto")
    # Changing must_contain / id / manifest is a test-side concern only.
    a = route_post_scan(ctx_a, snap, registry, catalog_names=CATALOG)
    b = route_post_scan(ctx_b, snap, registry, catalog_names=CATALOG)
    assert a.skill_id == b.skill_id
    assert "must_contain" not in ctx_a.as_dict()
    assert original.id not in str(ctx_a.as_dict())
