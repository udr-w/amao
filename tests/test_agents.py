from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from amao.agents import LocalExecutorAgent, PlannerAgent, ReviewerAgent, _strip_code_fence
from amao.exceptions import ExecutionError, PlanningError
from amao.git_helper import GitHelper
from amao.models import Milestone, MilestoneStatus


class _FakeOpenAI:
    def __init__(self, content):
        self._content = content
        self.last_kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _FakeAnthropic:
    """Uses real anthropic.types.TextBlock so review_code's isinstance check passes."""

    def __init__(self, content):
        self._content = content
        self.last_kwargs = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(content=[TextBlock(type="text", text=self._content)])


def _milestone(**overrides):
    defaults = dict(
        id=1,
        title="Add feature",
        description="Do the thing",
        status=MilestoneStatus.PENDING,
        attempts=0,
        last_error=None,
    )
    defaults.update(overrides)
    return Milestone(**defaults)


def test_strip_code_fence_extracts_fenced_content():
    assert _strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}\n'


def test_strip_code_fence_passes_through_plain_text():
    assert _strip_code_fence("plain text") == "plain text\n"


def test_strip_code_fence_preserves_trailing_newline_needed_by_diffs():
    diff_with_newline = "diff --git a/x b/x\n+content\n"
    assert _strip_code_fence(diff_with_newline) == diff_with_newline
    assert _strip_code_fence(diff_with_newline.rstrip("\n")) == diff_with_newline


def test_plan_project_parses_milestones():
    client = _FakeOpenAI('{"milestones": [{"title": "A", "description": "do a"}]}')
    planner = PlannerAgent(client, model="gpt-4o")

    milestones = planner.plan_project("build a thing", max_milestones=10)

    assert milestones == [{"title": "A", "description": "do a"}]


def test_plan_project_raises_on_malformed_json():
    planner = PlannerAgent(_FakeOpenAI("not json"), model="gpt-4o")

    with pytest.raises(PlanningError):
        planner.plan_project("build a thing", max_milestones=10)


def test_plan_project_raises_on_missing_milestones_key():
    planner = PlannerAgent(_FakeOpenAI('{"oops": []}'), model="gpt-4o")

    with pytest.raises(PlanningError):
        planner.plan_project("x", max_milestones=10)


def test_plan_project_raises_on_malformed_milestone_shape():
    planner = PlannerAgent(_FakeOpenAI('{"milestones": [{"title": "A"}]}'), model="gpt-4o")

    with pytest.raises(PlanningError):
        planner.plan_project("x", max_milestones=10)


def test_generate_task_prompt_returns_text():
    planner = PlannerAgent(_FakeOpenAI("do step 1"), model="gpt-4o")

    assert planner.generate_task_prompt(_milestone()) == "do step 1"


def test_generate_task_prompt_raises_on_empty_response():
    planner = PlannerAgent(_FakeOpenAI(None), model="gpt-4o")

    with pytest.raises(PlanningError):
        planner.generate_task_prompt(_milestone())


def test_plan_project_uses_static_system_message_and_cache_key():
    client = _FakeOpenAI('{"milestones": [{"title": "A", "description": "d"}]}')
    planner = PlannerAgent(client, model="gpt-4o")

    planner.plan_project("build a thing", max_milestones=10)

    messages = client.last_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "Project Goal" not in messages[0]["content"]  # static, no per-call data
    assert client.last_kwargs["prompt_cache_key"] == "amao-plan-project"


def test_generate_task_prompt_uses_static_system_message_and_cache_key():
    client = _FakeOpenAI("do step 1")
    planner = PlannerAgent(client, model="gpt-4o")

    planner.generate_task_prompt(_milestone())

    messages = client.last_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "Add feature" not in messages[0]["content"]  # static, no per-call data
    assert client.last_kwargs["prompt_cache_key"] == "amao-generate-task-prompt"


_VALID_NEW_FILE_DIFF = (
    "diff --git a/hello.txt b/hello.txt\n"
    "new file mode 100644\n"
    "index 0000000..e69de29\n"
    "--- /dev/null\n"
    "+++ b/hello.txt\n"
    "@@ -0,0 +1 @@\n"
    "+hi\n"
)


def test_executor_applies_valid_diff(tmp_path):
    git = GitHelper(str(tmp_path))
    git.init_repo()
    executor = LocalExecutorAgent(
        git, _FakeOpenAI(_VALID_NEW_FILE_DIFF), model="gpt-4o", max_diff_chars=10_000
    )

    result = executor.execute_prompt("add hello.txt")

    assert result.status == "SUCCESS"
    assert (tmp_path / "hello.txt").read_text() == "hi\n"


def test_executor_strips_markdown_fence_around_diff(tmp_path):
    git = GitHelper(str(tmp_path))
    git.init_repo()
    fenced = f"```diff\n{_VALID_NEW_FILE_DIFF}```"
    executor = LocalExecutorAgent(git, _FakeOpenAI(fenced), model="gpt-4o", max_diff_chars=10_000)

    executor.execute_prompt("add hello.txt")

    assert (tmp_path / "hello.txt").read_text() == "hi\n"


def test_executor_raises_on_empty_response(tmp_path):
    git = GitHelper(str(tmp_path))
    git.init_repo()
    executor = LocalExecutorAgent(git, _FakeOpenAI(None), model="gpt-4o", max_diff_chars=10_000)

    with pytest.raises(ExecutionError):
        executor.execute_prompt("do something")


def test_executor_uses_cache_key(tmp_path):
    git = GitHelper(str(tmp_path))
    git.init_repo()
    client = _FakeOpenAI(_VALID_NEW_FILE_DIFF)
    executor = LocalExecutorAgent(git, client, model="gpt-4o", max_diff_chars=10_000)

    executor.execute_prompt("add hello.txt")

    assert client.last_kwargs["prompt_cache_key"] == "amao-execute-prompt"


def test_review_code_returns_result_for_approved():
    reviewer = ReviewerAgent(
        _FakeAnthropic('{"status": "APPROVED", "feedback": "looks good"}'),
        model="claude-3-7-sonnet-20250219",
    )

    result = reviewer.review_code(_milestone(), "diff --git a/x b/x")

    assert result.approved
    assert result.feedback == "looks good"


def test_review_code_marks_system_prompt_as_cacheable():
    client = _FakeAnthropic('{"status": "APPROVED", "feedback": "ok"}')
    reviewer = ReviewerAgent(client, model="claude-3-7-sonnet-20250219")

    reviewer.review_code(_milestone(), "diff --git a/x b/x")

    system = client.last_kwargs["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # Dynamic per-call data must stay in the user message, never in the cached block.
    assert "Add feature" not in system[0]["text"]


def test_review_code_rejects_empty_diff_without_calling_llm():
    reviewer = ReviewerAgent(
        _FakeAnthropic("should not be used"), model="claude-3-7-sonnet-20250219"
    )

    result = reviewer.review_code(_milestone(), "   ")

    assert not result.approved


def test_review_code_raises_on_malformed_json():
    reviewer = ReviewerAgent(_FakeAnthropic("not json"), model="claude-3-7-sonnet-20250219")

    with pytest.raises(ExecutionError):
        reviewer.review_code(_milestone(), "diff --git a/x b/x")


def test_review_code_raises_on_unexpected_status():
    reviewer = ReviewerAgent(
        _FakeAnthropic('{"status": "MAYBE", "feedback": "??"}'), model="claude-3-7-sonnet-20250219"
    )

    with pytest.raises(ExecutionError):
        reviewer.review_code(_milestone(), "diff --git a/x b/x")
