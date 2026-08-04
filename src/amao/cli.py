from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from amao.config import config
from amao.exceptions import ConfigError
from amao.orchestrator import Orchestrator
from amao.state_manager import StateManager

_TRUNCATE_CHARS = 500


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


def _db_path(directory: str) -> str:
    return os.path.join(os.path.abspath(directory), config.DB_FILENAME)


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def _truncate(text: str, limit: int = _TRUNCATE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... (truncated, showing {limit} of {len(text)} chars)"


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        orchestrator = Orchestrator(project_dir=args.dir, project_goal=args.goal)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    orchestrator.run()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    db_path = _db_path(args.dir)
    if not os.path.exists(db_path):
        print(
            f"No orchestrator state found in {args.dir} -- has `amao run` been started here?",
            file=sys.stderr,
        )
        return 1

    state = StateManager(db_path)
    summary = state.get_progress_summary()

    print(f"Milestones: {summary.total} total")
    print(
        f"  pending: {summary.pending}  in_progress: {summary.in_progress}  "
        f"completed: {summary.completed}  halted: {summary.halted}"
    )

    if summary.current_milestone_title is not None:
        print(
            f"Current milestone: {summary.current_milestone_title} "
            f"(attempt {summary.current_milestone_attempts})"
        )
    else:
        print("Current milestone: none in progress")

    if summary.average_completed_seconds is not None:
        avg = _format_duration(summary.average_completed_seconds)
        print(f"Average time per completed milestone: {avg}")
    else:
        print("Average time per completed milestone: not enough data yet")

    if summary.estimated_remaining_seconds is not None:
        remaining = _format_duration(summary.estimated_remaining_seconds)
        print(f"Estimated remaining time: {remaining}")
    else:
        print("Estimated remaining time: not enough data yet")

    if summary.halted > 0:
        plural = "s" if summary.halted != 1 else ""
        print(
            f"\n{summary.halted} milestone{plural} halted and need human review "
            "-- run `amao logs` to see why."
        )

    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    db_path = _db_path(args.dir)
    if not os.path.exists(db_path):
        print(
            f"No orchestrator state found in {args.dir} -- has `amao run` been started here?",
            file=sys.stderr,
        )
        return 1

    state = StateManager(db_path)
    logs = state.get_audit_logs(milestone_id=args.milestone, limit=args.limit)

    if not logs:
        print("No audit log entries found.")
        return 0

    for entry in reversed(logs):
        details = entry["details"]
        details_text = details if isinstance(details, str) else json.dumps(details, indent=2)
        print(f"[{entry['timestamp']}] milestone={entry['milestone_id']} step={entry['step']}")
        print(_truncate(details_text))
        print()

    return 0


def _cmd_add_milestone(args: argparse.Namespace) -> int:
    # Unlike `status`/`logs`, this command's purpose is to write state, so
    # creating a fresh db for a brand new project (one that hasn't had
    # `amao run` started yet) is the intended behavior, not an accidental
    # side effect -- it lets a project be seeded with milestones up front.
    db_path = _db_path(args.dir)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    state = StateManager(db_path)
    state.add_milestone(args.title, args.description)
    print(
        f"Added milestone '{args.title}'. It will be picked up on its next iteration "
        "by any currently-running or future `amao run` against this directory."
    )
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

    status_parser = subparsers.add_parser("status", help="Show milestone progress for a project")
    status_parser.add_argument("--dir", required=True, help="Target project directory")
    status_parser.set_defaults(func=_cmd_status)

    logs_parser = subparsers.add_parser("logs", help="Show audit log entries for a project")
    logs_parser.add_argument("--dir", required=True, help="Target project directory")
    logs_parser.add_argument(
        "--milestone", type=int, default=None, help="Filter to a single milestone id"
    )
    logs_parser.add_argument(
        "--limit", type=int, default=20, help="Maximum number of entries to show (default: 20)"
    )
    logs_parser.set_defaults(func=_cmd_logs)

    add_milestone_parser = subparsers.add_parser(
        "add-milestone", help="Add a new pending milestone to a project"
    )
    add_milestone_parser.add_argument("--dir", required=True, help="Target project directory")
    add_milestone_parser.add_argument("--title", required=True, help="Milestone title")
    add_milestone_parser.add_argument("--description", required=True, help="Milestone description")
    add_milestone_parser.set_defaults(func=_cmd_add_milestone)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
