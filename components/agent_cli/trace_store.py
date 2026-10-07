"""Append-only per-run trace. Redacts secrets. Disk packages are optional."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .evidence import parse_recovery_verdict
from .evidence_models import (
    CALL_DENIED,
    CALL_ERROR,
    CALL_SUCCESS,
    CALL_TIMEOUT,
    CONTENT_DENIAL,
    CONTENT_STRUCTURED,
    CONTENT_TEXT,
    SCHEMA_VERSION,
    EvidenceItem,
    RunRecord,
    ToolCallRecord,
    ToolPayload,
    ViewerSlice,
)


SECRET_KEYS = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "token",
    "password",
    "secret",
    "deepseek_api_key",
    "citrus_llm_api_key",
    "mcp_auth_token",
    "x-webhook-token",
})

CONFIG_WHITELIST = (
    "model_name",
    "llm_provider",
    "llm_base_url",
    "max_steps",
    "memory_enabled",
    "writes_interactive",
    "max_content_length",
    "tool_timeout_seconds",
    "skill_routing",
    "skill_id",
)

_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_ASSIGNED = re.compile(
    r"(?i)((?:api[_-]?key|authorization|token|password|secret)\s*[=:]\s*)\S+"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return uuid.uuid4().hex


def config_fingerprint(config: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in CONFIG_WHITELIST:
        if hasattr(config, key):
            out[key] = getattr(config, key)
    return out


def _secret_key(key: str) -> bool:
    return key.lower().replace("-", "_") in SECRET_KEYS


def redact_text(text: str) -> str:
    if not text:
        return text
    out = _BEARER.sub(r"\1[REDACTED]", text)
    return _ASSIGNED.sub(r"\1[REDACTED]", out)


def redact(value: Any, key: str = "") -> Any:
    if _secret_key(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    return value


def object_key(tool_name: str, bound: Dict[str, Any]) -> str:
    selector = str((bound or {}).get("pod_selector") or "").strip()
    if selector:
        return f"selector:{selector}"
    obs = str((bound or {}).get("from_obs") or "").strip()
    if obs:
        return f"obs:{obs}"
    if tool_name in {"list_pods", "get_recent_events"}:
        return "scan:namespace"
    return ""


def domain_result(tool_name: str, payload: ToolPayload) -> Optional[str]:
    if payload.call_status != CALL_SUCCESS or payload.is_error:
        return None
    if tool_name != "validate_recovery":
        return None
    status = parse_recovery_verdict(payload.text).get("status")
    if status in {"pass", "fail", "unknown"}:
        return status
    return None


class TraceSession:
    def __init__(self, query: str, config: Any):
        self.run = RunRecord(
            schema_version=SCHEMA_VERSION,
            run_id=new_run_id(),
            started_at=utc_now(),
            query=query,
            config=config_fingerprint(config),
        )
        self._call_n = 1
        self._ev_n = 1

    def record_denied(
        self,
        tool_name: str,
        requested: Dict[str, Any],
        reason: str,
        bound: Optional[Dict[str, Any]] = None,
    ) -> ToolCallRecord:
        stamp = utc_now()
        call_id = f"call-{self._call_n}"
        self._call_n += 1
        bound_args = redact(bound if bound is not None else requested)
        evidence = self._append_evidence(
            call_id=call_id,
            text=redact_text(reason),
            content_type=CONTENT_DENIAL,
            locator="text",
            object_key="",
            source_truncated=False,
        )
        record = ToolCallRecord(
            call_id=call_id,
            tool_name=tool_name,
            requested_arguments=redact(requested),
            bound_arguments=bound_args,
            started_at=stamp,
            ended_at=stamp,
            call_status=CALL_DENIED,
            evidence_id=evidence.evidence_id,
            error_message=redact_text(reason),
        )
        self.run.tool_calls.append(record)
        self._generator_view(evidence.evidence_id, reason, reason)
        return record

    def record_payload(
        self,
        tool_name: str,
        requested: Dict[str, Any],
        bound: Dict[str, Any],
        payload: ToolPayload,
        visible_text: str,
        visible_ranges: List[Tuple[int, int]],
        started_at: str,
        ended_at: str,
    ) -> ToolCallRecord:
        call_id = f"call-{self._call_n}"
        self._call_n += 1
        status = payload.call_status
        if payload.is_error and status == CALL_SUCCESS:
            status = CALL_ERROR
        content_type = CONTENT_STRUCTURED if payload.structured is not None else CONTENT_TEXT
        evidence = self._append_evidence(
            call_id=call_id,
            text=redact_text(payload.text),
            content_type=content_type,
            locator="structured" if payload.structured is not None else "text",
            object_key=object_key(tool_name, bound),
            source_truncated=payload.source_truncated,
        )
        record = ToolCallRecord(
            call_id=call_id,
            tool_name=tool_name,
            requested_arguments=redact(requested),
            bound_arguments=redact(bound),
            started_at=started_at,
            ended_at=ended_at,
            call_status=status,
            evidence_id=evidence.evidence_id,
            domain_result=domain_result(tool_name, payload),
            error_message=redact_text(payload.text) if status != CALL_SUCCESS else "",
            is_error=bool(payload.is_error),
            structured=redact(payload.structured),
            source_truncated=payload.source_truncated,
        )
        self.run.tool_calls.append(record)
        self.run.generator_seen.append(
            ViewerSlice(
                evidence_id=evidence.evidence_id,
                viewer="generator",
                ranges=visible_ranges or [(0, len(payload.text or ""))],
                truncated_for_viewer=visible_text != payload.text,
            )
        )
        return record

    def finalize(
        self,
        *,
        draft: str,
        final: str,
        check: Any,
        stats: Dict[str, Any],
        diagnosis: Any = None,
    ) -> RunRecord:
        self.run.ended_at = utc_now()
        self.run.draft = draft
        self.run.diagnosis = diagnosis
        self.run.final = final
        if check is not None and hasattr(check, "footer"):
            self.run.check = {
                "level": check.level,
                "diagnosis_status": getattr(check, "diagnosis_status", ""),
                "recovery_status": getattr(check, "recovery_status", ""),
                "reasons": list(getattr(check, "reasons", []) or []),
            }
        else:
            self.run.check = {}
        self.run.stats = {
            "tool_calls": dict(stats.get("tool_calls") or {}),
            "errors": int(stats.get("errors") or 0),
            "prompt_tokens": int(stats.get("prompt_tokens") or 0),
            "completion_tokens": int(stats.get("completion_tokens") or 0),
            "llm_calls": int(stats.get("llm_calls") or 0),
            "total_steps": int(stats.get("total_steps") or 0),
            "tool_ok": dict(stats.get("tool_ok") or {}),
            "tool_denied": dict(stats.get("tool_denied") or {}),
            "tool_error": dict(stats.get("tool_error") or {}),
            "skill": dict(stats.get("skill") or {}),
        }
        return self.run

    def open_locator(self, evidence_id: str, start: int = 0, end: Optional[int] = None) -> str:
        item = self.run.evidence_by_id(evidence_id)
        if item is None:
            raise KeyError(f"unknown evidence locator: {evidence_id}")
        text = item.text or ""
        stop = len(text) if end is None else end
        if start < 0 or stop > len(text) or start > stop:
            raise KeyError(f"invalid range {start}:{end} on {evidence_id}")
        return text[start:stop]

    def _append_evidence(
        self,
        *,
        call_id: str,
        text: str,
        content_type: str,
        locator: str,
        object_key: str,
        source_truncated: bool,
    ) -> EvidenceItem:
        item = EvidenceItem(
            evidence_id=f"ev-{self._ev_n}",
            call_id=call_id,
            collected_at=utc_now(),
            content_type=content_type,
            locator=locator,
            text=text,
            source_truncated=source_truncated,
            object_key=object_key,
        )
        self._ev_n += 1
        self.run.evidence.append(item)
        if object_key:
            self.run.latest_by_object[object_key] = item.evidence_id
        return item

    def _generator_view(self, evidence_id: str, full: str, visible: str) -> None:
        self.run.generator_seen.append(
            ViewerSlice(
                evidence_id=evidence_id,
                viewer="generator",
                ranges=[(0, len(visible or ""))],
                truncated_for_viewer=visible != full,
            )
        )


def save_run(record: RunRecord, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.run_id}.json"
    path.write_text(
        json.dumps(redact(record.as_dict()), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_run(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("run package must be an object")
    data.setdefault("schema_version", 0)
    data.setdefault("skill", (data.get("stats") or {}).get("skill") or {})
    missing = []
    if "evidence" not in data:
        missing.append("evidence")
    if "tool_calls" not in data:
        missing.append("tool_calls")
    if missing:
        data["reviewable"] = False
        data["reviewable_reason"] = "不可完整复核：缺原始证据（" + ", ".join(missing) + "）"
    else:
        data["reviewable"] = True
        data["reviewable_reason"] = ""
    return data


def parse_mcp_result(result: Any) -> ToolPayload:
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    texts: List[str] = []
    content = getattr(result, "content", None) or []
    for part in content:
        if hasattr(part, "text"):
            texts.append(part.text)
        else:
            texts.append(str(part))
    text = "\n".join(texts) if texts else str(result)
    meta = getattr(result, "_meta", None) or getattr(result, "meta", None) or {}
    truncated = False
    if isinstance(meta, dict) and meta.get("truncated"):
        truncated = True
    status = CALL_ERROR if is_error else CALL_SUCCESS
    return ToolPayload(
        text=text,
        call_status=status,
        is_error=is_error,
        structured=structured,
        source_truncated=truncated,
    )
