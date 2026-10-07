"""Per-run evidence trace — no cluster, no LLM."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_cli.config import AgentConfig
from agent_cli.evidence_models import CALL_ERROR, CALL_SUCCESS, ToolPayload
from agent_cli.trace_store import (
    TraceSession,
    config_fingerprint,
    domain_result,
    load_run,
    parse_mcp_result,
    redact,
    save_run,
)


def test_config_fingerprint_omits_api_key():
    config = AgentConfig(api_key="sk-secret-test-key", max_steps=4)
    fp = config_fingerprint(config)
    blob = str(fp)
    assert "sk-secret-test-key" not in blob
    assert "api_key" not in fp
    assert fp["max_steps"] == 4


def test_redact_strips_keys_and_bearer_headers():
    payload = {
        "api_key": "sk-live",
        "Authorization": "Bearer abc.def",
        "note": "token=supersecret",
        "nested": {"mcp_auth_token": "t-1"},
    }
    out = redact(payload)
    blob = str(out)
    assert "sk-live" not in blob
    assert "abc.def" not in blob
    assert "supersecret" not in blob
    assert "t-1" not in blob
    assert out["api_key"] == "[REDACTED]"


def test_consecutive_sessions_do_not_reuse_ids():
    config = AgentConfig(api_key="k")
    a = TraceSession("first", config)
    a.record_denied("get_pod_logs", {"from_obs": "obs-1"}, "DENIED")
    b = TraceSession("second", config)
    b.record_denied("get_pod_logs", {"from_obs": "obs-1"}, "DENIED")
    assert a.run.run_id != b.run.run_id
    assert a.run.tool_calls[0].call_id == "call-1"
    assert b.run.tool_calls[0].call_id == "call-1"
    assert a.run.evidence[0].evidence_id == "ev-1"
    assert b.run.evidence[0].evidence_id == "ev-1"


def test_bound_arguments_are_what_was_executed():
    session = TraceSession("q", AgentConfig(api_key="k"))
    payload = ToolPayload(text="crash: exit 1", call_status=CALL_SUCCESS)
    record = session.record_payload(
        tool_name="get_pod_logs",
        requested={"from_obs": "obs-2", "lines": 20},
        bound={"pod_selector": "app.kubernetes.io/component=citrus-crashloop", "lines": 20},
        payload=payload,
        visible_text=payload.text,
        visible_ranges=[(0, len(payload.text))],
        started_at="t0",
        ended_at="t1",
    )
    assert "from_obs" in record.requested_arguments
    assert record.bound_arguments["pod_selector"] == (
        "app.kubernetes.io/component=citrus-crashloop"
    )
    assert "from_obs" not in record.bound_arguments


def test_validate_recovery_fail_is_success_call_with_domain_fail():
    payload = ToolPayload(text="FAIL: 0/1 Ready pods", call_status=CALL_SUCCESS)
    assert domain_result("validate_recovery", payload) == "fail"
    err = ToolPayload(text="FAIL: 0/1 Ready pods", call_status=CALL_ERROR, is_error=True)
    assert domain_result("validate_recovery", err) is None


def test_mcp_is_error_is_not_success_evidence():
    result = SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text="FAIL: apiserver down")],
        structuredContent=None,
    )
    payload = parse_mcp_result(result)
    assert payload.is_error is True
    assert payload.call_status == CALL_ERROR
    assert "apiserver down" in payload.text


def test_append_only_keeps_prior_sample_when_object_recovers():
    session = TraceSession("q", AgentConfig(api_key="k"))
    first = ToolPayload(text="ready=False", call_status=CALL_SUCCESS)
    second = ToolPayload(text="ready=True", call_status=CALL_SUCCESS)
    args = {"pod_selector": "app=frontend"}
    session.record_payload(
        "get_pod_status", args, args, first, first.text, [(0, len(first.text))], "a", "b",
    )
    session.record_payload(
        "get_pod_status", args, args, second, second.text, [(0, len(second.text))], "c", "d",
    )
    assert [e.text for e in session.run.evidence] == ["ready=False", "ready=True"]
    latest = session.run.latest_by_object["selector:app=frontend"]
    assert session.run.evidence_by_id(latest).text == "ready=True"
    assert session.open_locator("ev-1") == "ready=False"


def test_unknown_locator_is_rejected():
    session = TraceSession("q", AgentConfig(api_key="k"))
    with pytest.raises(KeyError):
        session.open_locator("ev-99")


def test_truncated_view_keeps_full_evidence(tmp_path: Path):
    session = TraceSession("q", AgentConfig(api_key="k"))
    full = "HEAD-ERR-middle-secret-crash-TAIL"
    visible = "HEAD-ERR...TAIL"
    session.record_payload(
        "get_pod_logs",
        {},
        {"pod_selector": "app=x"},
        ToolPayload(text=full, call_status=CALL_SUCCESS),
        visible,
        [(0, 8), (len(full) - 4, len(full))],
        "t0",
        "t1",
    )
    item = session.run.evidence[0]
    assert "middle-secret-crash" in item.text
    seen = session.run.generator_seen[0]
    assert seen.truncated_for_viewer is True
    assert session.open_locator(item.evidence_id, 0, 8).startswith("HEAD-ERR")


def test_save_roundtrip_and_incomplete_package_is_not_reviewable(tmp_path: Path):
    session = TraceSession("what happened", AgentConfig(api_key="sk-secret-test-key"))
    session.record_denied("restart_deployment", {"name": "frontend"}, "DENIED: no y")
    session.finalize(draft="draft", final="final", check=None, stats={"errors": 0})
    path = save_run(session.run, tmp_path)
    loaded = load_run(path)
    blob = path.read_text(encoding="utf-8")
    assert "sk-secret-test-key" not in blob
    assert loaded["reviewable"] is True
    assert loaded["schema_version"] == 1
    assert loaded["run_id"] == session.run.run_id

    old = tmp_path / "legacy.json"
    old.write_text('{"schema_version": 0, "run_id": "old", "draft": "x"}', encoding="utf-8")
    legacy = load_run(old)
    assert legacy["reviewable"] is False
    assert "不可完整复核" in legacy["reviewable_reason"]
    assert "evidence" not in legacy or legacy.get("evidence") is None
