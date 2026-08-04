from amao.llm import LLMBackend
from amao.models import Milestone, MilestoneStatus
from amao.testing.bdd import BehaveBDDStrategy, GherkinGenerator


class _FakeBackend(LLMBackend):
    def __init__(self, content):
        self._content = content
        self.last_call = None

    def complete(self, *, system, user, cache_key, json_mode=False, images=()):
        self.last_call = {"system": system, "user": user, "cache_key": cache_key}
        return self._content


def _milestone(**overrides):
    defaults = dict(
        id=1,
        title="Add a login button",
        description="A button labeled Login appears on the homepage",
        status=MilestoneStatus.PENDING,
        attempts=0,
        last_error=None,
    )
    defaults.update(overrides)
    return Milestone(**defaults)


def test_gherkin_generator_returns_stripped_backend_output():
    backend = _FakeBackend("  Feature: x\n  Scenario: y\n    Given I visit the homepage\n  ")
    generator = GherkinGenerator(backend)

    result = generator.generate(_milestone())

    assert result.startswith("Feature:")
    assert result.endswith("homepage")


def test_gherkin_generator_prompt_constrains_step_vocabulary_and_includes_milestone():
    backend = _FakeBackend("Feature: x")
    generator = GherkinGenerator(backend)

    generator.generate(_milestone())

    assert "I visit the homepage" in backend.last_call["system"]
    assert "Add a login button" in backend.last_call["user"]
    assert backend.last_call["cache_key"] == "amao-generate-gherkin"


def test_behave_strategy_not_applicable_without_a_scenario(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")
    strategy = BehaveBDDStrategy()

    assert strategy.detect(str(tmp_path)) is False


def test_behave_strategy_not_applicable_without_a_web_app(tmp_path):
    strategy = BehaveBDDStrategy()
    strategy.set_scenario("Feature: x\nScenario: y\nGiven I visit the homepage\n")

    assert strategy.detect(str(tmp_path)) is False


def test_behave_strategy_applicable_with_both_scenario_and_web_app(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")
    strategy = BehaveBDDStrategy()
    strategy.set_scenario("Feature: x\nScenario: y\nGiven I visit the homepage\n")

    assert strategy.detect(str(tmp_path)) is True


def test_behave_strategy_shell_command_embeds_the_scenario(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")
    strategy = BehaveBDDStrategy()
    scenario = "Feature: x\nScenario: y\nGiven I visit the homepage\n"
    strategy.set_scenario(scenario)

    command = strategy.shell_command(str(tmp_path))

    assert "manage.py runserver 0.0.0.0:8000" in command
    assert scenario in command
    assert "behave /tmp/amao_bdd" in command


def test_behave_strategy_shell_command_raises_without_a_scenario(tmp_path):
    (tmp_path / "manage.py").write_text("# django\n")
    strategy = BehaveBDDStrategy()

    try:
        strategy.shell_command(str(tmp_path))
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


def test_behave_strategy_ensure_ready_builds_the_shared_webui_image(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "amao.testing.bdd.ensure_image_built",
        lambda tag, dockerfile: calls.append((tag, dockerfile)),
    )

    BehaveBDDStrategy().ensure_ready()

    assert calls == [("amao-webui-tester:local", "webui-tester.Dockerfile")]
