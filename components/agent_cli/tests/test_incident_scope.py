"""Incident scope comes from the query and ledger, not eval answers."""
import inspect

from agent_cli.observations import scope as incident_scope
from agent_cli.observations.scope import (
    HISTORICAL,
    IN_SCOPE,
    RELATED,
    relation_for,
    scope_from_ledger,
    scope_from_query,
)
from agent_cli.observations import ObservationLedger
from agent_cli.stamps import iso_utc


PODS = """Pods in namespace 'citrus' (3 total) collected_at=2026-10-06T20:30:00+00:00:

- checkout-798c8f47cd-dk2xw | phase=Running | ready=True | restarts=0 | component=checkout | uid=live-c | ns=citrus | owner=ReplicaSet/checkout-798c8f47cd
- frontend-54698cfd67-k54bm | phase=Running | ready=True | restarts=0 | component=frontend | uid=live-f | ns=citrus | owner=ReplicaSet/frontend-54698cfd67
- frontend-proxy-7c6db5767-9hg8n | phase=Running | ready=True | restarts=0 | component=frontend-proxy | uid=live-p | ns=citrus | owner=ReplicaSet/frontend-proxy-7c6db5767
"""

EVENTS = """Events in last 10m (namespace=citrus, collected_at=2026-10-06T20:30:00+00:00):
TIME\tTYPE\tREASON\tOBJECT\tMESSAGE\tFIRST\tCOUNT
2026-10-06T20:18:15+00:00\tNormal\tKilling\tPod/checkout-798c8f47cd-tnwdf uid=dead-c\tStopping checkout\t2026-10-06T20:18:15+00:00\t1
2026-10-06T20:17:41+00:00\tNormal\tKilling\tPod/frontend-54698cfd67-68755 uid=dead-f\tStopping frontend\t2026-10-06T20:17:41+00:00\t1
"""


def _ledger():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    ledger.ingest("get_recent_events", EVENTS)
    return ledger


def test_scope_does_not_import_eval_answers():
    source = inspect.getsource(incident_scope)
    assert "eval_scenarios" not in source
    assert "eval_rca" not in source
    assert "from agent_cli.eval" not in source


def test_query_does_not_invent_a_time_window():
    scope = scope_from_query("what just happened to checkout?", ["checkout", "frontend"])
    assert scope.targets == ["checkout"]
    assert scope.time_range == "unspecified"
    assert scope.source == "query"


def test_unspecific_query_does_not_invent_targets():
    scope = scope_from_query("the namespace looks unhealthy", ["checkout", "frontend"])
    assert scope.targets == []


def test_frontend_query_does_not_select_frontend_proxy():
    scope = scope_from_query("what happened to frontend?", ["frontend", "frontend-proxy"])
    assert scope.targets == ["frontend"]


def test_checkout_query_marks_leftover_frontend_historical():
    ledger = _ledger()
    ledger.query = "What just happened to the checkout pods?"
    scope = scope_from_ledger(ledger.query, ledger)
    assert "checkout" in scope.targets
    assert "frontend" not in scope.targets
    by_name = {obs.name: obs for obs in ledger.focus_objects()}
    checkout = by_name["checkout-798c8f47cd-dk2xw"]
    frontend = by_name["frontend-54698cfd67-k54bm"]
    assert relation_for(checkout, scope) == IN_SCOPE
    assert relation_for(frontend, scope) == HISTORICAL
    catalog = ledger.catalog()
    assert "relation=historical" in catalog
    assert "relation=in_scope" in catalog


def test_generic_unhealthy_query_keeps_focus_in_scope():
    ledger = _ledger()
    ledger.query = "why is the namespace unhealthy?"
    scope = scope_from_ledger(ledger.query, ledger)
    assert scope.targets == []
    crash_or_focus = ledger.focus_objects()
    assert crash_or_focus
    assert all(relation_for(obs, scope) == IN_SCOPE for obs in crash_or_focus)


def test_ready_context_is_related_not_in_scope_when_query_names_a_service():
    ledger = ObservationLedger()
    ledger.ingest("list_pods", PODS)
    ledger.query = "what happened to checkout?"
    scope = scope_from_ledger(ledger.query, ledger)
    proxy = next(obs for obs in ledger.objects if obs.component == "frontend-proxy")
    assert proxy.focus is False
    assert relation_for(proxy, scope) == RELATED


def test_zulu_and_offset_are_the_same_instant():
    assert iso_utc("2026-10-06T18:23:34Z") == iso_utc("2026-10-06T18:23:34+00:00")
