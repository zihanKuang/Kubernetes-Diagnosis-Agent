# External candidate lock (T043–T049)

- Repo: https://github.com/geored/sre-skill
- File: `debugging-kubernetes-incidents/SKILL.md`
- Commit: `d8af309ec3fe7d5d34cf1d352e7ea05bba5ae844` (2025-11-10)
- URL: https://github.com/geored/sre-skill/blob/d8af309ec3fe7d5d34cf1d352e7ea05bba5ae844/debugging-kubernetes-incidents/SKILL.md
- GitHub `license` field: `null`
- `LICENSE` file: missing (HTTP 404)

Original SKILL.md is **not** copied into this repository and is **not** installed into a personal Cursor/Codex skill directory.

Arm C uses Citrus-authored `components/agent_cli/skill_bench/adapted_instructions/*/SKILL.md`.
Results must be described as **外部 Skill 的 Citrus 适配版**.

Observed method (summary, not a copy of the upstream file):

1. Read-only investigation
2. Triage → collect pods/events/logs/metrics → timeline → cause → recommendations
3. Events before treating logs as the first clue
4. Previous-container logs and `kubectl top` are part of the candidate method

Citrus mapping:

| Candidate | Citrus | Support |
|-----------|--------|---------|
| kubectl get pods | list_pods | full |
| describe / pod yaml | get_pod_status | partial (no YAML, no node dump) |
| kubectl get events | get_recent_events | partial (fixed namespace window) |
| kubectl logs --tail | get_pod_logs | partial |
| kubectl logs --previous | — | unsupported |
| kubectl top | query_prometheus | not in replay fixtures |
| TLS / Service / network | — | unsupported |
| remediations | gated writes | denied in bench |

Author claims such as “production ready” were not treated as verified quality.
