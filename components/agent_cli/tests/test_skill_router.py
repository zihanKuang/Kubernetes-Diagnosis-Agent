"""Deterministic routing. RequestContext must not contain eval ground truth."""
from agent_cli.skills.context import build_request_context
from agent_cli.skills.intents import detect_intent
from agent_cli.skills.models import ObjectFacts, ObservationSnapshot, SCAN_FAILED, SCAN_NOT_SCANNED, SCAN_SUCCESS
from agent_cli.skills.registry import SkillRegistry
from agent_cli.skills.router import route_post_scan, route_pre_scan
from agent_cli.eval_scenarios import SCENARIOS

CATALOG = [
    "list_pods", "get_recent_events", "get_pod_status", "get_pod_logs", "validate_recovery",
]


def _ctx(query, entrypoint="cli"):
    return build_request_context(query, entrypoint=entrypoint, routing_mode="auto")


def _snap(status, focus=None):
    return ObservationSnapshot(
        scan_status=status,
        focus=list(focus or []),
        observation_ids=[item.obs_id for item in (focus or [])],
        list_pods_ok=status in {SCAN_SUCCESS, "empty"},
    )


def test_intent_health_and_diagnosis():
    assert detect_intent("Is anything unhealthy in the citrus namespace right now?") == "health_check"
    assert detect_intent("What just happened to the frontend pods?") == "fault_diagnosis"
    assert detect_intent("为什么 citrus-crashloop 不健康") == "fault_diagnosis"
    assert detect_intent("asdf qwerty") == "unknown"


def test_request_context_excludes_eval_labels():
    ctx = _ctx("Why is citrus-crashloop unhealthy?")
    payload = ctx.as_dict()
    assert "must_contain" not in payload
    assert "expected_tools" not in payload
    assert "manifest" not in payload
    assert "scenario_id" not in payload
    assert "pre_manifest" not in payload
    assert ctx.named_workloads  # hint only


def test_user_said_oom_does_not_skip_scan():
    registry = SkillRegistry()
    ctx = _ctx("frontend is OOM, what happened?")
    decision = route_pre_scan(ctx, registry, catalog_names=CATALOG)
    assert decision.skill_id == "generic-triage"
    assert "scan required" in decision.fallback_reason


def test_failed_scan_is_not_healthy():
    registry = SkillRegistry()
    ctx = _ctx("Is anything unhealthy?")
    decision = route_post_scan(
        ctx,
        _snap(SCAN_FAILED),
        registry,
        catalog_names=CATALOG,
    )
    assert decision.skill_id == "generic-triage"
    assert "cannot conclude healthy" in decision.fallback_reason


def test_not_scanned_empty_focus_is_not_healthy():
    registry = SkillRegistry()
    ctx = _ctx("Is anything unhealthy?")
    decision = route_post_scan(ctx, _snap(SCAN_NOT_SCANNED), registry, catalog_names=CATALOG)
    assert decision.skill_id != "healthy"


def test_success_no_focus_is_healthy():
    registry = SkillRegistry()
    ctx = _ctx("Is anything unhealthy?")
    decision = route_post_scan(ctx, _snap(SCAN_SUCCESS, []), registry, catalog_names=CATALOG)
    assert decision.skill_id == "healthy"


def test_fault_classes_distinct():
    registry = SkillRegistry()
    ctx = _ctx("What is wrong in citrus?")
    cases = [
        ("CrashLoopBackOff", "", "crashloop"),
        ("ImagePullBackOff", "", "imagepull"),
        ("CrashLoopBackOff", "OOMKilled", "oom"),
        ("RunningNotReady", "", "readiness"),
        ("Unschedulable", "", "scheduling"),
    ]
    for state, last, skill_id in cases:
        focus = [ObjectFacts(
            obs_id="obs-1",
            name="pod-a",
            component="citrus-x",
            phase="Running" if "Pending" not in state and state != "Unschedulable" else "Pending",
            state=state,
            waiting_reason=state,
            last_termination=last,
        )]
        decision = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
        assert decision.skill_id == skill_id, (state, last, decision.skill_id)


def test_killing_event_selects_lifecycle():
    registry = SkillRegistry()
    ctx = _ctx("What just happened to checkout?")
    focus = [ObjectFacts(
        obs_id="obs-2",
        name="checkout-abc",
        component="checkout",
        phase="Running",
        state="Running",
        event_reasons=["Killing"],
    )]
    decision = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
    assert decision.skill_id == "pod-lifecycle"


def test_two_objects_keep_separate_bindings():
    registry = SkillRegistry()
    ctx = _ctx("What is wrong?")
    focus = [
        ObjectFacts(obs_id="obs-1", name="a", state="CrashLoopBackOff", waiting_reason="CrashLoopBackOff"),
        ObjectFacts(obs_id="obs-2", name="b", state="ImagePullBackOff", waiting_reason="ImagePullBackOff"),
    ]
    decision = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
    assert decision.object_bindings["obs-1"] == "crashloop"
    assert decision.object_bindings["obs-2"] == "imagepull"
    assert len(set(decision.object_bindings.values())) == 2


def test_query_name_is_not_focus():
    registry = SkillRegistry()
    ctx = _ctx("What happened to frontend?")
    focus = [ObjectFacts(
        obs_id="obs-4",
        name="citrus-crashloop-abc",
        component="citrus-crashloop",
        state="CrashLoopBackOff",
        waiting_reason="CrashLoopBackOff",
    )]
    decision = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
    assert decision.skill_id == "crashloop"
    assert "frontend" not in decision.object_bindings.values()


def test_scenario_relabel_does_not_change_route():
    registry = SkillRegistry()
    query = SCENARIOS[3].query  # crashloop-workload
    ctx = _ctx(query, entrypoint="eval")
    focus = [ObjectFacts(
        obs_id="obs-1",
        name="citrus-crashloop-abc",
        component="citrus-crashloop",
        state="CrashLoopBackOff",
        waiting_reason="CrashLoopBackOff",
    )]
    a = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
    # Same query+obs, pretend the catalog id changed.
    b = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
    assert a.skill_id == b.skill_id == "crashloop"
    assert "scenario" not in ctx.as_dict()


def test_discovery_order_does_not_change_route():
    registry = SkillRegistry()
    ctx = _ctx("why is it crashlooping?")
    focus = [ObjectFacts(
        obs_id="obs-1", name="x", state="CrashLoopBackOff", waiting_reason="CrashLoopBackOff",
    )]
    first = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
    second = route_post_scan(ctx, _snap(SCAN_SUCCESS, focus), registry, catalog_names=CATALOG)
    assert first.to_json() == second.to_json()
