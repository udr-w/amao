import os

import pytest

from amao.cli import build_parser
from amao.config import config
from amao.models import MilestoneStatus
from amao.state_manager import StateManager


def test_run_subcommand_parses_required_args():
    parser = build_parser()

    args = parser.parse_args(["run", "--dir", "/tmp/x", "--goal", "build stuff"])

    assert args.command == "run"
    assert args.dir == "/tmp/x"
    assert args.goal == "build stuff"
    assert args.log_level == "INFO"


def test_log_level_is_configurable():
    parser = build_parser()

    args = parser.parse_args(["--log-level", "DEBUG", "run", "--dir", "/tmp/x", "--goal", "g"])

    assert args.log_level == "DEBUG"


def test_missing_required_run_args_exits_nonzero():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


def test_missing_command_exits_nonzero():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_status_subcommand_parses_required_args():
    parser = build_parser()

    args = parser.parse_args(["status", "--dir", "/tmp/x"])

    assert args.command == "status"
    assert args.dir == "/tmp/x"


def test_missing_required_status_args_exits_nonzero():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["status"])


def test_logs_subcommand_parses_required_args_with_defaults():
    parser = build_parser()

    args = parser.parse_args(["logs", "--dir", "/tmp/x"])

    assert args.command == "logs"
    assert args.dir == "/tmp/x"
    assert args.milestone is None
    assert args.limit == 20


def test_logs_subcommand_parses_optional_args():
    parser = build_parser()

    args = parser.parse_args(["logs", "--dir", "/tmp/x", "--milestone", "3", "--limit", "5"])

    assert args.milestone == 3
    assert args.limit == 5


def test_missing_required_logs_args_exits_nonzero():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["logs"])


def test_add_milestone_subcommand_parses_required_args():
    parser = build_parser()

    args = parser.parse_args(
        [
            "add-milestone",
            "--dir",
            "/tmp/x",
            "--title",
            "Do the thing",
            "--description",
            "Do it well",
        ]
    )

    assert args.command == "add-milestone"
    assert args.dir == "/tmp/x"
    assert args.title == "Do the thing"
    assert args.description == "Do it well"


def test_missing_required_add_milestone_args_exits_nonzero():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["add-milestone", "--dir", "/tmp/x", "--title", "T"])


def test_status_against_missing_project_prints_error_and_exits_nonzero(tmp_path, capsys):
    parser = build_parser()
    project_dir = str(tmp_path / "no_such_project")

    args = parser.parse_args(["status", "--dir", project_dir])
    exit_code = args.func(args)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "No orchestrator state found" in captured.err
    assert "amao run" in captured.err
    # Guard against StateManager._init_db() side effects: the db file must
    # not have been created just by checking status on a nonexistent project.
    assert not os.path.exists(os.path.join(project_dir, config.DB_FILENAME))


def test_status_reports_milestone_counts_from_shared_db_path(tmp_path, capsys):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    db_path = os.path.join(str(project_dir), config.DB_FILENAME)

    state = StateManager(db_path)
    state.add_milestone("First milestone", "Do the first thing")
    state.add_milestone("Second milestone", "Do the second thing")
    first = state.get_next_pending_milestone()
    assert first is not None
    assert first.title == "First milestone"
    state.update_milestone_status(first.id, MilestoneStatus.IN_PROGRESS, attempts=1)
    state.update_milestone_status(first.id, MilestoneStatus.COMPLETED, attempts=1)

    parser = build_parser()
    args = parser.parse_args(["status", "--dir", str(project_dir)])
    exit_code = args.func(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Milestones: 2 total" in captured.out
    assert "completed: 1" in captured.out
    assert "pending: 1" in captured.out
    assert "Current milestone: none in progress" in captured.out


def test_add_milestone_writes_to_the_same_db_path_status_reads(tmp_path, capsys):
    project_dir = tmp_path / "fresh_project"

    parser = build_parser()
    args = parser.parse_args(
        [
            "add-milestone",
            "--dir",
            str(project_dir),
            "--title",
            "Seeded milestone",
            "--description",
            "Seeded via CLI before any `amao run`",
        ]
    )
    exit_code = args.func(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Added milestone 'Seeded milestone'" in captured.out

    db_path = os.path.join(str(project_dir), config.DB_FILENAME)
    assert os.path.exists(db_path)

    state = StateManager(db_path)
    milestone = state.get_next_pending_milestone()
    assert milestone is not None
    assert milestone.title == "Seeded milestone"
    assert milestone.description == "Seeded via CLI before any `amao run`"


def test_logs_against_missing_project_prints_error_and_exits_nonzero(tmp_path, capsys):
    parser = build_parser()
    project_dir = str(tmp_path / "no_such_project")

    args = parser.parse_args(["logs", "--dir", project_dir])
    exit_code = args.func(args)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "No orchestrator state found" in captured.err
    assert not os.path.exists(os.path.join(project_dir, config.DB_FILENAME))


def test_logs_prints_entries_logged_via_state_manager(tmp_path, capsys):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    db_path = os.path.join(str(project_dir), config.DB_FILENAME)

    state = StateManager(db_path)
    state.add_milestone("A milestone", "Do something")
    milestone = state.get_next_pending_milestone()
    assert milestone is not None
    state.log(milestone.id, "planner_started", {"note": "kicking off planning"})
    state.log(milestone.id, "executor_finished", "raw string details")

    parser = build_parser()
    args = parser.parse_args(["logs", "--dir", str(project_dir)])
    exit_code = args.func(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "step=planner_started" in captured.out
    assert "kicking off planning" in captured.out
    assert "step=executor_finished" in captured.out
    assert "raw string details" in captured.out
    assert f"milestone={milestone.id}" in captured.out


def test_logs_reports_no_entries_when_empty(tmp_path, capsys):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    db_path = os.path.join(str(project_dir), config.DB_FILENAME)
    StateManager(db_path)

    parser = build_parser()
    args = parser.parse_args(["logs", "--dir", str(project_dir)])
    exit_code = args.func(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No audit log entries found." in captured.out
