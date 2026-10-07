"""Hard evidence check — no LLM, no cluster."""
from agent_cli.evidence import assess, attach_footer, parse_recovery_verdict


def test_no_tools_is_low():
    check = assess(
        query="What happened to frontend?",
        answer="The frontend was killed by chaos.",
        stats={"tool_calls": {}, "errors": 0},
    )
    assert check.level == "LOW"
    assert "0 live tool calls" in check.reasons[0]


def test_empty_answer_is_low():
    check = assess(
        query="status",
        answer="   ",
        stats={"tool_calls": {"list_pods": 1}, "errors": 0},
    )
    assert check.level == "LOW"
    assert "empty" in check.reasons[0]


def test_healthy_rca_run_is_high():
    check = assess(
        query="What just happened to the frontend pods? RCA + validate recovery.",
        answer="Pod frontend-abc was killed; ReplicaSet recreated it. validate_recovery=PASS.",
        stats={
            "tool_calls": {
                "list_pods": 1,
                "get_recent_events": 1,
                "validate_recovery": 1,
            },
            "errors": 0,
        },
    )
    assert check.level == "HIGH"
    assert check.recovery_status == "unknown"
    assert "recovery verified" not in " ".join(check.reasons)
    assert check.diagnosis_status == "not_reviewed"


def test_parse_recovery_verdict_reads_payload_not_call_count():
    assert parse_recovery_verdict("PASS: 1/1 Ready pods")["status"] == "pass"
    assert parse_recovery_verdict("FAIL: 0/1 Ready pods")["status"] == "fail"
    assert parse_recovery_verdict("timeout talking to apiserver")["status"] == "unknown"
    unknown = parse_recovery_verdict(
        "UNKNOWN: query failed: timeout\nstatus=unknown\nreason=query_failed"
    )
    assert unknown["status"] == "unknown"
    assert unknown["reason"] == "query_failed"
    structured = parse_recovery_verdict(
        "PASS: 1/1 Ready pods\nstatus=pass\nreason=ready_at_check_time\n"
        "checked_at=2026-10-06T18:00:00+00:00\nscope=readiness_at_check_time"
    )
    assert structured["status"] == "pass"
    assert structured["scope"] == "readiness_at_check_time"


def test_window_zulu_matches_offset_ledger_stamp():
    from agent_cli.evidence import _window_matches

    assert _window_matches("2026-10-06T18:23:34Z", "2026-10-06T18:23:34+00:00")


def test_rca_without_validate_recovery_caps_at_medium():
    check = assess(
        query="What happened to frontend? RCA please.",
        answer="A pod was killed and came back.",
        stats={
            "tool_calls": {"list_pods": 1, "get_recent_events": 1},
            "errors": 0,
        },
    )
    assert check.level == "MEDIUM"
    assert any("validate_recovery" in r for r in check.reasons)


def test_half_the_calls_failing_is_low():
    check = assess(
        query="list pods",
        answer="I could not reach the cluster.",
        stats={"tool_calls": {"list_pods": 2}, "errors": 1},
    )
    assert check.level == "LOW"


def test_footer_is_appended_and_not_inside_the_answer():
    check = assess(
        query="list pods",
        answer="All pods Running.",
        stats={"tool_calls": {"list_pods": 1}, "errors": 0},
    )
    text = attach_footer("All pods Running.", check)
    assert text.startswith("All pods Running.")
    assert "Evidence check:" in text
    assert "---" in text


def test_empty_focus_after_list_pods_stays_high_on_rca_wording():
    check = assess(
        query="If there is an incident, give a short RCA and validate_recovery.",
        answer="All pods are Running and Ready. No incident.",
        stats={
            "tool_calls": {"list_pods": 1},
            "errors": 0,
            "focus": [],
        },
    )
    assert check.level == "HIGH"
    assert check.diagnosis_status == "not_reviewed"
    assert any("currently match the checked" in r for r in check.reasons)
    assert any("does not prove no incident occurred" in r for r in check.reasons)
    assert "根因已验证" not in check.footer()



