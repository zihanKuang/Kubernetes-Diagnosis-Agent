"""Structured diagnosis drafts — no cluster, no LLM."""
import json

from agent_cli.diagnosis import parse_diagnosis, publish_diagnosis
from agent_cli.diagnosis.models import (
    CLAIM_CAUSAL,
    CLAIM_OBSERVATION,
    CLAIM_RECOVERY,
    CLAIM_SYMPTOM,
    DRAFT_CITATION_INVALID,
    DRAFT_INVALID,
    DRAFT_OK,
    DRAFT_OPERATIONAL,
)
from agent_cli.diagnosis.renderer import render_diagnosis


OBS = {
    "id": "obs-2",
    "kind": "Pod",
    "name": "citrus-crashloop-abc",
    "object_ref": "Pod/citrus-crashloop-abc",
    "window": "current",
}


def _draft(**kwargs):
    payload = {
        "scope": "citrus-crashloop",
        "unknowns": ["exit cause"],
        "claims": [
            {
                "id": "c1",
                "statement": "citrus-crashloop restarted 4 times",
                "type": "observation",
                "object": "Pod/citrus-crashloop-abc",
                "window": "current",
                "obs": ["obs-2"],
                "evidence": ["ev-1"],
                "confidence": "high",
            }
        ],
    }
    payload.update(kwargs)
    return json.dumps(payload)


def test_draft_fields_are_separately_reviewable():
    draft = parse_diagnosis(
        _draft(),
        observations=[OBS],
        evidence_ids=["ev-1"],
        run_id="abc123",
    )
    assert draft.status == DRAFT_OK
    assert draft.scope == "citrus-crashloop"
    assert draft.unknowns == ["exit cause"]
    assert len(draft.facts()) == 1
    assert draft.symptoms() == []
    assert draft.causal_claims() == []
    assert draft.recovery_claims() == []


def test_compound_oom_restart_recovery_must_be_split():
    text = """{
      "scope": "checkout",
      "unknowns": [],
      "claims": [{
        "id": "c1",
        "statement": "checkout was OOMKilled, restarted, and recovered",
        "type": "causal_claim",
        "object": "Pod/checkout-1",
        "window": "current",
        "obs": ["obs-1"],
        "evidence": ["ev-1"],
        "confidence": "high"
      }]
    }"""
    draft = parse_diagnosis(text, observations=[{"id": "obs-1"}], evidence_ids=["ev-1"])
    assert draft.status == DRAFT_INVALID
    assert any("compound" in e for e in draft.errors)


def test_atomic_restart_oom_recovery_are_separate_claims():
    text = """{
      "scope": "checkout",
      "unknowns": [],
      "claims": [
        {"id": "c1", "statement": "checkout restarted 1 time", "type": "observation",
         "object": "Pod/checkout-old", "window": "current", "obs": ["obs-1"],
         "evidence": ["ev-1"], "confidence": "high"},
        {"id": "c2", "statement": "last termination is OOMKilled exit=137", "type": "causal_claim",
         "object": "Pod/checkout-old", "window": "current", "obs": ["obs-1"],
         "evidence": ["ev-2"], "confidence": "high"},
        {"id": "c3", "statement": "replacement is Ready at checked_at", "type": "recovery_claim",
         "object": "Pod/checkout-new", "window": "current", "obs": ["obs-2"],
         "evidence": ["ev-3"], "confidence": "medium"}
      ]
    }"""
    obs = [
        {"id": "obs-1", "object_ref": "Pod/checkout-old", "window": "current"},
        {"id": "obs-2", "object_ref": "Pod/checkout-new", "window": "current"},
    ]
    draft = parse_diagnosis(text, observations=obs, evidence_ids=["ev-1", "ev-2", "ev-3"])
    assert draft.status == DRAFT_OK
    assert [c.claim_type for c in draft.claims] == [
        CLAIM_OBSERVATION,
        CLAIM_CAUSAL,
        CLAIM_RECOVERY,
    ]


