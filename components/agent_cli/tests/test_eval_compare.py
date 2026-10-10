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


def test_compare_reads_phrase_hit_from_new_reports_and_root_cause_hit_from_old():
    old = {
        "results": [{"scenario_id": "a", "root_cause_hit": True, "tool_calls": 4, "duration_s": 1}],
    }
    new = {
        "results": [{
            "scenario_id": "a",
            "phrase_hit": False,
            "root_cause_hit": True,
            "tool_calls": 4,
            "duration_s": 1,
        }],
    }
    cmp = compare_runs(old, new)
    assert cmp["hits_baseline"] == "1/1"
    assert cmp["hits_current"] == "0/1"

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
    assert cmp["same_budget"] is None
    assert cmp["rows"][0]["delta_tokens"] is None


def _with_budget(run, engine, prompt_tokens):
    run = dict(run)
    run["engine"] = engine
    run["model_name"] = "deepseek-v4-flash"
    run["llm_provider"] = "deepseek"
    run["max_steps"] = 10
    run["results"] = [dict(row) for row in run["results"]]
    for row, tokens in zip(run["results"], prompt_tokens):
        row["prompt_tokens"] = tokens
        row["completion_tokens"] = 1
    return run


def test_compare_token_deltas_when_model_and_steps_match():
    baseline = _with_budget(
        _run([True, True, True], [4, 4, 4], [8, 8, 8]),
        "react",
        [10, 10, 10],
    )
    current = _with_budget(
        _run([True, True, True], [4, 4, 4], [9, 7, 8]),
        "langgraph",
        [14, 10, 8],
    )
    cmp = compare_runs(baseline, current)
    assert cmp["same_budget"] is True
    assert cmp["engine_baseline"] == "react"
    assert cmp["engine_current"] == "langgraph"
    assert cmp["max_steps_baseline"] == 10
    assert cmp["rows"][0]["delta_prompt_tokens"] == 4
    assert cmp["rows"][0]["delta_tokens"] == 4
    assert cmp["rows"][2]["delta_tokens"] == -2
    assert "budgets differ" not in cmp["verdict"]


def test_compare_flags_a_different_step_budget():
    baseline = _with_budget(
        _run([True, True, True], [4, 4, 4], [8, 8, 8]),
        "react",
        [10, 10, 10],
    )
    current = _with_budget(
        _run([True, True, True], [4, 4, 4], [8, 8, 8]),
        "langgraph",
        [10, 10, 10],
    )
    current["max_steps"] = 4
    cmp = compare_runs(baseline, current)
    assert cmp["same_budget"] is False
    assert cmp["verdict"].startswith("budgets differ")
