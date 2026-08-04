import subprocess

import pytest

from amao.exceptions import TesterInfraError
from amao.testing.sandbox import DockerSandbox


def _completed(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_returns_exit_code_and_combined_output(monkeypatch):
    monkeypatch.setattr(
        "amao.testing.sandbox.subprocess.run",
        lambda *a, **k: _completed(0, stdout="ok\n"),
    )
    sandbox = DockerSandbox(timeout=30)

    exit_code, output = sandbox.run("/tmp/project", "python:3.12-slim", "pytest -q")

    assert exit_code == 0
    assert "ok" in output


def test_run_returns_nonzero_exit_as_a_normal_result_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "amao.testing.sandbox.subprocess.run",
        lambda *a, **k: _completed(1, stderr="AssertionError: boom\n"),
    )
    sandbox = DockerSandbox(timeout=30)

    exit_code, output = sandbox.run("/tmp/project", "python:3.12-slim", "pytest -q")

    assert exit_code == 1
    assert "AssertionError" in output


def test_run_uses_expected_docker_invocation(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _completed(0)

    monkeypatch.setattr("amao.testing.sandbox.subprocess.run", fake_run)
    sandbox = DockerSandbox(timeout=45)

    sandbox.run(str(tmp_path), "node:20-slim", "npm test")

    cmd = captured["cmd"]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path}:/workspace" in cmd
    assert "node:20-slim" in cmd
    assert cmd[-3:] == ["sh", "-c", "npm test"]
    assert captured["kwargs"]["timeout"] == 45


@pytest.mark.parametrize(
    "stderr",
    [
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        "Unable to find image 'bogus:latest' locally",
        "pull access denied for private/image, repository does not exist",
    ],
)
def test_run_raises_tester_infra_error_for_docker_level_failures(monkeypatch, stderr):
    monkeypatch.setattr(
        "amao.testing.sandbox.subprocess.run",
        lambda *a, **k: _completed(1, stderr=stderr),
    )
    sandbox = DockerSandbox(timeout=30)

    with pytest.raises(TesterInfraError):
        sandbox.run("/tmp/project", "bogus:latest", "pytest -q")


def test_run_raises_tester_infra_error_when_docker_binary_missing(monkeypatch):
    def raiser(*args, **kwargs):
        raise FileNotFoundError("no such file: docker")

    monkeypatch.setattr("amao.testing.sandbox.subprocess.run", raiser)
    sandbox = DockerSandbox(timeout=30)

    with pytest.raises(TesterInfraError):
        sandbox.run("/tmp/project", "python:3.12-slim", "pytest -q")


def test_run_treats_timeout_as_a_test_failure_not_an_infra_error(monkeypatch):
    def raiser(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=30, output="partial\n", stderr="")

    monkeypatch.setattr("amao.testing.sandbox.subprocess.run", raiser)
    sandbox = DockerSandbox(timeout=30)

    exit_code, output = sandbox.run("/tmp/project", "python:3.12-slim", "pytest -q")

    assert exit_code == 124
    assert "timed out" in output.lower()
