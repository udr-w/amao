from unittest.mock import MagicMock

from amao.models import Milestone, MilestoneStatus
from amao.testing.agent import TesterAgent
from amao.testing.bdd import BehaveBDDStrategy
from amao.testing.strategies import TestStrategy


class _FakeStrategy(TestStrategy):
    def __init__(self, name, docker_image, detects=True):
        self.name = name
        self.docker_image = docker_image
        self._detects = detects
        self.ensure_ready_calls = 0

    def detect(self, project_dir):
        return self._detects

    def shell_command(self, project_dir):
        return f"run-{self.name}"

    def ensure_ready(self):
        self.ensure_ready_calls += 1


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


def test_screenshot_is_collected_when_the_strategy_produces_one(tmp_path):
    (tmp_path / ".amao_screenshot.png").write_bytes(b"fake-png")
    strategy = _FakeStrategy("web-ui-python", "python:3.12-slim")
    strategy.screenshot_relpath = ".amao_screenshot.png"
    sandbox = _sandbox_stub({"run-web-ui-python": (0, "UI_CHECK_OK")})
    tester = TesterAgent(sandbox=sandbox, max_output_chars=1000, strategies=(strategy,))

    outcome = tester.test_project(str(tmp_path))

    assert outcome.screenshots == (str(tmp_path / ".amao_screenshot.png"),)


def test_no_screenshot_collected_when_strategy_did_not_produce_one(tmp_path):
    strategy = _FakeStrategy("web-ui-python", "python:3.12-slim")
    strategy.screenshot_relpath = ".amao_screenshot.png"
    sandbox = _sandbox_stub({"run-web-ui-python": (1, "ERROR: app did not start")})
    tester = TesterAgent(sandbox=sandbox, max_output_chars=1000, strategies=(strategy,))

    outcome = tester.test_project(str(tmp_path))

    assert outcome.screenshots == ()


def test_ensure_ready_is_called_once_per_applicable_strategy(tmp_path):
    ready = _FakeStrategy("pytest", "python:3.12-slim")
    not_applicable = _FakeStrategy("web-ui-python", "amao-webui-tester:local", detects=False)
    sandbox = _sandbox_stub({"run-pytest": (0, "ok")})
    tester = TesterAgent(sandbox=sandbox, max_output_chars=1000, strategies=(ready, not_applicable))

    tester.test_project(str(tmp_path))

    assert ready.ensure_ready_calls == 1
    assert not_applicable.ensure_ready_calls == 0  # never run, never prepared


class _FakeGherkinGenerator:
    def __init__(self, scenario="Feature: x\nScenario: y\nGiven I visit the homepage\n"):
        self.scenario = scenario
        self.calls = []

    def generate(self, milestone):
        self.calls.append(milestone)
        return self.scenario


def _django_milestone():
    return Milestone(
        id=1,
        title="Add login",
        description="A login button appears",
        status=MilestoneStatus.PENDING,
        attempts=0,
        last_error=None,
    )


def test_bdd_scenario_generated_and_run_when_web_app_and_milestone_present(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")
    generator = _FakeGherkinGenerator()
    bdd_strategy = BehaveBDDStrategy()
    # shell_command's exact text depends on the scenario, so set it the same
    # way test_project() will before computing the expected command here.
    bdd_strategy.set_scenario(generator.scenario)
    sandbox = _sandbox_stub({bdd_strategy.shell_command(str(tmp_path)): (0, "1 scenario passed")})
    tester = TesterAgent(
        sandbox=sandbox,
        max_output_chars=1000,
        strategies=(),
        gherkin_generator=generator,
        bdd_strategy=bdd_strategy,
    )

    outcome = tester.test_project(str(tmp_path), milestone=_django_milestone())

    assert generator.calls == [_django_milestone()]
    assert outcome.ran is True
    assert outcome.passed is True
    assert "bdd-behave" in outcome.strategy_names


def test_bdd_scenario_not_generated_without_a_web_app(tmp_path):
    generator = _FakeGherkinGenerator()
    bdd_strategy = BehaveBDDStrategy()
    tester = TesterAgent(
        sandbox=MagicMock(),
        max_output_chars=1000,
        strategies=(),
        gherkin_generator=generator,
        bdd_strategy=bdd_strategy,
    )

    outcome = tester.test_project(str(tmp_path), milestone=_django_milestone())

    assert generator.calls == []
    assert outcome.ran is False


def test_bdd_scenario_not_generated_without_a_milestone(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")
    generator = _FakeGherkinGenerator()
    bdd_strategy = BehaveBDDStrategy()
    tester = TesterAgent(
        sandbox=MagicMock(),
        max_output_chars=1000,
        strategies=(),
        gherkin_generator=generator,
        bdd_strategy=bdd_strategy,
    )

    outcome = tester.test_project(str(tmp_path))  # no milestone

    assert generator.calls == []
    assert outcome.ran is False


def test_bdd_generation_failure_is_non_fatal(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")

    class _RaisingGenerator:
        def generate(self, milestone):
            raise RuntimeError("rate limited")

    bdd_strategy = BehaveBDDStrategy()
    tester = TesterAgent(
        sandbox=MagicMock(),
        max_output_chars=1000,
        strategies=(),
        gherkin_generator=_RaisingGenerator(),
        bdd_strategy=bdd_strategy,
    )

    outcome = tester.test_project(str(tmp_path), milestone=_django_milestone())

    assert outcome.ran is False  # no other strategies applicable, BDD skipped cleanly
    assert bdd_strategy.detect(str(tmp_path)) is False