def test_crashloop_cannot_be_a_causal_claim_by_itself():
    text = """{
      "scope": "crashloop",
      "unknowns": ["exit cause"],
      "claims": [{
        "id": "c1",
        "statement": "workload is CrashLoopBackOff",
        "type": "causal_claim",
        "object": "Pod/citrus-crashloop-abc",
        "window": "current",
        "obs": ["obs-2"],
        "evidence": ["ev-1"],
        "confidence": "high"
      }]
    }"""
    draft = parse_diagnosis(text, observations=[OBS], evidence_ids=["ev-1"])
    assert draft.status == DRAFT_OK
    assert draft.claims[0].claim_type == CLAIM_SYMPTOM
    assert any("symptom" in r for r in draft.claims[0].repairs)


def test_invalid_json_is_not_published_as_prose():
    draft, published = publish_diagnosis(
        "Checkout OOM is the root cause and it already recovered.",
        observations=[OBS],
        evidence_ids=["ev-1"],
    )
    assert draft.status == DRAFT_INVALID
    assert "unreviewed free text was not published" in published
    assert "already recovered" not in published
    assert "OOM is the root cause" not in published


def test_fenced_json_and_trailing_comma_are_repaired():
    text = """here is the draft
```json
{
  "scope": "crashloop",
  "unknowns": [],
  "claims": [
    {"id": "c1", "statement": "pod is not Ready", "type": "observation",
     "object": "Pod/citrus-crashloop-abc", "window": "current",
     "obs": ["obs-2"], "evidence": ["ev-1"], "confidence": "high",}
  ],
}
```
"""
    draft = parse_diagnosis(text, observations=[OBS], evidence_ids=["ev-1"])
    assert draft.status == DRAFT_OK
    assert "extracted JSON object" in draft.repairs
    assert "removed trailing commas" in draft.repairs


def test_unknown_claim_type_is_draft_invalid():
    text = """{
      "scope": "x",
      "unknowns": [],
      "claims": [{
        "id": "c1",
        "statement": "something happened",
        "type": "root_cause",
        "obs": ["obs-2"],
        "evidence": ["ev-1"],
        "confidence": "high"
      }]
    }"""
    draft = parse_diagnosis(text, observations=[OBS], evidence_ids=["ev-1"])
    assert draft.status == DRAFT_INVALID
    assert any("type must be" in e for e in draft.errors)


def test_fake_and_summary_and_cross_run_citations_are_caught():
    base = {
        "scope": "crashloop",
        "unknowns": [],
        "claims": [{
            "id": "c1",
            "statement": "pod restarted",
            "type": "observation",
            "object": "Pod/x",
            "window": "current",
            "obs": [],
            "evidence": ["ev-99"],
            "confidence": "high",
        }],
    }
    fake = parse_diagnosis(json.dumps(base), observations=[OBS], evidence_ids=["ev-1"], run_id="aaa")
    assert fake.status == DRAFT_CITATION_INVALID
    assert fake.claims[0].citation_status == "unknown_id"

    base["claims"][0]["evidence"] = ["summary"]
    summary = parse_diagnosis(json.dumps(base), observations=[OBS], evidence_ids=["ev-1"], run_id="aaa")
    assert summary.claims[0].citation_status == "summary_only"

    base["claims"][0]["evidence"] = ["deadbeef:ev-1"]
    cross = parse_diagnosis(json.dumps(base), observations=[OBS], evidence_ids=["ev-1"], run_id="abc123")
    assert cross.claims[0].citation_status == "cross_run"


