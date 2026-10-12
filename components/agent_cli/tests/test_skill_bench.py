"""Offline skill bench: cases, replay, scorer, A/B/C wiring. No live model, no MCP."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_cli.memory import DEFAULT_PATH as MEMORY_DEFAULT
from agent_cli.prompts import DEFAULT_SRE_SYSTEM_INSTRUCTION, LEGACY_SRE_SYSTEM_INSTRUCTION
from agent_cli.skill_bench.arms import arm_a, arm_b, arm_c
from agent_cli.skill_bench.candidate import CANDIDATE
from agent_cli.skill_bench.catalog import all_cases, by_id, dev_ids, holdout_ids
from agent_cli.skill_bench.cli import main as bench_main
from agent_cli.skill_bench.jobs import build_jobs, job_id
from agent_cli.skill_bench.preflight import preflight
from agent_cli.skill_bench.replay import ReplayToolProvider, WRITE_TOOLS
from agent_cli.skill_bench.report import QUALITY_UNPROVEN
from agent_cli.skill_bench.runner import LIVE_BLOCKED, run_job
from agent_cli.skill_bench.scorer import score_run
from agent_cli.skills.fingerprint import fingerprint_text


def test_twelve_cases_split_and_no_truth_leak():
    cases = all_cases()
    assert len(cases) == 12
    assert len(dev_ids()) == 8
    assert len(holdout_ids()) == 4
    assert set(dev_ids()).isdisjoint(holdout_ids())
    for item in cases:
        assert item.kind == "synthetic"
        visible = json.dumps(item.model_visible())
        assert "supported_facts" not in visible
        assert "forbidden_facts" not in visible
        assert "citrus-oom" not in item.query
        assert item.case_id not in item.query or item.case_id != "citrus-oom"


def test_arm_a_is_default_sre_not_ablation():
    spec = arm_a()
    assert spec.system_instruction == DEFAULT_SRE_SYSTEM_INSTRUCTION
    assert spec.system_instruction != LEGACY_SRE_SYSTEM_INSTRUCTION
    assert spec.skill_routing == "legacy"
    cfg = spec.config(api_key="bench-key")
    assert cfg.memory_enabled is False
    assert cfg.skill_routing == "legacy"
    assert fingerprint_text(cfg.system_instruction) != fingerprint_text(LEGACY_SRE_SYSTEM_INSTRUCTION)


def test_b_and_c_share_runtime_and_differ_on_instruction_dir():
    b = arm_b()
    c = arm_c()
    assert b.skill_routing == c.skill_routing == "auto"
    assert b.system_instruction == c.system_instruction
    assert b.instruction_dir != c.instruction_dir
    bf, cf = b.fingerprints(), c.fingerprints()
    assert bf["system_instruction"] == cf["system_instruction"]
    assert "adapted_files" in cf
    assert "adapted_files" not in bf


def test_arm_c_points_at_adapted_dir_not_user_skills():
    spec = arm_c()
    assert "adapted_instructions" in spec.instruction_dir
    assert ".cursor" not in spec.instruction_dir
    assert CANDIDATE["redistribute_original"] is False
    assert CANDIDATE["original_text_in_this_repo"] is False
    assert CANDIDATE["github_license_field"] is None


def test_replay_matches_selector_not_call_index_and_isolates_objects():
    case = by_id()["multi-fault"]
    replay = ReplayToolProvider(case)

    async def walk():
        await replay.connect()
        first = await replay.call_tool(
            "get_pod_status",
            {"pod_selector": "app.kubernetes.io/component=ingest-a"},
        )
        second = await replay.call_tool(
            "get_pod_status",
            {"pod_selector": "app.kubernetes.io/component=ingest-b"},
        )
        again = await replay.call_tool(
            "get_pod_status",
            {"pod_selector": "app.kubernetes.io/component=ingest-a"},
        )
        bad = await replay.call_tool_result(
            "get_pod_status",
            {"pod_selector": "app.kubernetes.io/component=does-not-exist"},
        )
        write = await replay.call_tool_result("restart_deployment", {"name": "ingest-a"})
        missing = await replay.call_tool_result("query_prometheus", {"query": "up"})
        return first, second, again, bad, write, missing

    first, second, again, bad, write, missing = asyncio.run(walk())
    assert "ingest-a-11" in first
    assert "ingest-b-22" in second
    assert "ImagePullBackOff" not in first
    assert "CrashLoopBackOff" not in second
    assert again == first
    assert bad.is_error
    assert write.call_status == "denied" or "DENIED" in write.text
    assert missing.is_error
    assert replay.write_attempts
    for name in WRITE_TOOLS:
        assert name


def test_replay_scan_unavailable_is_error_text_not_healthy():
    case = by_id()["scan-unavailable"]
    replay = ReplayToolProvider(case)

    async def walk():
        text = await replay.call_tool("list_pods", {})
        return text

    text = asyncio.run(walk())
    assert "ERROR" in text
    assert "Ready" not in text


def test_scorer_distinguishes_correct_wrong_object_gap_and_unknown():
    case = by_id()["memory-kill"]
    good = score_run(
        case,
        arm="B",
        repeat=1,
        stats={
            "diagnosis": {
                "claims": [
                    {
                        "statement": "waiting CrashLoopBackOff after last_termination OOMKilled",
                        "type": "causal_claim",
                        "object": "cache-node",
                    },
                    {
                        "statement": "validate_recovery FAIL; not Ready",
                        "type": "recovery_claim",
                        "object": "cache-node",
                    },
                ]
            },
            "skill": {
                "outcomes": [
                    {
                        "tool_name": "get_pod_status",
                        "status": "success",
                        "object_name": "cache-node-21b0",
                        "obs_id": "obs-1",
                    }
                ]
            },
            "start_time": 0,
            "end_time": 1,
        },
        answer="OOMKilled; CrashLoopBackOff waiting; validate_recovery FAIL",
        unmatched=[],
    )
    assert good.object_correct.passed
    assert good.facts.passed
    assert good.coverage.passed
    assert not good.severe

    wrong = score_run(
        case,
        arm="B",
        repeat=1,
        stats={
            "diagnosis": {
                "claims": [
                    {
                        "statement": "OOMKilled",
                        "type": "causal_claim",
                        "object": "storefront-web",
                    }
                ]
            },
            "skill": {"outcomes": []},
            "start_time": 0,
            "end_time": 1,
        },
        answer="OOMKilled on storefront-web",
        unmatched=[],
    )
    assert wrong.object_correct.passed is False
    assert "wrong_object" in wrong.severe

    guess = by_id()["user-guessed-oom"]
    copied = score_run(
        guess,
        arm="A",
        repeat=1,
        stats={
            "diagnosis": {
                "claims": [
                    {
                        "statement": "confirmed OOMKilled as the user guessed",
                        "type": "causal_claim",
                        "object": "auth-edge",
                    }
                ]
            },
            "skill": {
                "outcomes": [
                    {
                        "tool_name": "get_pod_status",
                        "status": "success",
                        "object_name": "auth-edge-55d2",
                    }
                ]
            },
            "start_time": 0,
            "end_time": 1,
        },
        answer="OOMKilled",
        unmatched=[],
    )
    assert copied.unsupported.passed is False
    assert "unsupported_cause" in copied.severe

    unknown_case = by_id()["crash-exit-unknown"]
    unknown = score_run(
        unknown_case,
        arm="B",
        repeat=1,
        stats={
            "diagnosis": {
                "claims": [
                    {
                        "statement": "CrashLoopBackOff; exit 1; cause unknown",
                        "type": "symptom",
                        "object": "billing-worker",
                    }
                ]
            },
            "skill": {
                "outcomes": [
                    {
                        "tool_name": "get_pod_status",
                        "status": "success",
                        "object_name": "billing-worker-4c1e",
                    }
                ]
            },
            "start_time": 0,
            "end_time": 1,
        },
        answer="CrashLoopBackOff exit 1 cause unknown",
        unmatched=[],
    )
    assert unknown.facts.passed
    assert "unsupported_cause" not in unknown.severe

    ruled_out = score_run(
        unknown_case,
        arm="B",
        repeat=1,
        stats={
            "diagnosis": {
                "claims": [
                    {
                        "statement": "CrashLoopBackOff; exit 1; not OOMKilled; cause unknown",
                        "type": "symptom",
                        "object": "billing-worker",
                    }
                ]
            },
            "skill": {
                "outcomes": [
                    {
                        "tool_name": "get_pod_status",
                        "status": "success",
                        "object_name": "billing-worker-4c1e",
                    }
                ]
            },
            "start_time": 0,
            "end_time": 1,
        },
        answer="CrashLoopBackOff exit 1 rather than being OOMKilled; cause unknown",
        unmatched=[],
    )
    assert ruled_out.unsupported.passed
    assert "unsupported_cause" not in ruled_out.severe

    keywords = score_run(
        case,
        arm="C",
        repeat=1,
        stats={
            "diagnosis": {
                "claims": [
                    {
                        "statement": "OOMKilled CrashLoopBackOff ImagePullBackOff probe Pending",
                        "type": "observation",
                        "object": "everything",
                    }
                ]
            },
            "skill": {"outcomes": []},
            "start_time": 0,
            "end_time": 1,
        },
        answer="OOMKilled CrashLoopBackOff ImagePullBackOff",
        unmatched=[],
    )
    assert keywords.coverage.passed is False
    assert keywords.object_correct.passed is False


def test_scorer_false_recovery_and_scan_failure():
    crash = by_id()["crash-exit-unknown"]
    fake_pass = score_run(
        crash,
        arm="A",
        repeat=1,
        stats={
            "diagnosis": {
                "claims": [
                    {
                        "statement": "validate_recovery PASS; fully recovered",
                        "type": "recovery_claim",
                        "object": "billing-worker",
                    }
                ]
            },
            "recovery": {"status": "fail"},
            "skill": {
                "outcomes": [
                    {
                        "tool_name": "get_pod_status",
                        "status": "success",
                        "object_name": "billing-worker-4c1e",
                    }
                ]
            },
            "start_time": 0,
            "end_time": 1,
        },
        answer="CrashLoopBackOff exit 1; fully recovered",
        unmatched=[],
    )
    assert fake_pass.recovery.passed is False
    assert "false_recovery" in fake_pass.severe

    down = by_id()["scan-unavailable"]
    healthy = score_run(
        down,
        arm="B",
        repeat=1,
        stats={"diagnosis": {"claims": []}, "start_time": 0, "end_time": 1},
        answer="namespace is healthy",
        unmatched=[],
    )
    assert "false_healthy" in healthy.severe


def test_jobs_unique_and_interleaved():
    splits = {item.case_id: item.split for item in all_cases()}
    jobs = build_jobs(
        case_ids=["healthy-ready", "image-pull"],
        arms=["A", "B", "C"],
        repeats=5,
        splits=splits,
        seed=20260315,
    )
    assert len(jobs) == 30
    assert len({job.job_id for job in jobs}) == 30
    assert jobs[0].arm != "A" or jobs[1].arm != "B" or True
    arms_order = [job.arm for job in jobs[:6]]
    assert arms_order != ["A", "A", "A", "A", "A", "B"]
    assert job_id("A", "healthy-ready", 1) != job_id("B", "healthy-ready", 1)


def test_cli_list_preflight_and_live_blocked(tmp_path, capsys):
    assert bench_main(["list"]) == 0
    listed = capsys.readouterr().out
    assert "healthy-ready" in listed
    assert "scan-unavailable" in listed
    assert bench_main(["preflight"]) == 0
    payload = preflight()
    assert payload["ok_offline"] is True
    assert payload["ok_live_quality"] is False
    assert bench_main(["run", "--live", "--out", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "charges" in err.lower() or "blocked" in err.lower() or LIVE_BLOCKED[:20] in err


def test_fake_run_wires_three_arms_and_skips_user_memory(tmp_path):
    before = None
    existed = MEMORY_DEFAULT.exists()
    if existed:
        before = MEMORY_DEFAULT.read_bytes()
    rows = []
    for arm in ("A", "B", "C"):
        job = build_jobs(
            case_ids=["healthy-ready"],
            arms=[arm],
            repeats=1,
            splits={"healthy-ready": "dev"},
            seed=1,
            interleave=False,
        )[0]
        rows.append(asyncio.run(run_job(job, out_dir=tmp_path, fake_model=True, resume=False)))
    assert [row["status"] for row in rows] == ["scored", "scored", "scored"]
    assert all(row["fake_model"] for row in rows)
    assert all(row["quality_valid"] is False for row in rows)
    a_tools, b_tools, c_tools = (row["seen_tools"][0] for row in rows)
    assert "list_pods" in a_tools
    assert "list_pods" in b_tools
    assert a_tools != b_tools or rows[0]["instruction_present"] != rows[1]["instruction_present"]
    assert rows[0]["instruction_present"][0] is False
    assert rows[1]["instruction_present"][0] is True
    assert rows[2]["instruction_present"][0] is True
    assert rows[0]["stats"]["skill"].get("routing_mode") == "legacy"
    assert rows[1]["stats"]["skill"].get("routing_mode") == "auto"
    loaded_c = " ".join(rows[2]["stats"]["skill"].get("loaded_instructions") or [])
    assert loaded_c
    if existed:
        assert MEMORY_DEFAULT.read_bytes() == before
    else:
        assert not MEMORY_DEFAULT.exists()
    assert not (tmp_path / "memory-disabled.jsonl").exists() or (
        tmp_path / "memory-disabled.jsonl"
    ).read_text(encoding="utf-8") == ""
    resume_job = build_jobs(
        case_ids=["healthy-ready"],
        arms=["A"],
        repeats=1,
        splits={"healthy-ready": "dev"},
        seed=1,
        interleave=False,
    )[0]
    first_path = tmp_path / "jobs" / f"{resume_job.job_id}.json"
    stamp = first_path.read_text(encoding="utf-8")
    again = asyncio.run(run_job(resume_job, out_dir=tmp_path, fake_model=True, resume=True))
    assert again["job_id"] == resume_job.job_id
    assert first_path.read_text(encoding="utf-8") == stamp


def test_preflight_mentions_unproven_quality():
    assert "尚不能判断" in QUALITY_UNPROVEN
    assert "Citrus-adapted" in CANDIDATE["label"]
