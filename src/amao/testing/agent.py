"""TesterAgent: detects which strategies apply to a project and runs each
one in a disposable Docker sandbox, aggregating the results into a single
TestOutcome. Mirrors ReviewerAgent's contract of returning a result rather
than raising for an ordinary "tests failed" outcome -- only a sandbox-level
infra problem (see DockerSandbox) raises.
"""

from __future__ import annotations

import os

from amao.testing.models import TestOutcome
from amao.testing.sandbox import DockerSandbox
from amao.testing.strategies import DEFAULT_STRATEGIES, TestStrategy, detect_strategies


class TesterAgent:
    def __init__(
        self,
        sandbox: DockerSandbox,
        max_output_chars: int,
        strategies: tuple[TestStrategy, ...] = DEFAULT_STRATEGIES,
    ) -> None:
        self.sandbox = sandbox
        self.max_output_chars = max_output_chars
        self.strategies = strategies

    def test_project(self, project_dir: str) -> TestOutcome:
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
