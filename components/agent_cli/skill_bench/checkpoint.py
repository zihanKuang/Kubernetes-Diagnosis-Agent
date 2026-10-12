"""Machine-readable T001–T080 statuses. Experiment rows stay separate from code."""

from __future__ import annotations

from typing import Dict

IMPLEMENTATION = "implementation_complete"
OFFLINE = "offline_verified"
BLOCKED = "blocked_pending_authorization"
NOT_RUN = "not_run"

TASKS: Dict[str, str] = {
    "T001": IMPLEMENTATION,
    "T002": IMPLEMENTATION,
    "T003": OFFLINE,
    "T004": IMPLEMENTATION,
    "T005": IMPLEMENTATION,
    "T006": IMPLEMENTATION,
    "T007": IMPLEMENTATION,
    "T008": IMPLEMENTATION,
    **{f"T{i:03d}": IMPLEMENTATION for i in range(9, 51)},
    **{f"T{i:03d}": IMPLEMENTATION for i in range(51, 65)},
    "T020": OFFLINE,
    "T050": OFFLINE,
    "T063": OFFLINE,
    "T064": IMPLEMENTATION,
    **{f"T{i:03d}": IMPLEMENTATION for i in range(65, 81)},
}
