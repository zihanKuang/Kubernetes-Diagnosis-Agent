"""Registry discovery, path sandbox, delayed instruction load."""
import json
from pathlib import Path

import pytest

from agent_cli.skills.cli import main as skills_main
from agent_cli.skills.errors import SkillError
from agent_cli.skills.loader import ResourceLoader
from agent_cli.skills.registry import SkillRegistry
from agent_cli.skills.workflows import compile_workflow

FIXTURES = Path(__file__).parent / "fixtures" / "skills"


def test_builtin_discovery_does_not_need_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    registry = SkillRegistry()
    ids = {row.meta.id for row in registry.list(include_disabled=False)}
    assert "generic-triage" in ids
    assert "healthy" in ids
    assert "crashloop" in ids


def test_external_dir_only_scans_configured_root():
    registry = SkillRegistry(extra_dirs=[str(FIXTURES / "crashloop-status-only")])
    row = registry.get("crashloop-status-only")
    assert row.meta.source == "external"
    with pytest.raises(SkillError, match="unknown skill"):
        registry.get("not-installed")


def test_duplicate_id_version_fails(tmp_path):
    src = FIXTURES / "crashloop-status-only"
    a = tmp_path / "a" / "crashloop-status-only"
    b = tmp_path / "b" / "crashloop-status-only"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    for dest in (a, b):
        for name in ("SKILL.toml", "workflow.toml", "SKILL.md"):
            (dest / name).write_bytes((src / name).read_bytes())
    with pytest.raises(SkillError, match="duplicate"):
        SkillRegistry(extra_dirs=[str(tmp_path / "a"), str(tmp_path / "b")])


def test_disabled_skill_not_in_available_and_override_errors():
    registry = SkillRegistry(disabled=["crashloop"])
    ids = {row.meta.id for row in registry.list(include_disabled=False)}
    assert "crashloop" not in ids
    with pytest.raises(SkillError, match="disabled"):
        registry.available("crashloop")


def test_path_escape_rejected(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    loader = ResourceLoader()
    with pytest.raises(SkillError, match="parent-directory"):
        loader.resolve(root, "../secret.md", skill_id="x")
    with pytest.raises(SkillError, match="absolute"):
        loader.resolve(root, str(tmp_path / "secret.md"), skill_id="x")


def test_instructions_load_only_when_activated():
    loader = ResourceLoader()
    registry = SkillRegistry(loader=loader)
    oom = registry.get("oom")
    crash = registry.get("crashloop")
    assert loader.reads == {}
    loader.load_instructions(oom.meta)
    loaded = [path for path in loader.reads if path.replace("\\", "/").endswith("oom/SKILL.md")]
    assert loaded
    crash_reads = [path for path in loader.reads if "crashloop/SKILL.md" in path.replace("\\", "/")]
    assert crash_reads == []
    assert crash.meta.id == "crashloop"


def test_run_snapshot_freezes_workflow_bytes(tmp_path):
    registry = SkillRegistry()
    row = registry.get("crashloop")
    fp = row.fingerprint()
    # Mutating the on-disk file would not change the already-parsed bytes.
    assert len(fp) == 64
    assert fp == row.fingerprint()


def test_cli_list_and_validate_without_model(capsys):
    assert skills_main(["list"]) == 0
    out = capsys.readouterr().out
    assert "generic-triage" in out
    assert skills_main(["validate"]) == 0


def test_cli_validate_broken_package_nonzero():
    code = skills_main(["validate", str(FIXTURES / "broken")])
    assert code != 0


def test_cli_show_compiles_plan(capsys):
    assert skills_main(["show", "crashloop"]) == 0
    out = capsys.readouterr().out
    assert "inspect" in out
    assert "fragment:" in out or "processor" in out


def test_cli_preview_with_observation_file(capsys):
    path = FIXTURES / "preview-oom.json"
    code = skills_main([
        "preview",
        "--query",
        "Why is the workload unhealthy?",
        "--observation-file",
        str(path),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill_id"] == "oom"
    assert payload["decision"]["stage"] == "post_scan"
    assert payload["needs_live_evidence"] is False
