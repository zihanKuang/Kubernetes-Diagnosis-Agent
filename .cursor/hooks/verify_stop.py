"""Generic stop gate: syntax-check edited files, then optional project tests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hooklib import (
    ROOT,
    audit,
    clear_edits,
    edited_paths,
    emit,
    inside_repo,
    load_checks,
    load_event,
)


def _syntax_errors(paths: list[str], extensions: list[str]) -> list[str]:
    errors: list[str] = []
    allowed = tuple(extensions)
    for raw in paths:
        path = Path(raw)
        if path.suffix.lower() not in allowed:
            continue
        if not path.is_file() or not inside_repo(path):
            continue
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "py_compile failed").strip()
            errors.append(f"{path.relative_to(ROOT)}: {detail}")
    return errors


def _test_error(test_cfg: dict) -> str | None:
    if not test_cfg.get("enabled"):
        return None
    argv = test_cfg.get("argv")
    if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
        return "checks.json test.argv must be a list of strings"
    cwd = ROOT / str(test_cfg.get("cwd") or ".")
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=90,
    )
    if result.returncode == 0:
        return None
    detail = (result.stdout or "") + (result.stderr or "")
    return f"tests exited {result.returncode}\n{detail[-4000:]}"


def main() -> int:
    try:
        event = load_event()
        status = event.get("status") or "completed"
        loop_count = event.get("loop_count")
        audit(event, {"status": status, "loop_count": loop_count})
        if status != "completed":
            emit({})
            return 0

        conversation_id = str(event.get("conversation_id") or "unknown")
        checks = load_checks()
        failures: list[str] = []
        syntax_cfg = checks.get("syntax") or {}
        if syntax_cfg.get("enabled", True):
            extensions = syntax_cfg.get("extensions") or [".py"]
            failures.extend(_syntax_errors(edited_paths(conversation_id), extensions))
        test_failure = _test_error(checks.get("test") or {})
        if test_failure:
            failures.append(test_failure)

        if not failures:
            clear_edits(conversation_id)
            emit({})
            return 0

        body = "\n\n".join(failures)[-6000:]
        emit({
            "followup_message": (
                "Project hook verify_stop found failures. Fix them from the output below. "
                "Do not claim the checks passed unless a later run exits 0.\n\n"
                + body
            )
        })
    except Exception as exc:
        sys.stderr.write(f"verify_stop failed open: {exc}\n")
        emit({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