def test_nonempty_focus_still_requires_events_and_validate():
    shared = {
        "tool_calls": {"list_pods": 1},
        "errors": 0,
        "focus": ["obs-1"],
    }
    no_events = assess(
        query="What happened to frontend? RCA please.",
        answer="CrashLoopBackOff on citrus-crashloop.",
        stats=shared,
    )
    assert no_events.level == "MEDIUM"
    assert any("get_recent_events" in r for r in no_events.reasons)
    assert any("validate_recovery" in r for r in no_events.reasons)

    complete = assess(
        query="What happened to frontend? RCA please.",
        answer="CrashLoopBackOff on citrus-crashloop. validate_recovery=FAIL.",
        stats={
            "tool_calls": {
                "list_pods": 1,
                "get_recent_events": 1,
                "validate_recovery": 1,
            },
            "errors": 0,
            "focus": ["obs-1"],
        },
    )
    assert complete.level == "HIGH"


def test_omitted_focus_key_keeps_legacy_rca_rules():
    check = assess(
        query="What happened to frontend? RCA please.",
        answer="All pods Running.",
        stats={"tool_calls": {"list_pods": 1}, "errors": 0},
    )
    assert check.level == "MEDIUM"


_CRASH = {
    "id": "obs-2",
    "kind": "Pod",
    "name": "citrus-crashloop-abc",
    "component": "citrus-crashloop",
    "focus": True,
    "window": "current",
    "object_ref": "Pod/citrus-crashloop-abc",
}

_FRONT = {
    "id": "obs-3",
    "kind": "Pod",
    "name": "frontend-54698cfd67-k54bm",
    "component": "frontend",
    "focus": True,
    "window": "2026-10-06T18:23:34+00:00",
    "object_ref": "Pod/frontend-54698cfd67-k54bm",
}

_CHECKOUT = {
    "id": "obs-5",
    "kind": "Pod",
    "name": "checkout-798c8f47cd-dk2xw",
    "component": "checkout",
    "focus": True,
    "window": "2026-10-06T20:18:15+00:00",
    "object_ref": "Pod/checkout-798c8f47cd-dk2xw",
}

_FRONT_LEFTOVER = {
    "id": "obs-9",
    "kind": "Pod",
    "name": "frontend-54698cfd67-68755",
    "component": "frontend",
    "focus": True,
    "window": "2026-10-06T20:17:41+00:00",
    "object_ref": "Pod/frontend-54698cfd67-68755",
}

_FULL_TOOLS = {
    "list_pods": 1,
    "get_recent_events": 1,
    "validate_recovery": 1,
}


def test_matching_citation_is_not_root_cause_verification():
    check = assess(
        query="What happened to frontend? RCA please.",
        answer=(
            "Evidence: obs-2 Pod/citrus-crashloop-abc window=current "
            "is CrashLoopBackOff. validate_recovery=FAIL."
        ),
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-2"],
            "observations": [_CRASH],
            "recovery": {"status": "fail"},
        },
    )
    assert check.claims[0].status == "reference_valid"
    assert check.diagnosis_status == "not_reviewed"
    assert check.diagnosis_passed() is False
    footer = check.footer()
    assert "根因已验证" not in footer
    assert "root-cause verification" in footer
    assert "confirmed" not in footer


def test_leftover_frontend_cite_does_not_pass_checkout_diagnosis():
    check = assess(
        query="What just happened to the checkout pods? RCA please.",
        answer=(
            "Checkout was killed.\n"
            "- obs-5 Pod/checkout-798c8f47cd-tnwdf window=2026-10-06T20:18:15+00:00\n"
            "Note: earlier frontend kill obs-9 Pod/frontend-54698cfd67-68755 "
            "window=2026-10-06T20:17:41+00:00 already recovered."
        ),
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-5", "obs-9"],
            "observations": [_CHECKOUT, _FRONT_LEFTOVER],
            "recovery": {"status": "pass"},
        },
    )
    statuses = {c.obs_id: c.status for c in check.claims}
    assert statuses["obs-9"] == "reference_valid"
    assert statuses["obs-5"] == "reference_invalid"
    assert check.diagnosis_status == "not_reviewed"
    assert check.diagnosis_passed() is False


def test_wrong_object_is_reference_invalid():
    check = assess(
        query="What happened? RCA please.",
        answer="Evidence: obs-2 Pod/frontend-54698cfd67-k54bm window=current CrashLoop.",
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-2"],
            "observations": [_CRASH],
        },
    )
    assert check.claims[0].status == "reference_invalid"
    assert "OBJECT" in check.claims[0].reason
    assert check.diagnosis_passed() is False


