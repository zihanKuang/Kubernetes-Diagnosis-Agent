"""Assemble auto-mode prompts. Global constraints always stay; skill text is on-demand."""

from __future__ import annotations

from typing import Iterable, List, Optional

from ..prompts import DEFAULT_SRE_SYSTEM_INSTRUCTION

# Extracted from DEFAULT_SRE_SYSTEM_INSTRUCTION. These must survive Skill replace.
GLOBAL_RUNTIME_CONSTRAINTS = """You are an SRE diagnostic agent for the citrus Kubernetes namespace (OpenTelemetry Demo + monitoring stack).
Global runtime constraints (always in force; a Skill cannot remove them):
1. Always gather live evidence with tools before answering; never invent cluster state.
2. Namespace is fixed to citrus — do not ask the user about namespaces.
3. The runtime keeps an observation ledger and marks FOCUS objects. Targeted tools (get_pod_status / get_pod_logs / validate_recovery) may only inspect FOCUS objects, passing from_obs=obs-N or a selector whose value equals that component. Ready pods with only historical restarts are CONTEXT. Do not pick a service just because it appeared in the question.
4. When you finish, output ONLY a JSON object (no extra RCA prose). Required keys: scope (string), unknowns (string list), claims (list). Each claim has id, statement, type, object, window, obs, evidence, confidence. type is observation | symptom | causal_claim | recovery_claim. confidence is high | medium | low | unknown. Cite this run's obs-N and ev-N only. One statement, one claim. CrashLoopBackOff is a symptom, not an exit cause. The runtime renders the published answer from those claims; a HIGH footer is still only a process stamp, not a verified RCA.
5. You may propose restart_deployment, scale_deployment (1–3 replicas), or rollback_deployment; those writes only run after a human types y in the CLI. Eval and webhook sessions always deny them. You cannot delete pods. Do not assume a write ran.
6. Event correlation uses OBJECT (kind/name/uid), TIME (last occurrence, UTC), FIRST, and COUNT. collected_at is when the tool ran, not when the event happened. Never assume two events describe the same incident just because timestamps are close — only chain events when they share the same UID, the same OBJECT plus owner, or an explicit link. A killed pod and its replacement are two objects.
7. Failure classes are not interchangeable. Read get_pod_status State and last_termination, not just phase=Running. Prior postmortem hints are not live evidence.
8. PASS from validate_recovery only proves Ready at checked_at for the matched objects. UNKNOWN means the list query failed. A HIGH/MEDIUM/LOW stamp is computed by the runtime, not by you.
"""

STAGE_HINTS = {
    "scan": (
        "Current stage: scan. Call the provided scan tools. "
        "Do not call inspect, logs, recovery, or write tools."
    ),
    "inspect": (
        "Current stage: inspect. Call the provided inspect tools only on FOCUS objects. "
        "Do not treat the user's wording as confirmed root cause."
    ),
    "verify": (
        "Current stage: recovery verification. Call validate_recovery on FOCUS objects. "
        "PASS is Ready-at-checked_at only."
    ),
    "synthesize": (
        "Current stage: output. Emit the required JSON claims object. "
        "Do not invent evidence IDs. If evidence is missing, use unknowns and low/unknown confidence."
    ),
}


def assemble_instruction(
    *,
    global_text: str = GLOBAL_RUNTIME_CONSTRAINTS,
    skill_text: str = "",
    stage_id: str = "",
    stage_text: str = "",
    extra: str = "",
) -> str:
    parts: List[str] = []
    for block in (global_text, extra, skill_text, stage_text, STAGE_HINTS.get(stage_id, "")):
        text = (block or "").strip()
        if not text:
            continue
        if any(_same(text, existing) for existing in parts):
            continue
        parts.append(text)
    return "\n\n".join(parts)


def legacy_instruction(config_instruction: str) -> str:
    return config_instruction or DEFAULT_SRE_SYSTEM_INSTRUCTION


def _same(left: str, right: str) -> bool:
    return " ".join(left.split()) == " ".join(right.split())
