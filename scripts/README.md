# Scripts

```
scripts/
├── deployment/     # PowerShell IaC (Helm monitoring + otel-demo)
└── tests/          # load_test_llm.py
```

The agent and MCP server live in `components/`, not here.

## Deployment

```powershell
.\scripts\deployment\0-deploy-all.ps1
```

Details: [deployment/README.md](deployment/README.md).

## LLM load test

```bash
python scripts/tests/load_test_llm.py
```

See [docs/VLLM.md](../docs/VLLM.md).

## Related docs

- [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md)
- [docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)
- [docs/README.md](../docs/README.md)
