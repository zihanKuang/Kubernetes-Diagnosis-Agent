# T001–T080 checkpoint

Status key: `done` implementation complete and tested; `offline-verified` checked without a paid model; `blocked` waiting on authorization or license; `not-run` live experiment.

Implementation complete and experiment complete are tracked separately.

## Phase 1 T001–T008

| ID | Status | Notes |
|----|--------|-------|
| T001 | done | Workspace snapshot in WORKSPACE_BASELINE.md. No secrets. |
| T002 | done | Eight polluting issues were still present; fixed in T009–T018. |
| T003 | offline-verified | 2026-10-07: `python -m pytest agent_cli/tests mcp-server/tests -q` from `components/` on Anaconda Python 3.12.7 → **283 passed**. Do not quote an older 262. |
| T004 | done | Arm A = DEFAULT_SRE + legacy tools; not the ablation prompt. ARM_A.md. |
| T005 | done | `skill_bench/conditions.py` SHARED. |
| T006 | done | Bench sets memory_enabled=False; test asserts user postmortem file unchanged. |
| T007 | done | WIN_RULES.md; no composite score. |
| T008 | done | This file. |

## Phase 2 T009–T020

| ID | Status | Notes |
|----|--------|-------|
| T009–T018 | done | Runtime/agent fixes + `test_skill_runtime_fixes.py`. |
| T019 | done | Those tests check calls, targets, stage status — not only skill id. |
| T020 | offline-verified | Shared infra; A strategy frozen as default prompt. Full pytest this session. |

## Phase 3 T021–T032

| ID | Status | Notes |
|----|--------|-------|
| T021–T032 | done | 12 synthetic cases in `catalog.py`; 8 dev / 4 holdout. Not captured incidents. |

## Phase 4 T033–T042

| ID | Status | Notes |
|----|--------|-------|
| T033–T041 | done | `ReplayToolProvider`; writes denied; unmatched recorded. |
| T042 | done | Selector replay is deterministic in tests. Fake LLM ≠ quality. |

## Phase 5 T043–T050

| ID | Status | Notes |
|----|--------|-------|
| T043 | done | Commit d8af309 locked. SOURCE.md. |
| T044 | done | GitHub license=null, LICENSE 404. Original text not redistributed. |
| T045–T049 | done | Capability map + Citrus-authored adapted SKILL.md. |
| T050 | offline-verified | `skill_bench freeze`. Holdout not used to edit prompts. |

## Phase 6 T051–T058

| ID | Status | Notes |
|----|--------|-------|
| T051–T057 | done | `scorer.py` + `report.py`. No self-grade. Reviewer LLM unused. |
| T058 | done | `test_skill_bench.py` scorer fixtures. |

## Phase 7 T059–T064

| ID | Status | Notes |
|----|--------|-------|
| T059–T062 | done | `python -m agent_cli.skill_bench` list/plan/preflight/run/report. |
| T063 | offline-verified | Pytest this session. |
| T064 | done | preflight: ok_offline, ok_live_quality=false. |

## Phase 8 T065–T072

| ID | Status | Notes |
|----|--------|-------|
| T065 | done | deepseek-v4-flash / DeepSeek. No credentials in reports. |
| T066 | done | Budget 180 diagnoses; 759 LLM calls actually used. Price unknown, not invented. |
| T067 | done | Smoke 9/9 scored, tools+parseable JSON, replay only. |
| T068 | done | Scorer negation fix after smoke; smoke kept separate from round-1. |
| T069 | done | Seed 20260315, interleaved, concurrency 1. |
| T070 | done | 180/180 scored, 0 dropped failures. |
| T071 | done | Tokens, LLM calls, replay duration recorded. Replay time ≠ cluster time. |
| T072 | done | Same scorer for all arms. See RESULTS.md. |

## Phase 9 T073–T080

| ID | Status | Notes |
|----|--------|-------|
| T073–T078 | done | RESULTS.md. Default stays legacy. |
| T079 | done | LIVE_CLUSTER.md written, not executed. |
| T080 | done | Docs + raw jobs + this checkpoint. |
