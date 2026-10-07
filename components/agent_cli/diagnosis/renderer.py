"""Deterministic diagnosis text from validated claims. Adds no new causal sentences."""
from __future__ import annotations

from typing import List

from .models import (
    CLAIM_CAUSAL,
    CLAIM_OBSERVATION,
    CLAIM_RECOVERY,
    CLAIM_SYMPTOM,
    DRAFT_INVALID,
    DRAFT_OPERATIONAL,
    Claim,
    DiagnosisDraft,
)


_SECTIONS = (
    (CLAIM_OBSERVATION, "Facts"),
    (CLAIM_SYMPTOM, "Symptoms"),
    (CLAIM_CAUSAL, "Root cause hypotheses (not reviewed)"),
    (CLAIM_RECOVERY, "Recovery (Ready at check time only)"),
)


def render_diagnosis(draft: DiagnosisDraft) -> str:
    if draft.status == DRAFT_OPERATIONAL:
        return (draft.raw_text or "").strip()
    if draft.status == DRAFT_INVALID:
        lines = [
            "Diagnosis draft invalid; unreviewed free text was not published.",
        ]
        for error in draft.errors:
            lines.append(f"- {error}")
        return "\n".join(lines)

    lines: List[str] = []
    if draft.scope:
        lines.append(f"Scope: {draft.scope}")
        lines.append("")

    published = draft.published_claims()
    for claim_type, title in _SECTIONS:
        group = [c for c in published if c.claim_type == claim_type]
        if not group:
            continue
        lines.append(title)
        for claim in group:
            lines.append(_claim_line(claim))
        if claim_type == CLAIM_RECOVERY:
            lines.append(
                "PASS/Ready here only proves readiness at checked_at; "
                "it is not lasting recovery."
            )
        lines.append("")

    if not any(c.claim_type == CLAIM_CAUSAL for c in published):
        lines.append("Root cause: not confirmed.")
        lines.append(
            "Confirmed facts may stand while the cause stays unknown. "
            "That is not a confirmed RCA and not an execution failure."
        )
        lines.append("")

    if draft.unknowns:
        lines.append("Unknowns")
        for item in draft.unknowns:
            lines.append(f"- {item}")
        lines.append("")

    withheld = [c for c in draft.claims if c.citation_status != "valid"]
    if withheld:
        lines.append("Not published (citation failed)")
        for claim in withheld:
            why = claim.citation_reason or claim.citation_status
            lines.append(f"- {claim.claim_id}: {why}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _claim_line(claim: Claim) -> str:
    bits = [claim.claim_id]
    if claim.object_ref:
        bits.append(claim.object_ref)
    bits.append(f"window={claim.window or 'current'}")
    bits.extend(claim.obs_ids)
    bits.extend(claim.evidence_ids)
    prefix = " ".join(bits)
    return f"- {prefix}: {claim.statement}"
