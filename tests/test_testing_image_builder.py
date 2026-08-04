import subprocess

import pytest

from amao.exceptions import TesterInfraError
from amao.testing.image_builder import ensure_image_built, image_exists


def _completed(returncode):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")


def test_image_exists_true_when_inspect_succeeds(monkeypatch):
    monkeypatch.setattr("amao.testing.image_builder.subprocess.run", lambda *a, **k: _completed(0))

    assert image_exists("some:tag") is True


def test_image_exists_false_when_inspect_fails(monkeypatch):
    monkeypatch.setattr("amao.testing.image_builder.subprocess.run", lambda *a, **k: _completed(1))

    assert image_exists("some:tag") is False


def test_ensure_image_built_skips_build_when_already_present(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "amao.testing.image_builder.subprocess.run",
        lambda cmd, **k: calls.append(cmd) or _completed(0),
    )

    ensure_image_built("amao-webui-tester:local", "webui-tester.Dockerfile")

    assert len(calls) == 1  # only the `docker image inspect` check, no build
    assert calls[0][:2] == ["docker", "image"]


def test_ensure_image_built_builds_when_missing(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # First call is the inspect check (missing -> nonzero); second is the build.
        return _completed(1) if cmd[:2] == ["docker", "image"] else _completed(0)

    monkeypatch.setattr("amao.testing.image_builder.subprocess.run", fake_run)

    ensure_image_built("amao-webui-tester:local", "webui-tester.Dockerfile")

    assert len(calls) == 2
    assert calls[1][:3] == ["docker", "build", "-t"]
    assert "webui-tester.Dockerfile" in calls[1][calls[1].index("-f") + 1]


def test_ensure_image_built_raises_tester_infra_error_on_build_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "image"]:
            return _completed(1)
        result = _completed(1)
        result.stderr = "Dockerfile parse error"
        return result

    monkeypatch.setattr("amao.testing.image_builder.subprocess.run", fake_run)

    with pytest.raises(TesterInfraError, match="Dockerfile parse error"):
        ensure_image_built("amao-webui-tester:local", "webui-tester.Dockerfile")


def test_ensure_image_built_raises_tester_infra_error_when_docker_missing(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "image"]:
            return _completed(1)
        raise FileNotFoundError("no docker")

    monkeypatch.setattr("amao.testing.image_builder.subprocess.run", fake_run)

    with pytest.raises(TesterInfraError):
        ensure_image_built("amao-webui-tester:local", "webui-tester.Dockerfile")


def test_ensure_image_built_raises_tester_infra_error_on_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["docker", "image"]:
            return _completed(1)
        raise subprocess.TimeoutExpired(cmd="docker build", timeout=1800)

    monkeypatch.setattr("amao.testing.image_builder.subprocess.run", fake_run)

    with pytest.raises(TesterInfraError, match="timed out"):
        ensure_image_built("amao-webui-tester:local", "webui-tester.Dockerfile", build_timeout=1800)
