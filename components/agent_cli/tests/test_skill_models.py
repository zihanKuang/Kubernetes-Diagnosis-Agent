"""Skill contract validation — no model, no cluster."""
import json

import pytest

from agent_cli.skills.errors import SkillError
from agent_cli.skills.models import (
    Condition,
    RequestContext,
    RouteDecision,
    SkillMetadata,
    SkillRunState,
    StageResult,
    StageSpec,
    WorkflowSpec,
    round_trip,
)


def test_missing_id_names_field():
    with pytest.raises(SkillError, match="field=id") as exc:
        SkillMetadata.from_dict(
            {
                "schema_version": "1",
                "id": "",
                "version": "1.0.0",
                "description": "x",
                "entrypoints": ["cli"],
                "workflow_id": "w",
                "instructions": "SKILL.md",
            },
            path="SKILL.toml",
        )
    assert exc.value.field == "id"
    assert "SKILL.toml" in str(exc.value)


def test_unknown_schema_version():
    with pytest.raises(SkillError, match="schema_version"):
        SkillMetadata.from_dict(
            {
                "schema_version": "99",
                "id": "x",
                "version": "1.0.0",
                "description": "x",
                "entrypoints": ["cli"],
                "workflow_id": "w",
                "instructions": "SKILL.md",
            },
            path="SKILL.toml",
        )


def test_duplicate_stage_id_rejected():
    with pytest.raises(SkillError, match="duplicate stage"):
        WorkflowSpec.from_dict(
            {
                "id": "w",
                "version": "1",
                "stages": [
                    {"id": "scan", "processor": "scan"},
                    {"id": "scan", "processor": "inspect"},
                ],
            }
        )


def test_stage_unknown_failure_policy():
    with pytest.raises(SkillError, match="on_failure"):
        StageSpec.from_dict({"id": "scan", "processor": "scan", "on_failure": "retry-forever"})


def test_request_context_rejects_unknown_entrypoint():
    with pytest.raises(SkillError, match="entrypoint"):
        RequestContext(query="q", entrypoint="slack")


def test_stage_result_rejects_success_alias():
    with pytest.raises(SkillError, match="status"):
        StageResult(stage_id="scan", status="ok")


def test_metadata_round_trip_stable():
    meta = SkillMetadata.from_dict(
        {
            "schema_version": "1",
            "id": "demo",
            "version": "1.2.3",
            "description": "demo skill",
            "tags": ["t"],
            "entrypoints": ["cli", "eval"],
            "workflow_id": "demo",
            "instructions": "SKILL.md",
            "enabled": True,
            "routing": {
                "intents": ["fault_diagnosis"],
                "priority": 3,
                "observations": [{"field": "state", "op": "eq", "value": "CrashLoopBackOff"}],
            },
        },
        path="SKILL.toml",
    )
    blob = json.dumps(meta.as_dict(), sort_keys=True)
    again = SkillMetadata.from_dict(json.loads(blob), path="SKILL.toml")
    assert json.dumps(again.as_dict(), sort_keys=True) == blob
    assert round_trip(meta)["id"] == "demo"


def test_route_decision_deterministic_json():
    left = RouteDecision(
        skill_id="oom",
        skill_version="1.0.0",
        workflow_id="oom",
        stage="post_scan",
        candidates=["oom", "crashloop"],
        matched_rules=["obs-1->oom"],
        observation_ids=["obs-1"],
        object_bindings={"obs-1": "oom"},
    )
    right = RouteDecision(
        skill_id="oom",
        skill_version="1.0.0",
        workflow_id="oom",
        stage="post_scan",
        candidates=["oom", "crashloop"],
        matched_rules=["obs-1->oom"],
        observation_ids=["obs-1"],
        object_bindings={"obs-1": "oom"},
    )
    assert left.to_json() == right.to_json()


def test_run_state_does_not_share_containers():
    req = RequestContext(query="q", entrypoint="cli")
    a = SkillRunState(request=req)
    b = SkillRunState(request=RequestContext(query="other", entrypoint="cli"))
    a.allowed_tools.append("list_pods")
    a.history.append(RouteDecision("a", "1", "a", "pre_scan"))
    assert b.allowed_tools == []
    assert b.history == []


def test_condition_rejects_non_table():
    with pytest.raises(SkillError):
        Condition.from_dict("state == crash")
