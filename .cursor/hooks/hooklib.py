"""Shared helpers for the trial hooks. Copy the scripts; keep project commands in checks.json."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".cursor" / "hooks"
STATE = HOOKS / "state"
AUDIT = STATE / "audit.jsonl"
EDITED = STATE / "edited.json"
CHECKS = HOOKS / "checks.json"


def load_event() -> dict:
    raw = sys.stdin.buffer.read()
    if not raw.strip():
        return {}
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")


def audit(event: dict, extra: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event.get("hook_event_name"),
        "conversation_id": event.get("conversation_id"),
    }
    row.update(extra)
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_edited() -> dict:
    if not EDITED.exists():
        return {}
    try:
        data = json.loads(EDITED.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def remember_edit(conversation_id: str, file_path: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    data = _read_edited()
    paths = data.setdefault(conversation_id, [])
    if file_path not in paths:
        paths.append(file_path)
    EDITED.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def edited_paths(conversation_id: str) -> list[str]:
    paths = _read_edited().get(conversation_id) or []
    return [path for path in paths if isinstance(path, str)]


def clear_edits(conversation_id: str) -> None:
    data = _read_edited()
    data.pop(conversation_id, None)
    if data:
        EDITED.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif EDITED.exists():
        EDITED.unlink()


def load_checks() -> dict:
    if not CHECKS.exists():
        return {}
    data = json.loads(CHECKS.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True
