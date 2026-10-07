"""Observation ledger: FOCUS from live state, targeted tools must cite it."""
from agent_cli.observations import ObservationLedger, annotate_target_tools, selector_for


PODS = """Pods in namespace 'citrus' (4 total):

- accounting-76dc9dc54-4l9j8 | phase=Running | ready=True | restarts=107 | component=accounting
- frontend-54698cfd67-k54bm | phase=Running | ready=True | restarts=0 | component=frontend
- frontend-proxy-7c6db5767-9hg8n | phase=Running | ready=True | restarts=2 | component=frontend-proxy
- citrus-crashloop-abc | phase=Running | ready=False | restarts=4 | component=citrus-crashloop | state=CrashLoopBackOff
"""

EVENTS = """Events in last 10m (namespace=citrus):
NOTE: Events are correlated by the OBJECT column
TIME\tTYPE\tREASON\tOBJECT\tMESSAGE
2026-10-06T18:23:34+00:00\tNormal\tKilling\tPod/frontend-54698cfd67-qnx8d\tStopping container frontend
2026-10-06T18:23:35+00:00\tNormal\tStarted\tPod/frontend-54698cfd67-k54bm\tStarted container frontend
2026-10-06T18:20:01+00:00\tNormal\tKilling\tPod/old-experiment-zzzzz\tStopping leftover pod
"""


def test_crashloop_is_focus_accounting_restarts_are_not():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    focus = {obs.component: obs.id for obs in ledger.focus_objects()}
    assert "citrus-crashloop" in focus
    assert "accounting" not in focus
    assert "frontend" not in focus
    ids = {obs.name: obs.id for obs in ledger.objects if obs.kind == "Pod"}
    assert ids["citrus-crashloop-abc"] == "obs-4"


def test_killing_event_promotes_replacement_pod_not_unrelated_rs():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    ledger.ingest("get_recent_events", EVENTS)
    focus_names = {obs.name for obs in ledger.focus_objects()}
    assert "frontend-54698cfd67-k54bm" in focus_names
    assert "frontend-proxy-7c6db5767-9hg8n" not in focus_names
    assert "accounting-76dc9dc54-4l9j8" not in focus_names
    assert "old-experiment-zzzzz" not in {obs.name for obs in ledger.objects}


def test_frontend_selector_does_not_match_frontend_proxy():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    ledger.ingest("get_recent_events", EVENTS)
    denied = ledger.authorize(
        "get_pod_logs",
        {"pod_selector": "app.kubernetes.io/component=frontend-proxy"},
    )
    assert denied.startswith("DENIED")
    allowed = ledger.authorize(
        "get_pod_logs",
        {"pod_selector": "app.kubernetes.io/component=frontend"},
    )
    assert allowed == ""


def test_targeted_tool_locked_until_scan():
    ledger = ObservationLedger()
    denied = ledger.authorize("get_pod_logs", {"pod_selector": "app=frontend"})
    assert "locked" in denied.lower()
    assert "list_pods" in denied


def test_from_obs_fills_selector_and_strips_before_mcp():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    crash = next(obs for obs in ledger.focus_objects() if obs.component == "citrus-crashloop")
    bound = ledger.bind_arguments({"from_obs": crash.id, "lines": 20})
    assert "from_obs" not in bound
    assert bound["pod_selector"] == selector_for(crash)
    assert bound["lines"] == 20
    assert ledger.authorize("get_pod_status", {"from_obs": crash.id}) == ""


def test_payment_never_observed_is_denied():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    denied = ledger.authorize(
        "get_pod_logs",
        {"pod_selector": "app.kubernetes.io/component=payment"},
    )
    assert denied.startswith("DENIED")
    assert "obs-4" in denied or "citrus-crashloop" in denied


def test_healthy_scan_catalog_tells_the_model_not_to_dig():
    ledger = ObservationLedger()
    healthy = (
        "Pods in namespace 'citrus' (1 total):\n\n"
        "- cart-1 | phase=Running | ready=True | restarts=2 | component=cart\n"
    )
    ledger.ingest("list_pods", healthy)
    catalog = ledger.catalog()
    assert "FOCUS: none" in catalog
    assert ledger.authorize("validate_recovery", {"pod_selector": "app.kubernetes.io/component=cart"}).startswith("DENIED")


