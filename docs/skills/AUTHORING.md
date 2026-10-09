# Authoring Citrus Skills

Citrus Skills are **this project's** diagnostic workflows. They are not Cursor or Codex personal skills. The agent only loads:

- builtin packages under `components/agent_cli/skills/builtin/`
- extra roots you pass with `--skill-dir` / `CITRUS_SKILL_DIRS`

It will not scan the repo, your home directory, or other apps' skill folders.

## Package layout

```
my-skill/
  SKILL.toml      # required metadata
  workflow.toml   # required stage plan
  SKILL.md        # investigation notes (loaded only when the skill is selected)
```

Validate without a model key or cluster:

```powershell
cd components
python -m agent_cli.skills validate .\path\to\parent-or-package
python -m agent_cli.skills list --skill-dir .\path\to\parent-or-package
python -m agent_cli.skills show my-skill
python -m agent_cli.skills preview --query "Is anything unhealthy in citrus?"
python -m agent_cli.skills preview --query "Why is it unhealthy?" --observation-file .\agent_cli\tests\fixtures\skills\preview-oom.json
```

Preview compiles the same stage plan as runtime. It does not call tools. Without an observation file, skill choice after scan is still marked as needing live FOCUS evidence.

## SKILL.toml

Required fields:

| field | meaning |
|-------|---------|
| `schema_version` | Manifest schema. First version is `"1"`. Unsupported versions are rejected. |
| `id` | Stable skill id (not the eval scenario id). |
| `version` | Skill content version (`1.0.0`). Same id+version cannot be registered twice. |
| `description` | One-line purpose. |
| `entrypoints` | Subset of `cli`, `interactive`, `webhook`, `eval`. |
| `workflow_id` | Workflow this skill runs. |
| `instructions` | Path to the Markdown file, relative to the package. Must stay inside the package. |
| `enabled` | `false` disables auto-selection. `--skill` on a disabled id errors. |

Optional: `tags`, `required_capabilities`, `workflow_file` (default `workflow.toml`).

Routing (deterministic, no extra LLM call):

```toml
[routing]
intents = ["fault_diagnosis", "health_check", "recovery_check", "unknown"]
entrypoints = ["cli", "interactive", "webhook", "eval"]
priority = 50
specificity = 60
fallback = false
match = "all"          # or "any"

[[routing.observations]]
field = "state"
op = "contains"
value = "CrashLoopBackOff"

[[routing.exclude]]
field = "last_termination"
op = "contains"
value = "OOMKilled"
```

Supported condition fields: `intent`, `entrypoint`, `scan_status`, `focus_count`, `phase`, `state`, `waiting_reason`, `last_termination`, `event_reason`, `event_reasons`, `ready`, `restarts`, `present`, `has_focus`.

Supported operators: `eq`, `ne`, `in`, `not_in`, `contains`, `gt`, `gte`, `lt`, `lte`, `exists`, `not_exists`. Arbitrary expressions are rejected.

`scan_status` is `not_scanned` | `success` | `empty` | `failed`. A failed or missing scan cannot select `healthy`.

Do not put eval answers, `must_contain`, manifests, or scenario ids in the skill. Those stay in `eval_scenarios.py`.

## Versions

- Manifest `schema_version` is the file format. Skill `version` is the content.
- There is no dependency solver. If two versions of the same id exist, `get(id)` requires an explicit version.
- Routing ranks by fallback (non-fallback first), specificity, priority, version, then id. File discovery order is not used.

## Minimal new skill

Copy `builtin/crashloop/`, change `id` / routing observations / `SKILL.md`, and compose the same fragments (see [WORKFLOWS.md](WORKFLOWS.md)). You should not need to edit `router.py` or `agent.py`.

Default diagnostic skills are read-only. Writes need an explicit enabled write workflow **and** the existing human gate.

Example that only replaces inspect (no Agent/router edits): `agent_cli/tests/fixtures/skills/crashloop-status-only/`.
