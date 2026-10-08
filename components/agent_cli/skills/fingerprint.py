"""Stable content fingerprints. No tokenizer, no token-savings claims."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint_text(text: str) -> str:
    return fingerprint_bytes((text or "").encode("utf-8"))


def fingerprint_json(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return fingerprint_text(blob)
