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


def test_catalog_keeps_healthy_baseline_first_and_covers_fault_classes():
    ids = [s.id for s in SCENARIOS]
    assert ids == [
        "healthy-baseline",
        "pod-kill-frontend",
        "pod-kill-checkout",
        "crashloop-workload",
        "imagepull-workload",
        "oomkilled-workload",
        "probe-not-ready",
        "pod-unschedulable",
    ]
    assert ids[0] == "healthy-baseline"


def test_kill_manifests_exist_on_disk():
    for scenario in SCENARIOS:
        path = scenario.manifest_path()
        if path is None:
            continue
        assert path.exists(), path
    assert scenario_by_id("pod-kill-frontend").manifest_path().parent.name == "chaos"
    assert scenario_by_id("crashloop-workload").manifest_path().parent.name == "faults"
    assert scenario_by_id("pod-unschedulable").manifest_path().name == "unschedulable.yaml"


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
    assert result.phrase_hit is True
    assert result.root_cause_hit is True
    assert result.scoring_rule == "phrase_v1"
    assert result.as_dict()["root_cause_hit_is_legacy"] is True
    assert result.diagnosis_status == "not_reviewed"
    assert result.evidence_level == "HIGH"
    assert result.duration_s == 10.0
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


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


def test_stuck_faults_do_not_pass_on_the_wrong_class():
    cases = [
        (
            "crashloop-workload",
            "citrus-crashloop was OOMKilled.",
            "citrus-crashloop is in CrashLoopBackOff after the process exits.",
        ),
        (
            "imagepull-workload",
            "citrus-imagepull is CrashLoopBackOff.",
            "citrus-imagepull is ImagePullBackOff: failed to pull the image.",
        ),
        (
            "oomkilled-workload",
            "citrus-oom is in CrashLoopBackOff and keeps restarting.",
            "citrus-oom last termination was OOMKilled, exit 137. CrashLoopBackOff is just the backoff.",
        ),
        (
            "probe-not-ready",
            "citrus-probe is CrashLoopBackOff.",
            "citrus-probe is Running but the readiness probe is failing.",
        ),
        (
            "pod-unschedulable",
            "citrus-unschedulable is Pending with ImagePullBackOff.",
            "citrus-unschedulable is Pending: FailedScheduling, the node selector didn't match.",
        ),
    ]
    for scenario_id, wrong, right in cases:
        scenario = scenario_by_id(scenario_id)
        assert score(scenario, wrong, _stats()).root_cause_hit is False
        assert score(scenario, right, _stats()).root_cause_hit is True


def test_print_table_does_not_call_phrase_hits_rca_accuracy(capsys):
    from agent_cli.eval_rca import _print_table
    from agent_cli.eval_score import Score

    _print_table([
        Score(scenario_id="healthy-baseline", phrase_hit=True, root_cause_hit=True),
        Score(scenario_id="pod-kill-frontend", phrase_hit=True, root_cause_hit=True),
    ])
    out = capsys.readouterr().out
    assert "phrase hit rate: 2/2" in out
    assert "not RCA accuracy" in out
    assert "root-cause hit rate" not in out


def test_score_copies_token_counts_from_the_run():
    scenario = scenario_by_id("pod-kill-frontend")
    stats = _stats()
    stats["prompt_tokens"] = 120
    stats["completion_tokens"] = 30
    stats["llm_calls"] = 3
    result = score(scenario, "Chaos killed the frontend pod.", stats)
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 30
    assert result.llm_calls == 3
    assert result.duration_s == 10.0


def test_score_copies_claims_without_treating_them_as_rca_accuracy():
    scenario = scenario_by_id("crashloop-workload")
    stats = _stats()
    stats["focus"] = ["obs-2"]
    stats["observations"] = [{
        "id": "obs-2",
        "kind": "Pod",
        "name": "citrus-crashloop-abc",
        "component": "citrus-crashloop",
        "focus": True,
        "window": "current",
        "object_ref": "Pod/citrus-crashloop-abc",
    }]
    stats["recovery"] = {"status": "fail"}
    miss = score(
        scenario,
        "citrus-crashloop is in CrashLoopBackOff after the process exits.",
        stats,
    )
    assert miss.phrase_hit is True
    assert miss.root_cause_hit is True
    assert miss.diagnosis_status == "not_reviewed"
    assert miss.recovery_status == "fail"
    assert miss.claims
    assert miss.claims[0]["status"] == "unverifiable"

    hit = score(
        scenario,
        "Evidence: obs-2 Pod/citrus-crashloop-abc window=current "
        "is in CrashLoopBackOff after the process exits.",
        stats,
    )
    assert hit.phrase_hit is True
    assert hit.diagnosis_status == "not_reviewed"
    assert hit.recovery_status == "fail"
    assert hit.claims[0]["status"] == "reference_valid"
    assert hit.as_dict()["root_cause_hit_is_legacy"] is True
