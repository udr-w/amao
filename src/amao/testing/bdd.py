"""BDD/Cucumber-style testing: an LLM generates a Gherkin scenario from the
milestone description, constrained to a small fixed vocabulary of generic
steps (visit/click/fill-in/see/title), executed via `behave` against the
same running app PythonWebUIStrategy already knows how to start. Reuses
that strategy's app-detection and app-start logic rather than duplicating
it -- BDD only makes sense for a project that already has a UI to click
through, so applicability piggybacks on the exact same detection.

A generated scenario is deliberately restricted to a handful of step
phrases rather than letting the LLM invent arbitrary Gherkin: without a
fixed, known vocabulary, there would be no way to ship matching step
definitions ahead of time, since we can't know what an LLM might phrase a
"click the button" step as. Trade generality for something that actually
executes.
"""

from __future__ import annotations

from amao.llm import LLMBackend
from amao.models import Milestone
from amao.rate_limiter import with_retry_and_backoff
from amao.testing.image_builder import ensure_image_built
from amao.testing.strategies import (
    _PYTHON_WEB_START_COMMANDS,
    _UI_PORT,
    TestStrategy,
    _detect_python_web_kind,
    _write_heredoc,
)

_ALLOWED_STEPS = (
    "Given I visit the homepage",
    'When I click "<text>"',
    'When I fill in "<field>" with "<value>"',
    'Then I should see "<text>"',
    'Then the page title should contain "<text>"',
)

_GHERKIN_SYSTEM_PROMPT = (
    "You generate an automated UI test scenario in Gherkin for a web application, to "
    "check that a milestone was implemented correctly. You MUST use ONLY these step "
    "phrases (substitute your own quoted text where shown, keep the wording otherwise "
    "exact):\n" + "\n".join(f"  {s}" for s in _ALLOWED_STEPS) + "\n"
    "Do not invent any other step phrasing -- an unrecognized step will fail to match "
    "and the whole scenario will error. Respond with ONLY the feature file content "
    "(Feature:/Scenario:/Given/When/Then lines) -- no explanation, no markdown fences."
)


class GherkinGenerator:
    def __init__(self, backend: LLMBackend) -> None:
        self.backend = backend

    @with_retry_and_backoff()
    def generate(self, milestone: Milestone) -> str:
        user_prompt = f"Milestone: {milestone.title}\nRequirements: {milestone.description}"
        content = self.backend.complete(
            system=_GHERKIN_SYSTEM_PROMPT,
            user=user_prompt,
            cache_key="amao-generate-gherkin",
        )
        return content.strip()


_BEHAVE_ENVIRONMENT = f"""
import socket
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def _wait_for_port(host, port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def before_all(context):
    if not _wait_for_port("127.0.0.1", {_UI_PORT}):
        raise RuntimeError("application did not start listening on port {_UI_PORT}")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    context.driver = webdriver.Chrome(options=options)
    context.driver.set_page_load_timeout(20)


def after_all(context):
    context.driver.quit()
"""

# Deliberately NOT an f-string: behave's own step-parameter syntax
# ("{text}", "{field}", "{value}") uses literal braces that would otherwise
# collide with Python's f-string placeholder syntax. __UI_PORT__ is
# substituted below instead.
_BEHAVE_STEPS_TEMPLATE = """
from behave import given, when, then
from selenium.webdriver.common.by import By

_BASE_URL = "http://127.0.0.1:__UI_PORT__/"


def _find_clickable_by_text(context, text):
    candidates = context.driver.find_elements(By.XPATH, "//*")
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            label = (el.text or "") + " " + (el.get_attribute("value") or "")
        except Exception:
            continue
        if text.lower() in label.lower():
            return el
    raise AssertionError("No clickable element containing text %r was found" % text)


def _find_input_by_label(context, field):
    inputs = context.driver.find_elements(By.XPATH, "//input | //textarea")
    for el in inputs:
        attrs = " ".join(
            filter(
                None,
                [
                    el.get_attribute("name"),
                    el.get_attribute("placeholder"),
                    el.get_attribute("id"),
                ],
            )
        )
        if field.lower() in attrs.lower():
            return el
    raise AssertionError("No input/textarea matching %r was found" % field)


@given("I visit the homepage")
def step_visit_homepage(context):
    context.driver.get(_BASE_URL)


@when('I click "{text}"')
def step_click(context, text):
    _find_clickable_by_text(context, text).click()


@when('I fill in "{field}" with "{value}"')
def step_fill_in(context, field, value):
    el = _find_input_by_label(context, field)
    el.clear()
    el.send_keys(value)


@then('I should see "{text}"')
def step_should_see(context, text):
    html = context.driver.page_source or ""
    assert text.lower() in html.lower(), "Expected to see %r on the page" % text


@then('the page title should contain "{text}"')
def step_title_contains(context, text):
    title = context.driver.title or ""
    assert text.lower() in title.lower(), (
        "Expected page title to contain %r, got %r" % (text, title)
    )
"""
_BEHAVE_STEPS = _BEHAVE_STEPS_TEMPLATE.replace("__UI_PORT__", str(_UI_PORT))


class BehaveBDDStrategy(TestStrategy):
    """Runs an LLM-generated Gherkin scenario against the same app
    PythonWebUIStrategy knows how to start, via `behave`. Requires a
    scenario to have been supplied via set_scenario() before detect() will
    report True -- generating one needs an LLM call, which TesterAgent
    makes (via GherkinGenerator), not this class; there's nothing to run
    without it.
    """

    name = "bdd-behave"
    docker_image = "amao-webui-tester:local"

    def __init__(self) -> None:
        self._scenario: str | None = None

    def set_scenario(self, scenario: str | None) -> None:
        self._scenario = scenario

    def detect(self, project_dir: str) -> bool:
        return bool(self._scenario) and _detect_python_web_kind(project_dir) is not None

    def ensure_ready(self) -> None:
        ensure_image_built(self.docker_image, "webui-tester.Dockerfile")

    def shell_command(self, project_dir: str) -> str:
        kind = _detect_python_web_kind(project_dir)
        if kind is None or not self._scenario:
            raise RuntimeError(
                "BehaveBDDStrategy.shell_command called without a matching project/scenario"
            )
        start_cmd = _PYTHON_WEB_START_COMMANDS[kind]

        install_deps = ""
        if kind != "static":
            install_deps = (
                "pip install --quiet --no-input -e . 2>/dev/null "
                "|| pip install --quiet --no-input -r requirements.txt 2>/dev/null\n"
            )

        # /tmp here is the disposable sandbox container's own /tmp -- see the
        # same note in strategies.py's PythonWebUIStrategy.
        write_env = _write_heredoc(_BEHAVE_ENVIRONMENT, "/tmp/amao_bdd/environment.py")  # noqa: S108
        write_steps = _write_heredoc(_BEHAVE_STEPS, "/tmp/amao_bdd/steps/steps.py")  # noqa: S108
        write_feature = _write_heredoc(self._scenario, "/tmp/amao_bdd/amao.feature")  # noqa: S108
        return (
            'export PATH="$HOME/.local/bin:$PATH"\n'
            f"{install_deps}"
            "mkdir -p /tmp/amao_bdd/steps\n"
            f"{write_env}\n"
            f"{write_steps}\n"
            f"{write_feature}\n"
            f"({start_cmd}) > /tmp/amao_app.log 2>&1 &\n"
            "behave /tmp/amao_bdd"
        )
