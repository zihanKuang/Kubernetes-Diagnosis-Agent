"""Deterministic request intent. No extra LLM call."""

from __future__ import annotations

from .models import INTENTS

_HEALTH = (
    "is anything unhealthy",
    "looks healthy",
    "look healthy",
    "cluster healthy",
    "namespace healthy",
    "health check",
    "healthcheck",
    "anything unhealthy",
    "if not, say",
    "if there is an incident",
    "健康",
    "有没有问题",
    "是否健康",
    "看起来健康",
    "巡检",
)
_RECOVERY = (
    "validate_recovery",
    "validate recovery",
    "has it recovered",
    "did it recover",
    "recovery check",
    "恢复了吗",
    "是否恢复",
    "恢复检查",
)
_DIAGNOSIS = (
    "rca",
    "root cause",
    "what happened",
    "what just happened",
    "why is",
    "why are",
    "unhealthy",
    "crash",
    "oom",
    "imagepull",
    "not ready",
    "not starting",
    "pending",
    "killed",
    "故障",
    "根因",
    "为什么",
    "怎么了",
    "崩溃",
    "没起来",
)


def detect_intent(query: str) -> str:
    text = (query or "").strip().lower()
    if not text:
        return "unknown"
    if _any(text, _HEALTH) and not _strong_diagnosis(text):
        return "health_check"
    if _any(text, _RECOVERY) and not _any(text, ("what happened", "rca", "root cause", "为什么", "故障")):
        return "recovery_check"
    if _any(text, _DIAGNOSIS):
        return "fault_diagnosis"
    return "unknown"


def _strong_diagnosis(text: str) -> bool:
    return any(token in text for token in (
        "what just happened",
        "root cause",
        "crashloop",
        "oomkill",
        "imagepull",
    ))


def _any(text: str, needles: tuple[str, ...]) -> bool:
    return any(item in text for item in needles)


def known_intent(name: str) -> bool:
    return name in INTENTS
