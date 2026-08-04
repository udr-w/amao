"""Real, non-mocked verification that PytestStrategy actually works against a
live Docker daemon -- excluded from the default `pytest` run (see the
`real_docker` marker in pyproject.toml) since it needs Docker installed and
reachable, and pulls/builds nothing lightweight-mocked tests can substitute
for. Run explicitly: `pytest -m real_docker tests/test_testing_real_docker.py`.

This exists because of a hard lesson from this project's own development:
mocked unit tests can prove amao constructs the right shell command, but
they cannot prove that command actually works inside the target Docker
image as the sandboxed non-root user -- see TESTER_AGENT_PLAN.md for the
real bugs (root-owned files, a $PATH-less pip --user install, a ~14-minute
Chromium install cost) that only a live run ever surfaced.
"""

import os

import pytest

from amao.testing.agent import TesterAgent
from amao.testing.sandbox import DockerSandbox
from amao.testing.strategies import PytestStrategy

pytestmark = pytest.mark.real_docker


def test_pytest_strategy_passes_against_a_real_passing_project(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    tester = TesterAgent(
        sandbox=DockerSandbox(timeout=180),
        max_output_chars=5000,
        strategies=(PytestStrategy(),),
    )

    outcome = tester.test_project(str(tmp_path))

    assert outcome.ran is True
    assert outcome.passed is True, outcome.output
    assert "pytest" in outcome.strategy_names


def test_pytest_strategy_fails_against_a_real_failing_project(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_broken():\n    assert 1 + 1 == 3\n")
    tester = TesterAgent(
        sandbox=DockerSandbox(timeout=180),
        max_output_chars=5000,
        strategies=(PytestStrategy(),),
    )

    outcome = tester.test_project(str(tmp_path))

    assert outcome.ran is True
    assert outcome.passed is False
    assert "AssertionError" in outcome.output


def test_container_does_not_leave_root_owned_files(tmp_path):
    # Regression check for the exact bug this project found and fixed: an
    # official image running as root by default leaving undeletable files
    # in the mounted project dir.
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    tester = TesterAgent(
        sandbox=DockerSandbox(timeout=180),
        max_output_chars=5000,
        strategies=(PytestStrategy(),),
    )

    tester.test_project(str(tmp_path))

    for root, _dirs, files in os.walk(tmp_path):
        for name in files:
            path = os.path.join(root, name)
            assert os.stat(path).st_uid == os.getuid(), f"{path} is not owned by the current user"
