"""Compare report keeps phrase_hit meaning and flags skill-mode mismatch."""
from agent_cli.eval_rca import compare_runs


def test_compare_marks_skill_routing_mismatch():
    baseline = {
        "engine": "react",
        "skill_routing": "legacy",
        "model_name": "m",
        "llm_provider": "deepseek",
        "max_steps": 10,
        "results": [{"scenario_id": "a", "phrase_hit": True, "tool_calls": 4, "duration_s": 1}],
    }
    current = dict(baseline)
    current["skill_routing"] = "auto"
    current["results"] = [{"scenario_id": "a", "phrase_hit": True, "tool_calls": 4, "duration_s": 1}]
    cmp = compare_runs(baseline, current)
    assert cmp["comparable"] is False
    assert "skill routing differs" in cmp["verdict"]
    assert "phrase_hit" in cmp["phrase_hit_note"]
