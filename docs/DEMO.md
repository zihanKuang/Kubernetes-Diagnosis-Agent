# Local demo

Cluster must be running (`kubectl get ns citrus` works). If not, start Kubernetes first, then deploy.

## A. Platform up

```powershell
.\scripts\deployment\0-deploy-all.ps1
kubectl get pods -n citrus
```

Expect otel-demo + monitoring pods Running (may take several minutes).

## B. Agent (stdio) smoke test

```powershell
# First run: install packages (after this, you can run from any directory)
cd components
pip install -e ".[test]"
pip install -e "mcp-server[test]"

# Configure agent_cli/.env (required):
#   DEEPSEEK_API_KEY=sk-your-key-here

# Smoke the agent
python -m agent_cli "List pods in citrus and summarize unhealthy ones."
```

Expect: tool calls (`list_pods` / `get_pod_status` / …) then a short summary.

## C. Chaos + RCA (main showcase)

```powershell
# once per cluster
.\infra\chaos\install-chaos-mesh.ps1

.\infra\chaos\run-demo.ps1

cd components
python -m agent_cli "What just happened to the frontend pods in citrus? Short RCA, then validate_recovery."
```

Cleanup (deletes the PodChaos resource in the cluster; the YAML file stays):

```powershell
kubectl delete -f infra/chaos/pod-kill-frontend.yaml
```

## E. Labeled eval

Run the automated eval on 3 scenarios (healthy baseline + frontend kill + checkout kill):

```powershell
# List scenarios (no cluster required)
python -m agent_cli.eval_rca --list

# Dry run (does not execute)
python -m agent_cli.eval_rca --dry-run

# Full eval (needs cluster + Chaos Mesh + DEEPSEEK_API_KEY)
# First: no-memory baseline
python -m agent_cli.eval_rca --all --no-memory

# Second: memory on (agent reads postmortems)
python -m agent_cli.eval_rca --all

# Compare the two runs
python -m agent_cli.eval_rca --compare data\eval\rca_eval_<timestamp1>.json data\eval\rca_eval_<timestamp2>.json
```

Details: [EVAL.md](EVAL.md).

## F. Webhook (Alertmanager)

Start the webhook HTTP server so Alertmanager can trigger RCA:

```powershell
# Option 1: env var
$env:CITRUS_WEBHOOK_TOKEN="change-me-webhook-secret"
python -m agent_cli.webhook --port 9093

# Option 2: flag
python -m agent_cli.webhook --port 9093 --token "change-me-webhook-secret"

# Exercise the webhook from another terminal
curl -X POST http://localhost:9093/webhook `
  -H "Authorization: Bearer change-me-webhook-secret" `
  -H "Content-Type: application/json" `
  -d '@infra/alerting/sample-alert.json'
```

The webhook:

- checks the token (idempotency + rate limit)
- runs the agent for RCA
- publishes only HIGH/MEDIUM evidence
- degrades by saving the raw payload if the agent fails

## G. Optional HTTP MCP

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
