"""UTC timestamps. Event time and collected_at must not be mixed."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def parse_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "current"}:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(value: Any) -> str:
    dt = parse_utc(value)
    if dt is None:
        return str(value or "").strip()
    return dt.isoformat()
