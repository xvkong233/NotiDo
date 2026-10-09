"""Plugin installation must allow AstrBot's protected dependency versions."""

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent


def plugin_requirements():
    return [
        Requirement(line)
        for line in (ROOT / "requirements.txt").read_text("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_plugin_requirements_match_direct_project_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    # Exporting uv.lock here would pin shared and transitive dependencies, forcing
    # pip to replace packages protected by the host's core constraints.
    assert sorted(map(str, plugin_requirements())) == sorted(
        str(Requirement(value)) for value in project["dependencies"]
    )


@pytest.mark.parametrize("version", ["3.32.7", "4.0.4"])
def test_plugin_allows_host_filelock(version):
    requirement = next(item for item in plugin_requirements() if item.name == "filelock")
    assert version in requirement.specifier
