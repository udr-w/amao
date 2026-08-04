import pytest

from amao.cli import build_parser


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
