"""Ensures a locally-tagged custom image exists before it's used, building it
from a Dockerfile shipped inside this package if it doesn't. Exists
specifically because installing Chromium via apt-get is too slow to redo on
every single test run -- observed ~14 minutes for chromium + its transitive
deps on a bare python:3.12-slim, dwarfing everything else in the pipeline.
Built once; every run after that reuses the image via Docker's own layer
cache in seconds. Never published to any registry -- local-only.
"""

from __future__ import annotations

import subprocess  # noqa: S404 -- always invoked with list args, no shell=True
from pathlib import Path

from amao.exceptions import TesterInfraError

_DOCKERFILE_DIR = Path(__file__).parent / "docker"


def image_exists(image_tag: str) -> bool:
    cmd = ["docker", "image", "inspect", image_tag]
    result = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    return result.returncode == 0


def ensure_image_built(image_tag: str, dockerfile_name: str, build_timeout: float = 1800) -> None:
    if image_exists(image_tag):
        return

    dockerfile_path = _DOCKERFILE_DIR / dockerfile_name
    cmd = ["docker", "build", "-t", image_tag, "-f", str(dockerfile_path), str(_DOCKERFILE_DIR)]
    try:
        result = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=build_timeout
        )
    except FileNotFoundError as e:
        raise TesterInfraError("docker is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise TesterInfraError(
            f"Building image {image_tag!r} timed out after {build_timeout}s"
        ) from e

    if result.returncode != 0:
        raise TesterInfraError(f"Failed to build image {image_tag!r}: {result.stderr.strip()}")
