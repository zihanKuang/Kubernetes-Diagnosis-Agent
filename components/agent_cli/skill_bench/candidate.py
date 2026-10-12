"""Locked candidate metadata. Original SKILL.md is not redistributed."""

from __future__ import annotations

from typing import Any, Dict

CANDIDATE: Dict[str, Any] = {
    "label": "Citrus-adapted external Skill",
    "not_upstream_as_is": True,
    "source_repo": "https://github.com/geored/sre-skill",
    "source_file": "debugging-kubernetes-incidents/SKILL.md",
    "source_url": (
        "https://github.com/geored/sre-skill/blob/d8af309ec3fe7d5d34cf1d352e7ea05bba5ae844/"
        "debugging-kubernetes-incidents/SKILL.md"
    ),
    "commit": "d8af309ec3fe7d5d34cf1d352e7ea05bba5ae844",
    "retrieved": "2026-10-07",
    "github_license_field": None,
    "license_file": "missing (HTTP 404)",
    "redistribute_original": False,
    "install_to_user_skill_dir": False,
    "original_text_in_this_repo": False,
    "phases_observed": [
        "triage / severity / scope",
        "data collection (pods, logs, events, metrics)",
        "correlation / timeline",
        "root cause",
        "remediation recommendations (read-only)",
    ],
    "tool_map": [
        {
            "candidate": "kubectl get pods",
            "citrus": "list_pods",
            "support": "full",
        },
        {
            "candidate": "kubectl describe/get pod yaml",
            "citrus": "get_pod_status",
            "support": "partial",
            "gap": "no full YAML, no node name dump",
        },
        {
            "candidate": "kubectl get events --sort-by lastTimestamp",
            "citrus": "get_recent_events",
            "support": "partial",
            "gap": "fixed citrus namespace window, not arbitrary sort flags",
        },
        {
            "candidate": "kubectl logs --tail",
            "citrus": "get_pod_logs",
            "support": "partial",
            "gap": "no --previous, no --all-containers switch",
        },
        {
            "candidate": "kubectl logs --previous",
            "citrus": None,
            "support": "unsupported",
        },
        {
            "candidate": "kubectl top pods/nodes",
            "citrus": "query_prometheus",
            "support": "unsupported_in_replay",
            "gap": "Prometheus not in replay fixtures; live MCP may query metrics",
        },
        {
            "candidate": "certificate / TLS / service selector / network debug",
            "citrus": None,
            "support": "unsupported",
        },
        {
            "candidate": "write/restart as remediation",
            "citrus": "gated write tools",
            "support": "schema_only_in_legacy",
            "gap": "bench replay denies all writes",
        },
    ],
    "adaptation_rules": [
        "Keep phase order: scan, inspect status, then logs if useful, then verify.",
        "Prefer events before treating logs as the first clue.",
        "Do not add tools the candidate used that Citrus cannot provide.",
        "Do not paste own-skill investigation paragraphs into C.",
        "Require the same claims JSON output protocol as B.",
        "Call the arm 'external-adapted', never 'upstream skill as-is'.",
    ],
}
