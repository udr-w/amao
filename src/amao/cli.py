from __future__ import annotations

import argparse
import logging
import sys

from amao.exceptions import ConfigError
from amao.orchestrator import Orchestrator


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        orchestrator = Orchestrator(project_dir=args.dir, project_goal=args.goal)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    orchestrator.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amao", description="Autonomous agent orchestrator")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run (or resume) the milestone pipeline")
    run_parser.add_argument("--dir", required=True, help="Target project directory")
    run_parser.add_argument("--goal", required=True, help="High-level project goal")
    run_parser.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