def test_wrong_window_is_reference_invalid():
    check = assess(
        query="What happened? RCA please.",
        answer=(
            "Evidence: obs-3 Pod/frontend-54698cfd67-k54bm "
            "window=2020-01-01T00:00:00Z was killed."
        ),
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-3"],
            "observations": [_FRONT],
        },
    )
    assert check.claims[0].status == "reference_invalid"
    assert "window" in check.claims[0].reason


def test_uncited_focus_is_unverifiable_not_a_verified_diagnosis():
    check = assess(
        query="What happened to frontend? RCA please.",
        answer="CrashLoopBackOff on citrus-crashloop. validate_recovery=FAIL.",
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-2"],
            "observations": [_CRASH],
            "recovery": {"status": "fail"},
        },
    )
    assert any(c.status == "unverifiable" and c.obs_id == "obs-2" for c in check.claims)
    assert check.diagnosis_status == "not_reviewed"
    assert check.diagnosis_passed() is False


def test_validate_recovery_call_count_is_not_a_verdict():
    check = assess(
        query="What happened? RCA please.",
        answer="CrashLoopBackOff. I called validate_recovery.",
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-2"],
            "observations": [_CRASH],
        },
    )
    assert check.recovery_status == "unknown"
    assert "recovery verified" not in " ".join(check.reasons)
    assert any("recovery unknown" in r for r in check.reasons)
    assert "- recovery: unknown" in check.footer()


def test_structured_recovery_fail_does_not_fail_the_diagnosis_process():
    check = assess(
        query="What happened? RCA please.",
        answer="obs-2 Pod/citrus-crashloop-abc window=current still CrashLoopBackOff.",
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-2"],
            "observations": [_CRASH],
            "recovery": {"status": "fail"},
        },
    )
    assert check.recovery_status == "fail"
    assert check.level != "LOW"
    assert check.diagnosis_status == "not_reviewed"
    assert check.claims[0].status == "reference_valid"


def test_empty_observations_skips_citation_on_healthy_scan():
    check = assess(
        query="If there is an incident, give a short RCA and validate_recovery.",
        answer="All pods are Running and Ready. No incident.",
        stats={
            "tool_calls": {"list_pods": 1},
            "errors": 0,
            "focus": [],
            "observations": [],
        },
    )
    assert check.level == "HIGH"
    assert check.claims == []
    assert check.scan_scope
    assert "does not prove no incident occurred" in check.scan_scope


def test_footer_obs_ids_do_not_count_as_citations():
    stamped = (
        "CrashLoopBackOff on citrus-crashloop.\n\n"
        "---\nEvidence check: HIGH\n- reference_valid obs-2 Pod/citrus-crashloop-abc\n"
    )
    check = assess(
        query="What happened? RCA please.",
        answer=stamped,
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-2"],
            "observations": [_CRASH],
        },
    )
    assert all(c.status == "unverifiable" for c in check.claims)
    assert check.diagnosis_passed() is False


def test_invalid_diagnosis_draft_is_not_accepted():
    check = assess(
        query="What happened? RCA please.",
        answer="Diagnosis draft invalid; unreviewed free text was not published.",
        stats={
            "tool_calls": {
                "list_pods": 1,
                "get_recent_events": 1,
                "validate_recovery": 1,
            },
            "errors": 0,
            "diagnosis": {"status": "draft_invalid"},
            "recovery": {"status": "fail"},
        },
    )
    assert check.diagnosis_status == "not_reviewed"
    assert check.diagnosis_passed() is False
    assert any("draft invalid" in r for r in check.reasons)


def test_footer_lists_reference_statuses():
    check = assess(
        query="What happened? RCA please.",
        answer="CrashLoopBackOff.",
        stats={
            "tool_calls": _FULL_TOOLS,
            "errors": 0,
            "focus": ["obs-2"],
            "observations": [_CRASH],
        },
    )
    text = attach_footer("CrashLoopBackOff.", check)
    assert "unverifiable obs-2" in text
    assert "references: valid=0 invalid=0 unverifiable=1" in text
    assert "diagnosis: not_reviewed" in text
    assert "待确认" not in text
    assert "confirmed=" not in text
