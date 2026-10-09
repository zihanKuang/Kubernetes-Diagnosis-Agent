"""Workflow compose, replace, and preview share compile_workflow."""
from pathlib import Path

import pytest

from agent_cli.skills.errors import SkillError
from agent_cli.skills.models import WorkflowSpec
from agent_cli.skills.registry import SkillRegistry
from agent_cli.skills.workflows import compile_workflow, preview_plan

FIXTURES = Path(__file__).parent / "fixtures" / "skills"


def test_compose_expands_and_records_origin():
    registry = SkillRegistry()
    row = registry.get("crashloop")
    plan = compile_workflow(row.workflow, registry, skill_id="crashloop")
    assert plan.stage_ids() == ["scan", "inspect", "verify", "synthesize"]
    origins = [item.origin for item in plan.stages]
    assert any("fragment:scan" in origin or "fragment:" in origin for origin in origins)


def test_replace_inspect_only_changes_that_stage():
    registry = SkillRegistry(extra_dirs=[str(FIXTURES / "crashloop-status-only")])
    row = registry.get("crashloop-status-only")
    plan = compile_workflow(row.workflow, registry, skill_id=row.meta.id)
    inspect = next(item for item in plan.stages if item.spec.id == "inspect")
    assert inspect.spec.tools == ["get_pod_status"]
    assert "get_pod_logs" not in inspect.spec.tools
    assert plan.stage_ids() == ["scan", "inspect", "verify", "synthesize"]
    assert plan.replacements


def test_unknown_replace_stage_fails():
    registry = SkillRegistry()
    spec = WorkflowSpec.from_dict({
        "id": "x",
        "version": "1",
        "compose": ["fragment:scan", "fragment:inspect-generic", "fragment:synthesize"],
        "replace": [{"stage_id": "not-there", "with_fragment": "fragment:inspect-status-only"}],
    })
    with pytest.raises(SkillError, match="unknown stage"):
        compile_workflow(spec, registry, skill_id="x")


def test_cannot_replace_verify_with_inspect():
    registry = SkillRegistry()
    spec = WorkflowSpec.from_dict({
        "id": "x",
        "version": "1",
        "compose": ["fragment:scan", "fragment:recovery", "fragment:synthesize"],
        "replace": [{
            "stage_id": "verify",
            "stage": {
                "id": "verify",
                "processor": "inspect",
                "tools": ["get_pod_status"],
                "outputs": ["inspect_records"],
            },
        }],
    })
    with pytest.raises(SkillError, match="protected processor|required outputs"):
        compile_workflow(spec, registry, skill_id="x")


def test_verify_cannot_be_replaced_with_list_pods_only():
    registry = SkillRegistry()
    spec = WorkflowSpec.from_dict({
        "id": "x",
        "version": "1",
        "compose": ["fragment:scan", "fragment:recovery", "fragment:synthesize"],
        "replace": [{
            "stage_id": "verify",
            "stage": {
                "id": "verify",
                "processor": "verify",
                "tools": ["list_pods"],
                "outputs": ["recovery_verdict"],
                "complete_tools": ["list_pods"],
            },
        }],
    })
    with pytest.raises(SkillError, match="validate_recovery|recovery"):
        compile_workflow(spec, registry, skill_id="x")


def test_recursive_compose_fails():
    registry = SkillRegistry()
    spec = WorkflowSpec.from_dict({
        "id": "loop",
        "version": "1",
        "compose": ["loop"],
    })
    # 'loop' is not a fragment; unknown compose also fails.
    with pytest.raises(SkillError, match="unknown compose|recursive"):
        compile_workflow(spec, registry, skill_id="loop")


def test_preview_uses_same_compile():
    registry = SkillRegistry()
    row = registry.get("oom")
    preview = preview_plan(row, registry)
    plan = compile_workflow(row.workflow, registry, skill_id="oom")
    assert preview["stages"] == plan.as_dict()["stages"]
    assert preview["needs_live_evidence"] is True
