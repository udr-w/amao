import json

import pytest

from amao.testing.strategies import (
    DEFAULT_STRATEGIES,
    GoTestStrategy,
    NpmTestStrategy,
    PytestStrategy,
    PythonWebUIStrategy,
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


def test_web_ui_strategy_detects_django_via_manage_py(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")

    assert PythonWebUIStrategy().detect(str(tmp_path)) is True


def test_web_ui_strategy_detects_flask_via_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("Flask==3.0.0\n")

    assert PythonWebUIStrategy().detect(str(tmp_path)) is True


def test_web_ui_strategy_detects_fastapi_via_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi"]\n')

    assert PythonWebUIStrategy().detect(str(tmp_path)) is True


def test_web_ui_strategy_detects_bare_static_site(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>\n")

    assert PythonWebUIStrategy().detect(str(tmp_path)) is True


def test_web_ui_strategy_does_not_detect_a_plain_backend_only_project(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")

    assert PythonWebUIStrategy().detect(str(tmp_path)) is False


def test_web_ui_strategy_shell_command_uses_django_runserver(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")

    command = PythonWebUIStrategy().shell_command(str(tmp_path))

    assert "manage.py runserver 0.0.0.0:8000" in command
    # Chromium/selenium are pre-baked into the amao-webui-tester:local image
    # (see ensure_ready()), not installed fresh on every run -- that was the
    # ~14-minute-per-run problem this design replaced.
    assert "apt-get" not in command
    assert "pip install --quiet --no-input selenium" not in command


def test_web_ui_strategy_shell_command_uses_http_server_for_static_sites(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>\n")

    command = PythonWebUIStrategy().shell_command(str(tmp_path))

    assert "python -m http.server 8000" in command
    # No app dependencies to install for a bare static site.
    assert "requirements.txt" not in command


def test_web_ui_strategy_shell_command_raises_if_called_on_a_non_matching_project(tmp_path):
    with pytest.raises(RuntimeError):
        PythonWebUIStrategy().shell_command(str(tmp_path))


def test_web_ui_strategy_is_included_in_default_strategies():
    assert any(s.name == "web-ui-python" for s in DEFAULT_STRATEGIES)


def test_web_ui_strategy_ensure_ready_builds_its_pinned_image(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "amao.testing.strategies.ensure_image_built",
        lambda tag, dockerfile: calls.append((tag, dockerfile)),
    )

    PythonWebUIStrategy().ensure_ready()

    assert calls == [("amao-webui-tester:local", "webui-tester.Dockerfile")]


def test_tier_one_strategies_have_a_no_op_ensure_ready():
    # Must not raise and must not need mocking -- there's nothing to build.
    PytestStrategy().ensure_ready()
    NpmTestStrategy().ensure_ready()
    GoTestStrategy().ensure_ready()
