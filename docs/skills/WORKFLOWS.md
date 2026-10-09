# Authoring Citrus Skill workflows

A workflow is an ordered list of stages. First version is compose + replace, not a general DAG language.

## Processors

Manifests may only name these processors:

| processor | job | required outputs |
|-----------|-----|------------------|
| `scan` | `list_pods` (and events for RCA) | `observation_snapshot` |
| `inspect` | status / logs on FOCUS objects | `inspect_records` |
| `verify` | `validate_recovery` | `recovery_verdict` (protected) |
| `synthesize` | JSON claims for `publish_diagnosis` | `diagnosis_draft` (protected) |

You cannot import an arbitrary Python function from TOML.

## Compose

```toml
id = "crashloop"
version = "1.0.0"
compose = [
  "fragment:scan",
  "fragment:inspect-crashloop",
  "fragment:recovery",
  "fragment:synthesize",
]
```

Fragments live in `agent_cli/skills/builtin/fragments/`. Unknown, recursive, or duplicate stage ids fail at compile time — before any tool runs.

`python -m agent_cli.skills show crashloop` and `preview` use the same `compile_workflow` as runtime.

## Replace

Replace a stage by its stable id. Other stages stay as composed.

```toml
compose = ["fragment:scan", "fragment:inspect-crashloop", "fragment:recovery", "fragment:synthesize"]

[[replace]]
stage_id = "inspect"
with_fragment = "fragment:inspect-status-only"
```

Or inline:

```toml
[[replace]]
stage_id = "inspect"

[replace.stage]
id = "inspect"
processor = "inspect"
tools = ["get_pod_status"]
outputs = ["inspect_records"]
complete_tools = ["get_pod_status"]
```

Compatibility: the replacement must keep the processor contract. You cannot replace `verify` with `inspect` and still claim recovery was verified. You cannot replace `synthesize` to skip publish.

Offline coverage: `tests/test_skill_workflows.py` and `tests/fixtures/skills/crashloop-status-only/`.

## Stage results

`success` | `failed` | `skipped` | `capability_unavailable` | `budget_exhausted`

A failed scan does not become a healthy conclusion. Optional missing tools skip and record; required missing tools go to a controlled unknown output. There is no unbounded retry and no silent workflow hop to hide failure.

Enter conditions use the same operator set as routing. Health scans skip inspect/verify when `focus_count` is 0.

Complete conditions require successful tool records (not a model saying “done”, not denied calls).

## Shared budget

Max reasoning steps, tool attempts, and stage transitions are shared for the whole run. Re-routing does not reset them. Combining more skills cannot bypass the cap.

## Protected runtime constraints

Regardless of which skill is selected:

- FOCUS / `from_obs` authorization
- gated writes + audit
- `publish_diagnosis` / evidence stamp / trace finalize

`--skill <id>` only locks selection. It does not skip scan, evidence, tool allow-list, or write approval.
