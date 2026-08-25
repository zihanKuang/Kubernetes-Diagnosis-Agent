# RCA eval (hard-rule scorer)

Three labeled scenarios. The scorer does **not** call an LLM. It checks whether
the answer body contains the phrases in `eval_scenarios.py`.

| id | inject | what a hit means |
|----|--------|------------------|
| `healthy-baseline` | nothing | agent does **not** invent an incident |
| `pod-kill-frontend` | existing PodChaos | names **frontend** + a kill/terminate verb |
| `pod-kill-checkout` | new PodChaos | names **checkout** + a kill/terminate verb |

`healthy-baseline` is first on purpose so leftover kill events do not pollute it.

## Commands

```powershell
cd components
# catalog only — no cluster, no API key
python -m agent_cli.eval_rca --list
python -m agent_cli.eval_rca --dry-run

# live (needs cluster + Chaos Mesh + DEEPSEEK_API_KEY)
python -m agent_cli.eval_rca --all --no-memory   # freeze a baseline
python -m agent_cli.eval_rca --all               # memory on (ablation)
python -m agent_cli.eval_rca --compare ..\data\eval\baseline.json ..\data\eval\rca_eval_<stamp>.json
```

`--all` writes `data/eval/rca_eval_<timestamp>.json` (gitignored).

`--skip-inject` runs the agent against whatever is already in the cluster
(useful if you already ran `.\infra\chaos\run-demo.ps1`).

`--no-memory` is how the baseline was frozen: same agent, no JSONL hints.

## Frozen baseline (2026-08-13, DeepSeek, memory off)

| scenario | hit | evidence | duration_s | tool_calls |
|----------|-----|----------|------------|------------|
| healthy-baseline | True | HIGH | 14.31 | 7 |
| pod-kill-frontend | True | HIGH | 8.46 | 5 |
| pod-kill-checkout | True | HIGH | 8.53 | 4 |

Root-cause hit rate: **3/3**. File: `data/eval/baseline.json` (gitignored).

`healthy-baseline` HIT here because the cluster was *not* clean: `frontend-proxy` was crash-looping (Ready=False, 2293 restarts). The phrase rules accept "healthy/running/ready" in the body; the agent named the real unhealthy pod and did not invent Chaos Mesh. That is a HIT on the ruler, not a claim that the namespace was fine.

## Memory ablation (same cluster, memory on)

Postmortems go to JSONL only when the evidence stamp is not LOW. The next query
gets keyword-overlap hints. Live rerun 2026-08-13 vs the frozen baseline:

| scenario | hit | evidence | duration_s (base → mem) | tools (base → mem) |
|----------|-----|----------|-------------------------|--------------------|
| healthy-baseline | True | HIGH | 14.31 → 23.59 | 7 → 10 |
| pod-kill-frontend | True | HIGH | 8.46 → 7.92 | 5 → 4 |
| pod-kill-checkout | True | HIGH | 8.53 → 7.18 | 4 → 4 |

Hit rate stayed **3/3**. Memory did inject (1 hint on frontend, 2 on checkout).
frontend used one fewer tool; checkout was flat. healthy-baseline used *more*
tools because the previous eval's Killing events were still in the 15-minute
window, and that scenario starts with an empty JSONL — so the extra work is
not a memory effect.

Honest line for a resume: **n=3 上未见可归因于记忆的提升**. One-tool movement
on a single scenario is noise.

## Live rerun + OBJECT ablation (2026-08-21, DeepSeek, memory off)

Cluster still had `frontend-proxy` crash-looping (~2616 restarts). Leftover
Chaos Mesh events from earlier runs were in the 10-minute window.

### Kill scenarios (the number that belongs on a resume)

| condition | frontend kill | checkout kill | evidence | duration_s |
|-----------|---------------|---------------|----------|------------|
| current (OBJECT + correlation prompt) | HIT | HIT | HIGH | 8.32 / 6.81 |
| legacy (no OBJECT column, no correlation rules) | HIT | HIT | HIGH | 8.66 / 7.37 |

**2/2** on injected pod-kills either way. Ceiling is already the phrase hit.

### Cross-contamination (`checkout-after-frontend`)

Inject frontend kill, delete the CR, then inject checkout kill. Ask only about checkout.

| condition | hit | stale_mix | duration_s | tools |
|-----------|-----|-----------|------------|-------|
| current | HIT | false | 11.28 | 4 |
| legacy | HIT | false | 11.72 | 5 |

n=1: both named checkout; neither treated leftover frontend as *this* incident.
Current answer cited `Pod/checkout-…` from the OBJECT column and matched
`list_pods`. That is a quality difference, not a hit-rate lift. Do **not** write
“OBJECT 使命中率提升”.

### Catalog `healthy-baseline`

Current run **MISS** because the body contained `PodChaos` (forbidden phrase)
while correctly separating recovered chaos from the real `frontend-proxy`
crash-loop. Legacy **HIT** by avoiding that token. The miss is a scorer artifact,
not a worse RCA. Do not quote today’s catalog as 3/3 or as a regression.

```powershell
python -m agent_cli.eval_rca --stale-trap --no-memory --ablation current
python -m agent_cli.eval_rca --stale-trap --no-memory --ablation legacy
python -m agent_cli.eval_rca --all --no-memory --ablation current
python -m agent_cli.eval_rca --all --no-memory --ablation legacy
```

## What the numbers mean

- **root-cause hit**: phrase check on the answer *body* (the evidence footer is stripped).
- **evidence HIGH/MEDIUM/LOW**: the Step-1 stamp from tool-call counts. Separate metric.
- **expected tools missing**: reported, does not flip the hit by itself.

A hit is not “this is a good RCA”. It is “the agent named the labeled fault”.
Wrong-service stories fail (checkout scenario + frontend-only answer = miss).

## Unit tests (no cluster)

```powershell
cd components
python -m pytest agent_cli/tests/test_eval_score.py agent_cli/tests/test_eval_compare.py -q
```
