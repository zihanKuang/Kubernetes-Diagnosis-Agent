"""Eval JSON compare — no cluster."""
from agent_cli.eval_rca import compare_runs


def _run(hits, tools, durations):
    ids = ["healthy-baseline", "pod-kill-frontend", "pod-kill-checkout"]
    return {
        "hit_rate": sum(hits) / 3,
        "results": [
            {
                "scenario_id": sid,
                "root_cause_hit": hit,
                "evidence_level": "HIGH",
                "duration_s": dur,
                "tool_calls": tool,
            }
            for sid, hit, tool, dur in zip(ids, hits, tools, durations)
        ],
    }


def test_compare_reports_no_lift_when_hits_and_tools_flat():
    baseline = _run([True, True, True], [7, 5, 4], [14.3, 8.5, 8.5])
    current = _run([True, True, True], [7, 5, 4], [13.0, 9.0, 8.0])
    cmp = compare_runs(baseline, current)
    assert cmp["hits_baseline"] == "3/3"
    assert cmp["hits_current"] == "3/3"
    assert cmp["scenarios_with_fewer_tools"] == 0
    assert "no lift" in cmp["verdict"]


def test_compare_notices_fewer_tools():
    baseline = _run([True, True, True], [7, 5, 4], [14.3, 8.5, 8.5])
    current = _run([True, True, True], [4, 3, 4], [10.0, 6.0, 8.5])
    cmp = compare_runs(baseline, current)
    assert cmp["scenarios_with_fewer_tools"] == 2
    assert "moved" in cmp["verdict"]
