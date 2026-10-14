# Preflight (T064)

Date: 2026-10-07

- Offline tests: `cd components && python -m pytest agent_cli/tests mcp-server/tests -q` → **283 passed**.
- `python -m agent_cli.skill_bench preflight` → `ok_offline=true`, `ok_live_quality=false`.
- Fake-model run: 12 cases × 3 arms × 1 repeat = 36 diagnoses, 0 infrastructure failures, `quality_valid=0`.
- Plan file: 180 jobs for a later paid round (`--repeats 5`).
- B vs C crashloop sample: same skill id `crashloop`, instruction_bytes 1360 vs 2246 (adapted text actually loaded).
- Live blockers: no paid authorization; upstream GitHub license is null so C is a Citrus-authored adaptation, not a redistributed original file.
- Default CLI routing remains `legacy`.

This file is the **2026-10-07 offline snapshot**. Live smoke + round-1 ran
after it; do not quote `ok_live_quality=false` as the current experiment state.
See [RESULTS.md](RESULTS.md).
