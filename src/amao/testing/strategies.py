"""Test strategies: each declares whether it applies to a project and how to
run it. The Tester only ever runs strategies whose detect() returns True --
a Go project's container is never given Python or Node, and vice versa. This
is deliberate: irrelevant tooling must never run just because it's supported
somewhere in this module.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Sequence

from amao.testing.image_builder import ensure_image_built

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}

# Fixed port every UI strategy's app-under-test binds to; the embedded driver
# script always looks here. Not configurable -- there's only ever one
# container, so there's no port-collision risk to guard against.
_UI_PORT = 8000
_SCREENSHOT_FILENAME = ".amao_screenshot.png"


class TestStrategy(ABC):
    name: str
    docker_image: str
    # Relative to project_dir; set by strategies that can produce a UI
    # screenshot. None for everything else (the default Tier-1 strategies).
    screenshot_relpath: str | None = None

    @abstractmethod
    def detect(self, project_dir: str) -> bool:
        """Return True if this strategy applies to the project at project_dir."""

    @abstractmethod
    def shell_command(self, project_dir: str) -> str:
        """Setup + run, as a single shell command executed via `sh -c`.

        Takes project_dir (read-only inspection, e.g. to pick a concrete
        app-start command) even though the strategy instance itself is
        stateless and shared -- avoids storing per-run detection results on
        a singleton, which would not be safe to reuse across milestones.
        """

    def ensure_ready(self) -> None:  # noqa: B027 -- intentional default no-op hook
        """Called once before this strategy's first `sandbox.run()` in a
        process. Default no-op -- Tier-1 strategies use official images with
        nothing to prepare. Strategies backed by a custom local image (e.g.
        PythonWebUIStrategy) override this to build it if missing.
        """


class PytestStrategy(TestStrategy):
    name = "pytest"
    docker_image = "python:3.12-slim"

    def detect(self, project_dir: str) -> bool:
        markers = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")
        if any(os.path.exists(os.path.join(project_dir, m)) for m in markers):
            return True
        return self._has_test_files(project_dir)

    @staticmethod
    def _has_test_files(project_dir: str) -> bool:
        for _root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if not name.endswith(".py"):
                    continue
                if name.startswith("test_") or name.endswith("_test.py"):
                    return True
        return False

    def shell_command(self, project_dir: str) -> str:
        # Running as a non-root host UID (see sandbox.py) means pip installs
        # land in --user mode under $HOME/.local, not the system site-packages
        # -- and that bin dir isn't on PATH by default.
        return (
            'export PATH="$HOME/.local/bin:$PATH"; '
            "pip install --quiet --no-input pytest; "
            "pip install --quiet --no-input -e . 2>/dev/null "
            "|| pip install --quiet --no-input -r requirements.txt 2>/dev/null; "
            "pytest -q"
        )


class NpmTestStrategy(TestStrategy):
    name = "npm-test"
    docker_image = "node:20-slim"

    def detect(self, project_dir: str) -> bool:
        package_json = os.path.join(project_dir, "package.json")
        if not os.path.exists(package_json):
            return False
        try:
            with open(package_json, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        return bool(data.get("scripts", {}).get("test"))

    def shell_command(self, project_dir: str) -> str:
        return "npm install --no-audit --no-fund --silent; npm test --silent"


class GoTestStrategy(TestStrategy):
    name = "go-test"
    docker_image = "golang:1.24-bookworm"

    def detect(self, project_dir: str) -> bool:
        return os.path.exists(os.path.join(project_dir, "go.mod"))

    def shell_command(self, project_dir: str) -> str:
        return "go test ./..."


def _read_dependency_text(project_dir: str) -> str:
    """Lowercased contents of common Python dependency manifests, concatenated
    -- cheap, good-enough signal for 'does this project depend on Flask' etc.
    without needing a real resolver or import graph.
    """
    parts = []
    for name in ("requirements.txt", "pyproject.toml"):
        path = os.path.join(project_dir, name)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    parts.append(f.read().lower())
            except OSError:
                pass
    return "\n".join(parts)


def _detect_python_web_kind(project_dir: str) -> str | None:
    if os.path.exists(os.path.join(project_dir, "manage.py")):
        return "django"
    deps = _read_dependency_text(project_dir)
    if "flask" in deps:
        return "flask"
    if "fastapi" in deps:
        return "fastapi"
    if os.path.exists(os.path.join(project_dir, "index.html")):
        return "static"
    return None


_PYTHON_WEB_START_COMMANDS = {
    "django": f"python manage.py runserver 0.0.0.0:{_UI_PORT}",
    "flask": (
        f"python app.py || python main.py "
        f"|| FLASK_APP=app flask run --host=0.0.0.0 --port={_UI_PORT}"
    ),
    "fastapi": (
        f"uvicorn app:app --host 0.0.0.0 --port {_UI_PORT} "
        f"|| uvicorn main:app --host 0.0.0.0 --port {_UI_PORT}"
    ),
    "static": f"python -m http.server {_UI_PORT}",
}

# Selenium's Python bindings, not the framework being tested, are what need
# this driver -- it works identically regardless of which Python web
# framework (or none, for the static case) is serving the page.
_PYTHON_SELENIUM_DRIVER = f"""
import socket
import sys
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def wait_for_port(host, port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


if not wait_for_port("127.0.0.1", {_UI_PORT}):
    print("ERROR: application did not start listening on port {_UI_PORT} within timeout")
    sys.exit(1)

options = Options()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1280,800")

driver = webdriver.Chrome(options=options)
try:
    driver.set_page_load_timeout(20)
    driver.get("http://127.0.0.1:{_UI_PORT}/")
    time.sleep(1)
    title = driver.title
    driver.save_screenshot("/workspace/{_SCREENSHOT_FILENAME}")
    print("PAGE_TITLE=" + repr(title))
    html = driver.page_source or ""
    if "<html" not in html.lower():
        print("ERROR: page did not render valid HTML")
        sys.exit(1)
    print("UI_CHECK_OK")
finally:
    driver.quit()
"""


def _write_heredoc(script: str, dest: str) -> str:
    """A quoted heredoc ('AMAO_EOF') so none of the embedded script's own
    $variables, backticks, or quotes get interpreted by the outer shell.
    """
    return f"cat <<'AMAO_EOF' > {dest}\n{script}\nAMAO_EOF"


class PythonWebUIStrategy(TestStrategy):
    """Starts a detected Python web app (Django/Flask/FastAPI) or a bare
    static HTML site, drives headless Chromium against it via Selenium, and
    captures a screenshot for the Reviewer.

    Deliberately narrow scope for this pass: verifies the homepage renders
    valid HTML and captures what it looks like. It does not yet attempt to
    click through interactions described in a milestone -- see the BDD
    strategy for that, which layers on top of the same running app.
    """

    name = "web-ui-python"
    # A pre-built local image (Chromium + selenium already installed) --
    # NOT an official upstream image like the Tier-1 strategies use. See
    # image_builder.py's docstring for why: apt-get installing Chromium
    # fresh on every run was measured at ~14 minutes, unworkable for a
    # pipeline that may retry a milestone several times.
    docker_image = "amao-webui-tester:local"
    screenshot_relpath = _SCREENSHOT_FILENAME

    def detect(self, project_dir: str) -> bool:
        return _detect_python_web_kind(project_dir) is not None

    def ensure_ready(self) -> None:
        ensure_image_built(self.docker_image, "webui-tester.Dockerfile")

    def shell_command(self, project_dir: str) -> str:
        kind = _detect_python_web_kind(project_dir)
        if kind is None:
            # detect() is always called first by the registry, so this
            # shouldn't happen in practice -- fail loudly rather than guess.
            raise RuntimeError("PythonWebUIStrategy.shell_command called on a non-matching project")
        start_cmd = _PYTHON_WEB_START_COMMANDS[kind]

        install_deps = ""
        if kind != "static":
            install_deps = (
                "pip install --quiet --no-input -e . 2>/dev/null "
                "|| pip install --quiet --no-input -r requirements.txt 2>/dev/null\n"
            )

        # /tmp here is the disposable sandbox container's own /tmp, not the
        # host's -- no shared-filesystem race/symlink concern applies.
        write_driver = _write_heredoc(_PYTHON_SELENIUM_DRIVER, "/tmp/amao_driver.py")  # noqa: S108
        return (
            'export PATH="$HOME/.local/bin:$PATH"\n'
            f"rm -f /workspace/{_SCREENSHOT_FILENAME}\n"
            f"{install_deps}"
            f"{write_driver}\n"
            f"({start_cmd}) > /tmp/amao_app.log 2>&1 &\n"
            "python /tmp/amao_driver.py"
        )


DEFAULT_STRATEGIES: tuple[TestStrategy, ...] = (
    PytestStrategy(),
    NpmTestStrategy(),
    GoTestStrategy(),
    PythonWebUIStrategy(),
)


def detect_strategies(
    project_dir: str, strategies: Sequence[TestStrategy] = DEFAULT_STRATEGIES
) -> list[TestStrategy]:
    return [s for s in strategies if s.detect(project_dir)]
