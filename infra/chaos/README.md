# One-shot chaos + agent reminder (after platform is up)

```powershell
# 0) cluster + citrus namespace already deployed
kubectl get pods -n citrus

# 1) chaos (once)
.\infra\chaos\install-chaos-mesh.ps1

# 2) inject + wait
.\infra\chaos\run-demo.ps1

# 3) diagnose
cd components
python -m agent_cli "What happened to frontend in citrus? RCA + validate_recovery."

# or the labeled eval (hard-rule score; pod-kills need Chaos Mesh,
# infra/faults/ fixtures do not)
python -m agent_cli.eval_rca --all
```

See [docs/EVAL.md](../../docs/EVAL.md). Stuck-pod fixtures (crash loop, image pull, OOM, failed probe, unschedulable) live in `infra/faults/`. Delete every fixture with:

```powershell
kubectl delete deploy -n citrus -l citrus.dev/fault-fixture=true
```
