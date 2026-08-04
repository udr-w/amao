import json

from amao.testing.strategies import (
    DEFAULT_STRATEGIES,
    GoTestStrategy,
    NpmTestStrategy,
    PytestStrategy,
    detect_strategies,
)


def test_pytest_strategy_detects_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    assert PytestStrategy().detect(str(tmp_path)) is True


def test_pytest_strategy_detects_loose_test_files(tmp_path):
    (tmp_path / "test_foo.py").write_text("def test_x(): assert True\n")

    assert PytestStrategy().detect(str(tmp_path)) is True


def test_pytest_strategy_skips_vendored_test_files_in_node_modules(tmp_path):
    vendored = tmp_path / "node_modules" / "somepkg"
    vendored.mkdir(parents=True)
    (vendored / "test_thing.py").write_text("# not ours\n")

    assert PytestStrategy().detect(str(tmp_path)) is False


def test_pytest_strategy_does_not_detect_empty_dir(tmp_path):
    assert PytestStrategy().detect(str(tmp_path)) is False


def test_npm_strategy_detects_package_json_with_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))

    assert NpmTestStrategy().detect(str(tmp_path)) is True


def test_npm_strategy_ignores_package_json_without_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "webpack"}}))

    assert NpmTestStrategy().detect(str(tmp_path)) is False


def test_npm_strategy_tolerates_malformed_package_json(tmp_path):
    (tmp_path / "package.json").write_text("{not valid json")

    assert NpmTestStrategy().detect(str(tmp_path)) is False


def test_go_strategy_detects_go_mod(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/x\n")

    assert GoTestStrategy().detect(str(tmp_path)) is True


def test_go_strategy_does_not_run_on_a_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    assert GoTestStrategy().detect(str(tmp_path)) is False


def test_detect_strategies_only_returns_applicable_ones(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/x\n")

    applicable = detect_strategies(str(tmp_path), DEFAULT_STRATEGIES)

    assert [s.name for s in applicable] == ["go-test"]


def test_detect_strategies_can_return_multiple_for_a_mixed_stack_project(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/x\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))

    applicable = detect_strategies(str(tmp_path), DEFAULT_STRATEGIES)

    assert {s.name for s in applicable} == {"go-test", "npm-test"}


def test_detect_strategies_returns_nothing_for_an_unrecognized_project(tmp_path):
    (tmp_path / "README.md").write_text("just docs\n")

    assert detect_strategies(str(tmp_path), DEFAULT_STRATEGIES) == []
