"""TesterAgent: detects which strategies apply to a project and runs each
one in a disposable Docker sandbox, aggregating the results into a single
TestOutcome. Mirrors ReviewerAgent's contract of returning a result rather
than raising for an ordinary "tests failed" outcome -- only a sandbox-level
infra problem (see DockerSandbox) raises.
"""

from __future__ import annotations

import logging
import os

from amao.models import Milestone
from amao.testing.bdd import BehaveBDDStrategy, GherkinGenerator
from amao.testing.models import TestOutcome
from amao.testing.sandbox import DockerSandbox
from amao.testing.strategies import (
    DEFAULT_STRATEGIES,
    TestStrategy,
    _detect_python_web_kind,
    detect_strategies,
)

logger = logging.getLogger(__name__)


class TesterAgent:
    def __init__(
        self,
        sandbox: DockerSandbox,
        max_output_chars: int,
        strategies: tuple[TestStrategy, ...] = DEFAULT_STRATEGIES,
        gherkin_generator: GherkinGenerator | None = None,
        bdd_strategy: BehaveBDDStrategy | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.max_output_chars = max_output_chars
        self.gherkin_generator = gherkin_generator
        self.bdd_strategy = bdd_strategy
        if bdd_strategy is not None and bdd_strategy not in strategies:
            strategies = (*strategies, bdd_strategy)
        self.strategies = strategies

    def test_project(self, project_dir: str, milestone: Milestone | None = None) -> TestOutcome:
        self._maybe_generate_bdd_scenario(project_dir, milestone)

        applicable = detect_strategies(project_dir, self.strategies)
        if not applicable:
            return TestOutcome(
                ran=False,
                passed=True,
                summary="No applicable test strategy was detected for this project.",
                output="",
            )

        all_passed = True
        names: list[str] = []
        summaries: list[str] = []
        outputs: list[str] = []
        screenshots: list[str] = []
        for strategy in applicable:
            strategy.ensure_ready()
            exit_code, output = self.sandbox.run(
                project_dir, strategy.docker_image, strategy.shell_command(project_dir)
            )
            passed = exit_code == 0
            all_passed = all_passed and passed
            names.append(strategy.name)
            status = "PASSED" if passed else f"FAILED (exit {exit_code})"
            summaries.append(f"{strategy.name}: {status}")
            outputs.append(f"--- {strategy.name} ---\n{output}")

            if strategy.screenshot_relpath:
                candidate = os.path.join(project_dir, strategy.screenshot_relpath)
                if os.path.exists(candidate):
                    screenshots.append(candidate)

        combined_output = "\n\n".join(outputs)[: self.max_output_chars]
        return TestOutcome(
            ran=True,
            passed=all_passed,
            summary="; ".join(summaries),
            output=combined_output,
            strategy_names=tuple(names),
            screenshots=tuple(screenshots),
        )

    def _maybe_generate_bdd_scenario(self, project_dir: str, milestone: Milestone | None) -> None:
        if self.bdd_strategy is None:
            return
        # Only bother calling the LLM if there's actually a web app to test --
        # generating a scenario nobody can run would just be wasted cost.
        if (
            self.gherkin_generator is None
            or milestone is None
            or _detect_python_web_kind(project_dir) is None
        ):
            self.bdd_strategy.set_scenario(None)
            return
        try:
            scenario = self.gherkin_generator.generate(milestone)
        except Exception as e:
            # BDD is an enhancement layered on top of Tier-1/web-UI testing,
            # not a hard requirement -- a generation failure (rate limit,
            # unparseable response) should skip BDD this round, not abort
            # the rest of the test run.
            logger.warning("Skipping BDD scenario for this milestone: %s", e)
            scenario = None
        self.bdd_strategy.set_scenario(scenario)
