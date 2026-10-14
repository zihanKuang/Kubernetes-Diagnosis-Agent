# Alertmanager webhook (incident card)

A small HTTP service **outside** the ReAct loop. Auth, idempotency, rate limit,
degrade, and a fixed card template are the engineering story. The model only
fills the RCA body after live tools run. Blast radius, suggested actions, and
evidence links are filled by **hard rules**, not a second LLM.

## Run (host process first)

```powershell
cd components
$env:CITRUS_WEBHOOK_TOKEN="change-me"
python -m agent_cli.webhook --host 0.0.0.0 --port 8088
```

`--host 0.0.0.0` is required if in-cluster Alertmanager will POST via
`host.docker.internal`. Keep this process running.

## Wire Alertmanager (optional)

Helm values enable Alertmanager and point it at
`http://host.docker.internal:8088/webhook` with the same Bearer token.
Apply the demo rule (fires when a `citrus` pod is not Ready — `frontend-proxy`
on this cluster often already is):

```powershell
kubectl apply -f infra/alerting/citrus-pod-not-ready.yaml
```

If `host.docker.internal` is not routed from the cluster, **curl is the
supported degrade path** — do not put the Agent in-cluster just for this:

```powershell
curl -s -X POST http://127.0.0.1:8088/webhook `
  -H "Authorization: Bearer change-me" `
  -H "Content-Type: application/json" `
  -d '{"groupKey":"{}/{alertname=PodNotReady}","status":"firing","commonLabels":{"service":"frontend","alertname":"PodNotReady"},"alerts":[{"status":"firing","labels":{"service":"frontend","alertname":"PodNotReady"},"annotations":{"summary":"frontend is not ready"}}]}'
```

## Card fields

| field | source |
|-------|--------|
| `suspected_root_cause` | Agent answer body; **null** when evidence is LOW |
| `blast_radius.alerted` | Alertmanager `service` / `app` / … labels |
| `blast_radius.one_hop_neighbors` | Jaeger adjacency, one hop (empty if Jaeger is down) |
| `suggested_actions` | Rules: LOW → do not treat as RCA; otherwise inspect + propose gated writes (not executed) |
| `evidence_links` | Tools that actually ran → kubectl / Prometheus UI hint. No fake PromQL permalinks |
| `diagnosis_status` | Process field from the evidence stamp. Usually `not_reviewed` (reviewer is off this path) |
| `recovery_status` | `pass` / `fail` / `unknown` from `validate_recovery`, not lasting SLI recovery |

## Gates

| gate | behavior |
|------|----------|
| auth | `Authorization: Bearer` or `X-Webhook-Token`; empty secret fails closed |
| idempotency | same `groupKey` within 15 minutes → `duplicate`, no second agent run |
| rate limit | same `service` label within 5 minutes → `rate_limited` |
| degrade | agent exception → HTTP 200 + raw alert appended to `data/webhook/degraded.jsonl` and stdout |
| LOW stamp | `suspected_root_cause` is `null`; the card says the analysis is not published |

## Topology

If several services fire in one payload, `agent_cli/topology.py` asks Jaeger
`/api/dependencies` and attaches undirected edges as a **hint** in the query.
The same neighbors fill `blast_radius.one_hop_neighbors`. Jaeger down → empty
adjacency, alerts still processed. Not Neo4j.

## Writes

Webhook sessions set `writes_interactive=False`. `restart_deployment` /
`scale_deployment` / `rollback_deployment` are denied even if the model proposes them.

Alert labels (`service`, `alertname`, …) are routing clues when skill routing is `auto` (`CITRUS_SKILL_ROUTING=auto` on the webhook process). They are not a verified root cause and cannot authorize writes. Default routing is still **legacy**.
