from unittest.mock import MagicMock

from amao.testing.agent import TesterAgent
from amao.testing.strategies import TestStrategy


class _FakeStrategy(TestStrategy):
    def __init__(self, name, docker_image, detects=True):
        self.name = name
        self.docker_image = docker_image
        self._detects = detects

    def detect(self, project_dir):
        return self._detects

    def shell_command(self):
        return f"run-{self.name}"


def _sandbox_stub(results_by_command):
    sandbox = MagicMock()
    sandbox.run.side_effect = lambda project_dir, image, shell_command: results_by_command[
        shell_command
    ]
    return sandbox


def test_no_applicable_strategy_reports_ran_false_and_passed_true(tmp_path):
    strategy = _FakeStrategy("noop", "img", detects=False)
    tester = TesterAgent(sandbox=MagicMock(), max_output_chars=1000, strategies=(strategy,))

    outcome = tester.test_project(str(tmp_path))

    assert outcome.ran is False
    assert outcome.passed is True
    assert outcome.strategy_names == ()


def test_single_passing_strategy(tmp_path):
    strategy = _FakeStrategy("pytest", "python:3.12-slim")
    sandbox = _sandbox_stub({"run-pytest": (0, "3 passed\n")})
    tester = TesterAgent(sandbox=sandbox, max_output_chars=1000, strategies=(strategy,))

    outcome = tester.test_project(str(tmp_path))

    assert outcome.ran is True
    assert outcome.passed is True
    assert outcome.strategy_names == ("pytest",)
    assert "3 passed" in outcome.output
    assert "PASSED" in outcome.summary


def test_single_failing_strategy(tmp_path):
    strategy = _FakeStrategy("pytest", "python:3.12-slim")
    sandbox = _sandbox_stub({"run-pytest": (1, "AssertionError\n")})
    tester = TesterAgent(sandbox=sandbox, max_output_chars=1000, strategies=(strategy,))

    outcome = tester.test_project(str(tmp_path))

    assert outcome.ran is True
    assert outcome.passed is False
    assert "FAILED" in outcome.summary
    assert "AssertionError" in outcome.output


def test_mixed_pass_and_fail_across_strategies_is_overall_failed(tmp_path):
    good = _FakeStrategy("go-test", "golang:1.24-bookworm")
    bad = _FakeStrategy("npm-test", "node:20-slim")
    sandbox = _sandbox_stub({"run-go-test": (0, "ok\n"), "run-npm-test": (1, "fail\n")})
    tester = TesterAgent(sandbox=sandbox, max_output_chars=1000, strategies=(good, bad))

    outcome = tester.test_project(str(tmp_path))

    assert outcome.passed is False
    assert set(outcome.strategy_names) == {"go-test", "npm-test"}


def test_output_is_capped_at_max_output_chars(tmp_path):
    strategy = _FakeStrategy("pytest", "python:3.12-slim")
    sandbox = _sandbox_stub({"run-pytest": (0, "x" * 1000)})
    tester = TesterAgent(sandbox=sandbox, max_output_chars=50, strategies=(strategy,))

    outcome = tester.test_project(str(tmp_path))

    assert len(outcome.output) <= 50