def test_errors_are_not_parsed_as_pods():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", "Error listing pods: Forbidden")
    assert ledger.objects == []


def test_annotate_adds_from_obs_without_dropping_selector():
    tools = [{
        "type": "function",
        "function": {
            "name": "get_pod_logs",
            "description": "logs",
            "parameters": {
                "type": "object",
                "properties": {"pod_selector": {"type": "string"}},
                "required": ["pod_selector"],
            },
        },
    }]
    annotate_target_tools(tools)
    props = tools[0]["function"]["parameters"]["properties"]
    assert "from_obs" in props
    assert "pod_selector" in props
    assert "FOCUS" in tools[0]["function"]["description"]


def test_killing_event_sets_window_on_replacement():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    ledger.ingest("get_recent_events", EVENTS)
    frontend = next(obs for obs in ledger.objects if obs.name.endswith("k54bm"))
    assert frontend.focus
    assert frontend.window.startswith("2026-10-06T18:23:34")
    catalog = ledger.catalog()
    assert "window=" in catalog
    assert "obs-2" in catalog or frontend.id in catalog


def test_rescan_does_not_drop_event_promoted_focus():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    ledger.ingest("get_recent_events", EVENTS)
    ledger.ingest("list_pods", PODS)
    focus_names = {obs.name for obs in ledger.focus_objects()}
    assert "frontend-54698cfd67-k54bm" in focus_names
    assert "citrus-crashloop-abc" in focus_names


def test_same_name_different_uid_are_two_objects():
    ledger = ObservationLedger()
    ledger._upsert_pod(name="web-1", uid="aaa", component="web", namespace="citrus")
    ledger._upsert_pod(name="web-1", uid="bbb", component="web", namespace="citrus")
    twins = [obs for obs in ledger.objects if obs.name == "web-1"]
    assert {obs.uid for obs in twins} == {"aaa", "bbb"}


def test_killing_event_keeps_deleted_pod_and_replacement():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    ledger.ingest("get_recent_events", EVENTS)
    names = {obs.name: obs for obs in ledger.objects}
    killed = names["frontend-54698cfd67-qnx8d"]
    live = names["frontend-54698cfd67-k54bm"]
    assert killed.present is False
    assert killed.focus
    assert live.present is True
    assert live.focus
    assert killed.id != live.id
    assert "old-experiment-zzzzz" not in names
    bound = ledger.bind_arguments({"from_obs": killed.id})
    assert bound["pod_selector"] == selector_for(killed)
    assert ledger.authorize("get_pod_status", {"from_obs": killed.id}) == ""


def test_event_time_is_not_collected_at():
    text = (
        "Pods in namespace 'citrus' (1 total) collected_at=2026-10-06T20:30:00+00:00:\n\n"
        "- frontend-54698cfd67-k54bm | phase=Running | ready=True | restarts=0 | "
        "component=frontend | uid=live | ns=citrus | owner=ReplicaSet/frontend-54698cfd67\n"
    )
    events = (
        "Events in last 10m (namespace=citrus, collected_at=2026-10-06T20:30:00+00:00):\n"
        "TIME\tTYPE\tREASON\tOBJECT\tMESSAGE\tFIRST\tCOUNT\n"
        "2026-10-06T18:23:34Z\tNormal\tKilling\tPod/frontend-54698cfd67-qnx8d uid=dead\t"
        "Stopping\t2026-10-06T18:23:34Z\t1\n"
    )
    ledger = ObservationLedger()
    ledger.ingest("list_pods", text)
    ledger.ingest("get_recent_events", events)
    killed = next(obs for obs in ledger.objects if obs.name.endswith("qnx8d"))
    live = next(obs for obs in ledger.objects if obs.name.endswith("k54bm"))
    assert killed.event_time.startswith("2026-10-06T18:23:34")
    assert killed.window.startswith("2026-10-06T18:23:34")
    assert killed.collected_at.startswith("2026-10-06T20:30:00")
    assert killed.collected_at != killed.event_time
    assert live.uid == "live"
    assert killed.uid == "dead"
    assert live.owner_name == "frontend-54698cfd67"
    catalog = ledger.catalog()
    assert "present=false" in catalog
    assert "collected_at=" in catalog
