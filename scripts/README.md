# Scripts

```
scripts/
├── deployment/     # PowerShell IaC (Helm monitoring + otel-demo)
├── canary/         # metric-based canary + rollback
└── tests/          # bats (canary wrapper) + load_test_llm.py
```

The agent and MCP server live in `components/`, not here.

## Deployment

```powershell
.\scripts\deployment\0-deploy-all.ps1
```

Details: [deployment/README.md](deployment/README.md).

## Canary

Decision script (replica-ratio approximation, not mesh weighting):

```bash
pip install -r scripts/canary/requirements.txt
python scripts/canary/canary-deploy.py \
  --service recommendationservice \
  --baseline ghcr.io/user/app:v1.0 \
  --canary ghcr.io/user/app:v1.1
```

Wrapper + mock mode:

```bash
MOCK_MODE=1 \
MOCK_ERROR_RATIO=1.5 \
bash scripts/canary/canary-wrapper.sh --service test --baseline v1 --canary v2

bats scripts/tests/canary-wrapper.bats
```

## LLM load test

```bash
python scripts/tests/load_test_llm.py
```

See [docs/VLLM.md](../docs/VLLM.md).

## Related docs

- [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md)
- [docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)
- [docs/README.md](../docs/README.md)
