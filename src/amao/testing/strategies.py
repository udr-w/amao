"""Test strategies: each declares whether it applies to a project and how to
run it. The Tester only ever runs strategies whose detect() returns True --
a Go project's container is never given Python or Node, and vice versa. This
is deliberate: irrelevant tooling must never run just because it's supported
somewhere in this module.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Sequence

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


class TestStrategy(ABC):
    name: str
    docker_image: str

    @abstractmethod
    def detect(self, project_dir: str) -> bool:
        """Return True if this strategy applies to the project at project_dir."""

    @abstractmethod
    def shell_command(self) -> str:
        """Setup + run, as a single shell command executed via `sh -c`."""


class PytestStrategy(TestStrategy):
    name = "pytest"
    docker_image = "python:3.12-slim"

    def detect(self, project_dir: str) -> bool:
        markers = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")
        if any(os.path.exists(os.path.join(project_dir, m)) for m in markers):
            return True
        return self._has_test_files(project_dir)

    @staticmethod
    def _has_test_files(project_dir: str) -> bool:
        for _root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if not name.endswith(".py"):
                    continue
                if name.startswith("test_") or name.endswith("_test.py"):
                    return True
        return False

    def shell_command(self) -> str:
        # Running as a non-root host UID (see sandbox.py) means pip installs
        # land in --user mode under $HOME/.local, not the system site-packages
        # -- and that bin dir isn't on PATH by default.
        return (
            'export PATH="$HOME/.local/bin:$PATH"; '
            "pip install --quiet --no-input pytest; "
            "pip install --quiet --no-input -e . 2>/dev/null "
            "|| pip install --quiet --no-input -r requirements.txt 2>/dev/null; "
            "pytest -q"
        )


class NpmTestStrategy(TestStrategy):
    name = "npm-test"
    docker_image = "node:20-slim"

    def detect(self, project_dir: str) -> bool:
        package_json = os.path.join(project_dir, "package.json")
        if not os.path.exists(package_json):
            return False
        try:
            with open(package_json, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        return bool(data.get("scripts", {}).get("test"))

    def shell_command(self) -> str:
        return "npm install --no-audit --no-fund --silent; npm test --silent"


class GoTestStrategy(TestStrategy):
    name = "go-test"
    docker_image = "golang:1.24-bookworm"

    def detect(self, project_dir: str) -> bool:
        return os.path.exists(os.path.join(project_dir, "go.mod"))

    def shell_command(self) -> str:
        return "go test ./..."


DEFAULT_STRATEGIES: tuple[TestStrategy, ...] = (
    PytestStrategy(),
    NpmTestStrategy(),
    GoTestStrategy(),
)


def detect_strategies(
    project_dir: str, strategies: Sequence[TestStrategy] = DEFAULT_STRATEGIES
) -> list[TestStrategy]:
    return [s for s in strategies if s.detect(project_dir)]
