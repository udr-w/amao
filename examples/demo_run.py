"""Self-contained demo: builds a CLI task manager app end-to-end.

Requires the package to be installed (`pip install -e ".[dev]"`) and
OPENAI_API_KEY / ANTHROPIC_API_KEY set in the environment.
"""

from __future__ import annotations

import logging
import sys

from amao.exceptions import ConfigError
from amao.orchestrator import Orchestrator

DEMO_PROJECT_DIR = "./demo_task_manager_app"
DEMO_PROJECT_GOAL = (
    "Build a simple Python CLI Task Manager app. "
    "It should support adding tasks, listing tasks in a formatted table, "
    "and marking tasks as completed. Store tasks in a local tasks.json file."
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

    print("=" * 50)
    print("    AGENT-TO-AGENT AUTOMATED WORKFLOW DEMO")
    print("=" * 50)

    try:
        orchestrator = Orchestrator(project_dir=DEMO_PROJECT_DIR, project_goal=DEMO_PROJECT_GOAL)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    orchestrator.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
