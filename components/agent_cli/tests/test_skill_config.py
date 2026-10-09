"""Config priority, CLI flags, ablation conflicts."""
import subprocess
import sys

import pytest

from agent_cli.skills.errors import SkillError
from agent_cli.skills.settings import resolve_skill_settings


def test_cli_beats_env_beats_default():
    env = {"CITRUS_SKILL_ROUTING": "auto"}
    settings = resolve_skill_settings(env=env)
    assert settings.routing == "auto"
    settings = resolve_skill_settings(cli_routing="legacy", env=env)
    assert settings.routing == "legacy"
    settings = resolve_skill_settings(env={})
    assert settings.routing == "legacy"


def test_skill_id_implies_auto():
    settings = resolve_skill_settings(cli_skill="oom", env={})
    assert settings.routing == "auto"
    assert settings.skill_id == "oom"


def test_skill_id_with_explicit_legacy_errors():
    with pytest.raises(SkillError, match="legacy"):
        resolve_skill_settings(cli_routing="legacy", cli_skill="oom", env={})


def test_ablation_legacy_rejects_auto():
    with pytest.raises(SkillError, match="ABLATION"):
        resolve_skill_settings(cli_routing="auto", env={"CITRUS_ABLATION": "legacy"})


def test_unknown_mode_rejected():
    with pytest.raises(SkillError, match="legacy or auto"):
        resolve_skill_settings(cli_routing="magic", env={})


def test_agent_cli_help_parses_skill_flags():
    result = subprocess.run(
        [sys.executable, "-m", "agent_cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--skill-routing" in result.stdout
    assert "--skill" in result.stdout
    assert "--engine" in result.stdout
