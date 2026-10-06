"""Alertmanager webhook → incident card. Outside the ReAct loop.

Engineering (not model) requirements:
  auth token, groupKey idempotency, per-service rate limit, degrade on agent
  failure, fixed card template. LOW evidence is never published as a root cause.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .agent import ReActAgent
from .config import AgentConfig
from .eval_score import split_answer
from .evidence import assess
from .metrics import render as render_metrics
from .topology import ServiceGraph

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEGRADE_PATH = _REPO_ROOT / "data" / "webhook" / "degraded.jsonl"

DEFAULT_IDEMPOTENCY_TTL_S = 15 * 60
DEFAULT_RATE_LIMIT_S = 5 * 60


def _now() -> float:
    return time.time()


def extract_token(headers: Dict[str, str]) -> Optional[str]:
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    extra = headers.get("X-Webhook-Token") or headers.get("x-webhook-token")
    extra = (extra or "").strip()
    return extra or None


def authorized(headers: Dict[str, str], expected: str) -> bool:
    if not expected:
        return False
    got = extract_token(headers)
    return bool(got) and got == expected


def alert_services(payload: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    seen = set()
    alerts = payload.get("alerts") or []
    if not isinstance(alerts, list):
        alerts = []
    common = payload.get("commonLabels") or {}
    for labels in [common, *[a.get("labels") or {} for a in alerts if isinstance(a, dict)]]:
        for key in ("service", "app", "job", "deployment", "component", "pod"):
            value = str(labels.get(key) or "").strip()
            if value and value not in seen:
                seen.add(value)
                names.append(value)
    return names


def firing_alerts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for alert in payload.get("alerts") or []:
        if isinstance(alert, dict) and (alert.get("status") or "").lower() == "firing":
            out.append(alert)
    return out


def build_query(payload: Dict[str, Any], edges: List[Tuple[str, str]]) -> str:
    services = alert_services(payload) or ["unknown"]
    summaries = []
    for alert in firing_alerts(payload)[:8]:
        ann = alert.get("annotations") or {}
        labels = alert.get("labels") or {}
        bit = ann.get("summary") or ann.get("description") or labels.get("alertname") or ""
        if bit:
            summaries.append(str(bit).strip()[:200])
    summary_txt = "; ".join(summaries) or "no annotation"
    edge_txt = ""
    if edges:
        pretty = ", ".join(f"{a}↔{b}" for a, b in edges)
        edge_txt = (
            f" Jaeger adjacency among these alerts (hint only, not live evidence): {pretty}."
        )
    return (
        f"Alertmanager firing on {', '.join(services)}. {summary_txt}.{edge_txt} "
        "Short RCA from live cluster state, then validate_recovery."
    )


_EVIDENCE_HOW = {
    "list_pods": "kubectl get pods -n citrus",
    "get_recent_events": "kubectl get events -n citrus --sort-by=.lastTimestamp",
    "get_pod_status": "kubectl get pods -n citrus -l <selector>",
    "get_pod_logs": "kubectl logs -n citrus -l <selector> --tail=50",
    "query_prometheus": (
        "Prometheus UI (kubectl port-forward -n citrus "
        "svc/monitoring-kube-prometheus-prometheus 9090:9090). "
        "PromQL from this run is not stored as a permalink."
    ),
    "validate_recovery": "read-only Ready check on the named pod selector",
}


def blast_radius(
    services: List[str],
    graph: Optional[ServiceGraph] = None,
) -> Dict[str, List[str]]:
    """Alert labels plus one-hop Jaeger neighbors. Labels, not a model story."""
    alerted = [s for s in services if s]
    one_hop: List[str] = []
    seen = set(alerted)
    if graph is not None:
        for svc in alerted:
            for neighbor in sorted(graph.neighbors(svc)):
                if neighbor not in seen:
                    seen.add(neighbor)
                    one_hop.append(neighbor)
    return {"alerted": alerted, "one_hop_neighbors": one_hop}


_POD_REPLICA = re.compile(r"-[a-z0-9]{5,10}-[a-z0-9]{5}$")


def deploy_targets(names: List[str]) -> List[str]:
    """Names that can be restart/rollback targets. Drop ReplicaSet pod ids."""
    return [n for n in names if n and not _POD_REPLICA.search(n)]


def suggested_actions(level: str, services: List[str]) -> List[str]:
    """Hard rules. Webhook never executes writes."""
    if level == "LOW":
        return [
            "do not treat this card as a root cause",
            "gather live evidence (list_pods / get_recent_events) before acting",
        ]
    actions = [
        "inspect events and logs for the alerted services",
        "call validate_recovery after any change",
    ]
    for svc in deploy_targets(services):
        actions.append(
            f"propose restart_deployment / rollback_deployment on {svc} "
            "after CLI y/n (webhook will not execute writes)"
        )
    return actions


def evidence_links(tools_used: Dict[str, int]) -> List[Dict[str, str]]:
    """Map tools that actually ran to a kubectl/UI entry. No fake permalinks."""
    out: List[Dict[str, str]] = []
    for name in sorted(tools_used or {}):
        how = _EVIDENCE_HOW.get(name)
        if how:
            out.append({"tool": name, "how": how, "calls": str(tools_used[name])})
    return out


def render_card(
    *,
    payload: Dict[str, Any],
    answer: str,
    stats: Dict[str, Any],
    query: str,
    edges: List[Tuple[str, str]],
    status: str = "ok",
    graph: Optional[ServiceGraph] = None,
) -> Dict[str, Any]:
    body, _footer = split_answer(answer)
    check = assess(query, answer, stats)
    services = alert_services(payload)
    root_cause: Optional[str]
    if check.level == "LOW":
        root_cause = None
        note = "evidence LOW — suspected root cause not published"
    else:
        root_cause = body[:2000]
        note = ""
    tools = dict(stats.get("tool_calls") or {})
    return {
        "status": status,
        "group_key": payload.get("groupKey"),
        "services": services,
        "related_via_topology": [list(e) for e in edges],
        "blast_radius": blast_radius(services, graph),
        "suggested_actions": suggested_actions(check.level, services),
        "evidence_links": evidence_links(tools),
        "suspected_root_cause": root_cause,
        "evidence_level": check.level,
        "evidence_reasons": list(check.reasons),
        "tools": tools,
        "note": note,
    }


def degrade(payload: Dict[str, Any], error: str, path: Optional[Path] = None) -> Path:
    dest = path or DEGRADE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error": error,
        "raw": payload,
    }
    line = json.dumps(record, ensure_ascii=False)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(f"[webhook degrade] {error}", file=sys.stderr)
    print(line)
    return dest


class Gate:
    """Idempotency (groupKey) + per-service rate limit. In-memory."""

    def __init__(
        self,
        *,
        idempotency_ttl_s: float = DEFAULT_IDEMPOTENCY_TTL_S,
        rate_limit_s: float = DEFAULT_RATE_LIMIT_S,
    ):
        self.idempotency_ttl_s = idempotency_ttl_s
        self.rate_limit_s = rate_limit_s
        self._groups: Dict[str, float] = {}
        self._services: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, group_key: str, services: List[str]) -> Optional[str]:
        now = _now()
        with self._lock:
            expired_g = [k for k, t in self._groups.items() if now - t > self.idempotency_ttl_s]
            for k in expired_g:
                del self._groups[k]
            expired_s = [k for k, t in self._services.items() if now - t > self.rate_limit_s]
            for k in expired_s:
                del self._services[k]

            if group_key and group_key in self._groups:
                return "duplicate"
            for svc in services:
                if svc in self._services:
                    return "rate_limited"
            if group_key:
                self._groups[group_key] = now
            for svc in services:
                self._services[svc] = now
        return None


class WebhookApp:
    def __init__(
        self,
        *,
        token: str,
        runner: Callable[[str], Tuple[str, Dict[str, Any]]],
        graph: Optional[ServiceGraph] = None,
        gate: Optional[Gate] = None,
        degrade_path: Optional[Path] = None,
    ):
        self.token = token
        self.runner = runner
        self.graph = graph or ServiceGraph()
        self.gate = gate or Gate()
        self.degrade_path = degrade_path or DEGRADE_PATH

    def handle(self, headers: Dict[str, str], body: bytes) -> Tuple[int, Dict[str, Any]]:
        if not authorized(headers, self.token):
            return 401, {"error": "unauthorized"}
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, {"error": "invalid json"}
        if not isinstance(payload, dict):
            return 400, {"error": "payload must be an object"}

        if (payload.get("status") or "").lower() == "resolved" and not firing_alerts(payload):
            return 200, {"status": "ignored", "reason": "resolved"}

        group_key = str(payload.get("groupKey") or "")
        services = alert_services(payload)
        blocked = self.gate.check(group_key, services)
        if blocked:
            return 200, {"status": blocked, "group_key": group_key, "services": services}

        edges = self.graph.edges_among(services) if services else []
        query = build_query(payload, edges)
        try:
            answer, stats = self.runner(query)
        except Exception as exc:
            degrade(payload, str(exc), path=self.degrade_path)
            return 200, {
                "status": "degraded",
                "group_key": group_key,
                "services": services,
                "error": str(exc),
                "raw_alert_logged": True,
            }
        card = render_card(
            payload=payload,
            answer=answer,
            stats=stats,
            query=query,
            edges=edges,
            graph=self.graph,
        )
        return 200, card


def _make_handler(app: WebhookApp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("[webhook] " + (fmt % args) + "\n")

        def _send(self, code: int, payload: Dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/healthz"}:
                self._send(200, {"ok": True})
                return
            if path == "/metrics":
                # Prometheus scrape endpoint (agent runtime metrics).
                data, content_type = render_metrics()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path not in {"/", "/webhook"}:
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            headers = {k: v for k, v in self.headers.items()}
            code, payload = app.handle(headers, body)
            self._send(code, payload)

    return Handler


class AgentRunner:
    """One ReAct agent on a background event loop so the HTTP thread can call it."""

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig(writes_interactive=False)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.agent = ReActAgent(self.config)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        self.thread.start()
        fut = asyncio.run_coroutine_threadsafe(self.agent.initialize(), self.loop)
        fut.result(timeout=60)

    def run(self, query: str) -> Tuple[str, Dict[str, Any]]:
        fut = asyncio.run_coroutine_threadsafe(self.agent.run(query), self.loop)
        answer = fut.result(timeout=180)
        return answer, dict(self.agent.stats)

    def stop(self) -> None:
        try:
            fut = asyncio.run_coroutine_threadsafe(self.agent.cleanup(), self.loop)
            fut.result(timeout=15)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)


def main() -> None:
    parser = argparse.ArgumentParser(description="Citrus Alertmanager webhook")
    parser.add_argument("--host", default=os.getenv("CITRUS_WEBHOOK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CITRUS_WEBHOOK_PORT", "8088")))
    parser.add_argument(
        "--token",
        default=os.getenv("CITRUS_WEBHOOK_TOKEN", ""),
        help="Bearer / X-Webhook-Token value. Required.",
    )
    args = parser.parse_args()
    if not args.token:
        print("Error: set CITRUS_WEBHOOK_TOKEN or pass --token", file=sys.stderr)
        sys.exit(1)

    runner = AgentRunner()
    runner.start()
    app = WebhookApp(token=args.token, runner=runner.run)
    server = ThreadingHTTPServer((args.host, args.port), _make_handler(app))
    print(f"webhook listening on http://{args.host}:{args.port}/webhook", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping webhook")
    finally:
        server.server_close()
        runner.stop()


if __name__ == "__main__":
    main()
