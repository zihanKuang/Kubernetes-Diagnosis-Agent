"""Hard RCA scorer — no cluster, no LLM."""
from pathlib import Path

import pytest

from agent_cli.eval_scenarios import SCENARIOS, scenario_by_id
from agent_cli.eval_score import score, split_answer


def _stats(tools=None, errors=0, steps=4, duration=10.0):
    return {
        "tool_calls": tools or {"list_pods": 1, "get_recent_events": 1, "validate_recovery": 1},
        "errors": errors,
        "total_steps": steps,
        "start_time": 0.0,
        "end_time": duration,
    }


def test_catalog_has_exactly_three_scenarios_in_safe_order():
    ids = [s.id for s in SCENARIOS]
    assert ids == ["healthy-baseline", "pod-kill-frontend", "pod-kill-checkout"]


def test_kill_manifests_exist_on_disk():
    for scenario in SCENARIOS:
        path = scenario.manifest_path()
        if path is None:
            continue
        assert path.exists(), path


def test_unknown_scenario_id_is_a_hard_error():
    with pytest.raises(KeyError):
        scenario_by_id("pod-kill-payment")


def test_frontend_kill_hit_when_answer_names_the_service_and_the_fault():
    scenario = scenario_by_id("pod-kill-frontend")
    answer = (
        "What happened: Chaos Mesh killed a frontend pod. "
        "ReplicaSet recreated it. validate_recovery=PASS.\n\n"
        "---\nEvidence check: HIGH\n- live tools succeeded\n"
    )
    result = score(scenario, answer, _stats())
    assert result.root_cause_hit is True
    assert result.evidence_level == "HIGH"
    assert result.duration_s == 10.0


def test_frontend_kill_miss_when_the_service_is_never_named():
    scenario = scenario_by_id("pod-kill-frontend")
    answer = "A pod was killed and came back. Everything is fine."
    result = score(scenario, answer, _stats())
    assert result.root_cause_hit is False
    assert "frontend" in result.missing_required


def test_footer_phrases_do_not_count_as_root_cause():
    scenario = scenario_by_id("pod-kill-frontend")
    # Body has no 'frontend'. Footer mentions a tool, which must not leak into the hit.
    answer = "Something restarted.\n\n---\nEvidence check: HIGH\n- tools used: list_pods×1"
    body, footer = split_answer(answer)
    assert "frontend" not in body.lower()
    assert "list_pods" in footer
    result = score(scenario, answer, _stats())
    assert result.root_cause_hit is False


def test_healthy_baseline_miss_if_the_agent_invents_an_outage():
    scenario = scenario_by_id("healthy-baseline")
    answer = "There is an outage. Chaos Mesh ran a PodChaos experiment."
    result = score(scenario, answer, _stats({"list_pods": 1}))
    assert result.root_cause_hit is False
    assert "outage" in result.forbidden_hits


def test_healthy_baseline_hit_when_it_reports_running_pods():
    scenario = scenario_by_id("healthy-baseline")
    answer = "All pods I checked are Running and Ready. The cluster looks healthy."
    result = score(scenario, answer, _stats({"list_pods": 1}))
    assert result.root_cause_hit is True


def test_checkout_kill_does_not_pass_on_a_frontend_story():
    scenario = scenario_by_id("pod-kill-checkout")
    answer = "Chaos Mesh killed the frontend pod. It recovered."
    result = score(scenario, answer, _stats())
    assert result.root_cause_hit is False
    assert "checkout" in result.missing_required


def test_stale_mix_flags_unlabeled_frontend_in_checkout_story():
    from agent_cli.eval_scenarios import STALE_TRAP

    answer = "Chaos Mesh killed a frontend pod. Checkout is also involved."
    result = score(STALE_TRAP, answer, _stats())
    assert result.root_cause_hit is True
    assert result.stale_mix is True


def test_stale_mix_allows_labeled_prior_history():
    from agent_cli.eval_scenarios import STALE_TRAP

    answer = (
        "Chaos Mesh killed a checkout pod. "
        "Note: an earlier frontend kill cycle was already recovered."
    )
    result = score(STALE_TRAP, answer, _stats())
    assert result.root_cause_hit is True
    assert result.stale_mix is False
    scenario = scenario_by_id("pod-kill-frontend")
    answer = "Chaos Mesh killed a frontend pod. It is back."
    result = score(scenario, answer, _stats({"list_pods": 1, "get_recent_events": 1}))
    assert result.root_cause_hit is True
    assert "validate_recovery" in result.expected_tools_missing
    assert result.evidence_level == "MEDIUM"
