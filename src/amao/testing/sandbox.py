"""Docker-based sandbox for running a project's own test suite without
executing LLM-generated code on the host. Shells out to the `docker` CLI
via subprocess -- same pattern GitHelper uses for `git` -- rather than
adding a docker-py SDK dependency.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 -- always invoked with list args, no shell=True

from amao.exceptions import TesterInfraError


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _docker_user_args() -> list[str]:
    """Run the container as the host user, not root.

    Without this, official images (python:3.12-slim, node:20-slim, ...) run
    as root by default, so anything the test command writes into the mounted
    project dir (.pytest_cache, __pycache__, node_modules) ends up owned by
    root on the host -- undeletable by the user who ran amao without sudo.
    getuid()/getgid() are POSIX-only; skipped on platforms without them.
    """
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return ["-u", f"{os.getuid()}:{os.getgid()}"]
    return []


_INFRA_ERROR_MARKERS = (
    "cannot connect to the docker daemon",
    "unable to find image",
    "pull access denied",
    "no matching manifest",
    "manifest unknown",
    "error response from daemon",
)


class DockerSandbox:
    """Runs a shell command inside a disposable container with the project
    directory mounted at /workspace. Distinguishes "the sandbox itself
    couldn't run" (raises TesterInfraError) from "the command inside it
    exited nonzero" (returned as a normal result -- that's a test failure,
    not an infra problem, and it's up to the caller what to do with it).

    Not run with --network none: setup steps (pip install, npm install) need
    to reach public package registries. That's a real, deliberate narrowing
    of isolation -- bounded to fetching packages, not full network access to
    arbitrary code -- see TESTER_AGENT_PLAN.md's "Known limitations".
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def run(self, project_dir: str, image: str, shell_command: str) -> tuple[int, str]:
        cmd = [
            "docker",
            "run",
            "--rm",
            *_docker_user_args(),
            # An arbitrary host UID has no /etc/passwd entry inside the
            # container, so HOME is otherwise unset/unwritable -- breaks
            # npm's cache directory and similar tools that need $HOME to
            # exist and be writable.
            "-e",
            "HOME=/tmp",
            "-v",
            f"{os.path.abspath(project_dir)}:/workspace",
            "-w",
            "/workspace",
            image,
            "sh",
            "-c",
            shell_command,
        ]
        try:
            result = subprocess.run(  # noqa: S603
                cmd, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError as e:
            raise TesterInfraError("docker is not installed or not on PATH") from e
        except subprocess.TimeoutExpired as e:
            # A hanging test is plausibly the generated code's fault (infinite
            # loop, deadlock) -- treat as a test failure, not an infra error.
            timed_out_output = _to_text(e.stdout) + _to_text(e.stderr)
            return 124, f"Test run timed out after {self.timeout}s\n{timed_out_output}"

        stderr = _to_text(result.stderr)
        output = _to_text(result.stdout) + stderr
        if result.returncode != 0 and self._looks_like_infra_failure(stderr):
            raise TesterInfraError(
                f"Docker sandbox could not run image {image!r}: {stderr.strip()}"
            )
        return result.returncode, output

    @staticmethod
    def _looks_like_infra_failure(stderr: str) -> bool:
        lowered = stderr.lower()
        return any(marker in lowered for marker in _INFRA_ERROR_MARKERS)
