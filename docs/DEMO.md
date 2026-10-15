# Local demo

Verified live on 2026-10-15 (Docker Desktop Kubernetes, DeepSeek). Commands
below ran; the caveats in section C are from that run, not theory.

Cluster must be running (`kubectl get ns citrus` works). If Docker Desktop is
stopped, start it and wait until `kubectl get nodes` shows Ready. Then deploy
if the `citrus` namespace is missing.

Default agent routing is **legacy**. Citrus Skills are opt-in
(`--skill-routing auto`). Do not present Skills as the production default;
the replay bench kept legacy. See [skills/README.md](skills/README.md).

## A. Platform up

```powershell
.\scripts\deployment\0-deploy-all.ps1
kubectl get pods -n citrus
```

Expect otel-demo + monitoring pods Running (may take several minutes).
`frontend-proxy` may CrashLoop on this cluster; the chaos demo targets
**frontend**, not the proxy.

## B. Agent (stdio) smoke test

```powershell
# First run: install packages (after this, you can run from any directory)
cd components
pip install -e ".[test]"
pip install -e "mcp-server[test]"

# Configure agent_cli/.env (required):
#   DEEPSEEK_API_KEY=sk-your-key-here

# Smoke the agent (legacy routing)
python -m agent_cli "List pods in citrus and summarize unhealthy ones."
```

Expect: tool calls (`list_pods` / `get_pod_status` / …), then either a short
summary or structured Facts/Symptoms claims, then an `Evidence check:` footer.
`diagnosis: not_reviewed` is normal — the independent reviewer is not on the
publish path. DeepSeek may return HTTP 503; `ModelClient` retries.

Offline Skill preview (no API key, no cluster tools):

```powershell
python -m agent_cli.skills list
python -m agent_cli.skills preview --query "Is anything unhealthy in citrus?"
```

## C. Chaos + RCA (main showcase)

Keep a watch window open so the kill is visible (heal is often a few seconds):

```powershell
kubectl get pods -n citrus -l app.kubernetes.io/component=frontend -w
```

```powershell
# once per cluster
.\infra\chaos\install-chaos-mesh.ps1

.\infra\chaos\run-demo.ps1

cd components
python -m agent_cli "What just happened to the frontend pods in citrus? Short RCA, then validate_recovery."
```

What actually happens on a good run:

- `run-demo.ps1`: old frontend pod name disappears, a new name is Ready in ~25s
  (2026-10-15: `…-68755` → `…-t9b47`).
- Agent (legacy): `list_pods`, `get_recent_events`, `get_pod_status`,
  `validate_recovery`. Logs contain Killing / SuccessfulCreate and a PASS.
- **Published body may still say** `Diagnosis draft invalid; unreviewed free
  text was not published` if a claim mixes restart/OOM/recovery. That is the
  publisher, not a missed kill. Point at the tool log, not only the footer.
- Footer: `Evidence check: HIGH`, `diagnosis: not_reviewed`, `recovery: pass`.

Do **not** use `--skill-routing auto` as the camera money shot on this
self-heal. A live 2026-10-15 auto run routed to `readiness`, denied
`validate_recovery` six times (`not enabled for the current skill stage`), and
hit `max_steps=10` with `recovery: unknown`. That matches the replay-bench
self-heal gap. Keep auto for a separate Skills preview, not the chaos close.

Cleanup (deletes the PodChaos resource in the cluster; the YAML file stays):

```powershell
kubectl delete -f infra/chaos/pod-kill-frontend.yaml
```

Kubernetes recreates the pod. The agent diagnoses and checks Ready; it does
not delete or restart pods under the default ServiceAccount.

## D. Labeled eval (8 scenarios)

Catalog: healthy baseline, two Chaos Mesh pod-kills, and five stuck-pod
fixtures (crash loop, image pull, OOM, failed probe, unschedulable). Phrase
hit means the answer named the labeled fault — not RCA accuracy.

```powershell
cd components
python -m agent_cli.eval_rca --list
python -m agent_cli.eval_rca --dry-run

# live (needs cluster + DEEPSEEK_API_KEY; Chaos Mesh only for the two pod-kills)
python -m agent_cli.eval_rca --all --no-memory
python -m agent_cli.eval_rca --all --no-memory --skill-routing auto --engine react --max-steps 10
```

Details: [EVAL.md](EVAL.md). Replay A/B/C (no live cluster):
[skills/benchmark/README.md](skills/benchmark/README.md).

## E. Webhook (Alertmanager)

Default listen port is **8088** (same as README / `CITRUS_WEBHOOK_PORT`).

```powershell
cd components
$env:CITRUS_WEBHOOK_TOKEN="change-me-webhook-secret"
python -m agent_cli.webhook --host 0.0.0.0 --port 8088

# Exercise the webhook from another terminal
curl -X POST http://localhost:8088/webhook `
  -H "Authorization: Bearer change-me-webhook-secret" `
  -H "Content-Type: application/json" `
  -d '@infra/alerting/sample-alert.json'
```

The webhook:

- checks the token (idempotency + rate limit)
- runs the agent for RCA (`CITRUS_SKILL_ROUTING=auto` uses the same router)
- publishes only HIGH/MEDIUM evidence
- records `diagnosis_status` / `recovery_status` on the card (usually
  `not_reviewed`)
- degrades by saving the raw payload if the agent fails

## F. Optional HTTP MCP

Image tag in the manifests is **mcp-server:v4**.

```powershell
cd components\mcp-server
docker build -t mcp-server:v4 .
# Kind: kind load docker-image mcp-server:v4
# Docker Desktop K8s: image usually already visible

kubectl apply -f ..\..\infra\rbac\mcp-server-rbac.yaml
kubectl apply -f ..\..\infra\manifests\mcp-server-secret.yaml
kubectl apply -f ..\..\infra\manifests\mcp-server-service.yaml
kubectl apply -f ..\..\infra\manifests\mcp-server-networkpolicy.yaml
kubectl apply -f ..\..\infra\manifests\mcp-server-deployment.yaml

kubectl port-forward -n citrus svc/mcp-server 8080:8080
curl http://127.0.0.1:8080/health

cd ..
$env:MCP_AUTH_TOKEN="change-me-citrus-mcp"
python -m agent_cli --mcp-url http://127.0.0.1:8080/mcp "List pods"
```
