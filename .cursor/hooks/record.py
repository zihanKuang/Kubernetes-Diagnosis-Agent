"""Generic stats hook. Records edits and shell commands. Copy as-is."""
from __future__ import annotations

import sys

from hooklib import audit, emit, load_event, remember_edit


def main() -> int:
    try:
        event = load_event()
        extra: dict = {}
        file_path = event.get("file_path")
        if isinstance(file_path, str) and file_path:
            extra["file_path"] = file_path
            extra["edit_count"] = len(event.get("edits") or [])
            remember_edit(str(event.get("conversation_id") or "unknown"), file_path)
        command = event.get("command")
        if isinstance(command, str) and command:
            extra["command"] = command[:500]
            extra["duration_ms"] = event.get("duration")
        audit(event, extra)
        emit({})
    except Exception as exc:
        sys.stderr.write(f"record hook failed open: {exc}\n")
        emit({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
