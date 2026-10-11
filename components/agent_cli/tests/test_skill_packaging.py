"""Packaging config keeps Skill resources and excludes tests."""
import tomllib
from pathlib import Path

COMPONENTS = Path(__file__).resolve().parents[2]


def test_pyproject_packages_skills_not_tests():
    data = tomllib.loads((COMPONENTS / "pyproject.toml").read_text(encoding="utf-8"))
    find = data["tool"]["setuptools"]["packages"]["find"]
    assert "agent_cli*" in find["include"]
    assert "agent_cli.tests" in find["exclude"]
    assert "agent_cli.tests.*" in find["exclude"]
    package_data = data["tool"]["setuptools"]["package-data"]
    assert "*.toml" in package_data["*"]
    assert "*.md" in package_data["*"]
    builtin = package_data["agent_cli.skills.builtin"]
    assert "*/*.toml" in builtin
    assert "fragments/*.toml" in builtin


def test_manifest_includes_builtin_and_prunes_tests():
    text = (COMPONENTS / "MANIFEST.in").read_text(encoding="utf-8")
    assert "agent_cli/skills/builtin" in text
    assert "prune agent_cli/tests" in text