def test_invalid_evidence_range_is_rejected():
    text = """{
      "scope": "x",
      "unknowns": [],
      "claims": [{
        "id": "c1",
        "statement": "pod restarted",
        "type": "observation",
        "obs": [],
        "evidence": ["ev-1#80-90"],
        "confidence": "high"
      }]
    }"""

    def opener(evidence_id, start, end):
        raise KeyError("bad range")

    draft = parse_diagnosis(
        text,
        observations=[OBS],
        evidence_ids=["ev-1"],
        open_locator=opener,
    )
    assert draft.status == DRAFT_CITATION_INVALID
    assert draft.claims[0].citation_status == "invalid_locator"


def test_render_uses_ledger_object_and_is_order_stable():
    text_a = """{
      "scope": "crashloop",
      "unknowns": ["exit cause"],
      "claims": [
        {"id": "c2", "statement": "CrashLoopBackOff", "type": "symptom",
         "object": "wrong-name", "window": "now", "obs": ["obs-2"],
         "evidence": ["ev-1"], "confidence": "high"},
        {"id": "c1", "statement": "pod is not Ready", "type": "observation",
         "object": "wrong-name", "window": "now", "obs": ["obs-2"],
         "evidence": ["ev-1"], "confidence": "high"}
      ]
    }"""
    text_b = """{
      "scope": "crashloop",
      "unknowns": ["exit cause"],
      "claims": [
        {"id": "c1", "statement": "pod is not Ready", "type": "observation",
         "object": "wrong-name", "window": "now", "obs": ["obs-2"],
         "evidence": ["ev-1", "ev-1"], "confidence": "high"},
        {"id": "c2", "statement": "CrashLoopBackOff", "type": "symptom",
         "object": "wrong-name", "window": "now", "obs": ["obs-2"],
         "evidence": ["ev-1"], "confidence": "high"}
      ]
    }"""
    a = parse_diagnosis(text_a, observations=[OBS], evidence_ids=["ev-1"])
    b = parse_diagnosis(text_b, observations=[OBS], evidence_ids=["ev-1"])
    rendered_a = render_diagnosis(a)
    rendered_b = render_diagnosis(b)
    assert rendered_a == rendered_b
    assert "Pod/citrus-crashloop-abc" in rendered_a
    assert "wrong-name" not in rendered_a
    assert "window=current" in rendered_a
    assert "therefore" not in rendered_a.lower()
    assert "Root cause: not confirmed" in rendered_a
    assert "not an execution failure" in rendered_a


def test_recovery_render_does_not_claim_lasting_health():
    text = """{
      "scope": "frontend",
      "unknowns": [],
      "claims": [{
        "id": "c1",
        "statement": "1/1 pods Ready at checked_at",
        "type": "recovery_claim",
        "object": "Pod/frontend-1",
        "window": "current",
        "obs": ["obs-2"],
        "evidence": ["ev-1"],
        "confidence": "medium"
      }]
    }"""
    draft, published = publish_diagnosis(text, observations=[OBS], evidence_ids=["ev-1"])
    assert draft.status == DRAFT_OK
    assert "lasting recovery" in published
    assert "recovered the service" not in published


def test_partial_restart_without_cause_is_ok_not_failure():
    text = """{
      "scope": "checkout",
      "unknowns": ["why the process exited"],
      "claims": [{
        "id": "c1",
        "statement": "checkout restarted 1 time",
        "type": "observation",
        "object": "Pod/checkout-1",
        "window": "current",
        "obs": ["obs-2"],
        "evidence": ["ev-1"],
        "confidence": "high"
      }]
    }"""
    draft, published = publish_diagnosis(text, observations=[OBS], evidence_ids=["ev-1"])
    assert draft.status == DRAFT_OK
    assert "checkout restarted 1 time" in published
    assert "Root cause: not confirmed" in published
    assert "not an execution failure" in published
    assert "根因已验证" not in published


def test_operational_failure_is_not_a_draft():
    text = "Agent exceeded maximum reasoning steps without reaching a conclusion."
    draft, published = publish_diagnosis(text)
    assert draft.status == DRAFT_OPERATIONAL
    assert published == text
