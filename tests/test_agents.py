import pytest

from amao.agents import LocalExecutorAgent, PlannerAgent, ReviewerAgent, _strip_code_fence
from amao.exceptions import ExecutionError, PlanningError
from amao.git_helper import GitHelper
from amao.llm import LLMBackend
from amao.models import Milestone, MilestoneStatus


class _FakeBackend(LLMBackend):
    """Records what each agent asks of the backend, decoupled from any
    specific provider -- agents.py should behave identically regardless of
    which LLMBackend it's given. Provider-specific request/response shapes
    are covered separately in test_llm.py.
    """

    def __init__(self, content):
        self._content = content
        self.last_call = None

    def complete(self, *, system, user, cache_key, json_mode=False, images=()):
        self.last_call = {
            "system": system,
            "user": user,
            "cache_key": cache_key,
            "json_mode": json_mode,
            "images": images,
        }
        return self._content


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
    backend = _FakeBackend('{"milestones": [{"title": "A", "description": "do a"}]}')
    planner = PlannerAgent(backend)

    milestones = planner.plan_project("build a thing", max_milestones=10)

    assert milestones == [{"title": "A", "description": "do a"}]


def test_plan_project_raises_on_malformed_json():
    planner = PlannerAgent(_FakeBackend("not json"))

    with pytest.raises(PlanningError):
        planner.plan_project("build a thing", max_milestones=10)


def test_plan_project_raises_on_missing_milestones_key():
    planner = PlannerAgent(_FakeBackend('{"oops": []}'))

    with pytest.raises(PlanningError):
        planner.plan_project("x", max_milestones=10)


def test_plan_project_raises_on_malformed_milestone_shape():
    planner = PlannerAgent(_FakeBackend('{"milestones": [{"title": "A"}]}'))

    with pytest.raises(PlanningError):
        planner.plan_project("x", max_milestones=10)


def test_plan_project_uses_static_system_prompt_json_mode_and_cache_key():
    backend = _FakeBackend('{"milestones": [{"title": "A", "description": "d"}]}')
    planner = PlannerAgent(backend)

    planner.plan_project("build a thing", max_milestones=10)

    assert "Project Goal" not in backend.last_call["system"]  # static, no per-call data
    assert "build a thing" in backend.last_call["user"]
    assert backend.last_call["json_mode"] is True
    assert backend.last_call["cache_key"] == "amao-plan-project"


def test_generate_task_prompt_returns_text():
    planner = PlannerAgent(_FakeBackend("do step 1"))

    assert planner.generate_task_prompt(_milestone()) == "do step 1"


def test_generate_task_prompt_raises_on_empty_response():
    planner = PlannerAgent(_FakeBackend(None))

    with pytest.raises(PlanningError):
        planner.generate_task_prompt(_milestone())


def test_generate_task_prompt_uses_static_system_prompt_and_cache_key():
    backend = _FakeBackend("do step 1")
    planner = PlannerAgent(backend)

    planner.generate_task_prompt(_milestone())

    assert "Add feature" not in backend.last_call["system"]  # static, no per-call data
    assert "Add feature" in backend.last_call["user"]
    assert backend.last_call["cache_key"] == "amao-generate-task-prompt"


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
    executor = LocalExecutorAgent(git, _FakeBackend(_VALID_NEW_FILE_DIFF), max_diff_chars=10_000)

    result = executor.execute_prompt("add hello.txt")

    assert result.status == "SUCCESS"
    assert (tmp_path / "hello.txt").read_text() == "hi\n"


def test_executor_strips_markdown_fence_around_diff(tmp_path):
    git = GitHelper(str(tmp_path))
    git.init_repo()
    fenced = f"```diff\n{_VALID_NEW_FILE_DIFF}```"
    executor = LocalExecutorAgent(git, _FakeBackend(fenced), max_diff_chars=10_000)

    executor.execute_prompt("add hello.txt")

    assert (tmp_path / "hello.txt").read_text() == "hi\n"


def test_executor_raises_on_empty_response(tmp_path):
    git = GitHelper(str(tmp_path))
    git.init_repo()
    executor = LocalExecutorAgent(git, _FakeBackend(None), max_diff_chars=10_000)

    with pytest.raises(ExecutionError):
        executor.execute_prompt("do something")


def test_executor_uses_cache_key(tmp_path):
    git = GitHelper(str(tmp_path))
    git.init_repo()
    backend = _FakeBackend(_VALID_NEW_FILE_DIFF)
    executor = LocalExecutorAgent(git, backend, max_diff_chars=10_000)

    executor.execute_prompt("add hello.txt")

    assert backend.last_call["cache_key"] == "amao-execute-prompt"
    assert backend.last_call["json_mode"] is False


def test_review_code_returns_result_for_approved():
    reviewer = ReviewerAgent(_FakeBackend('{"status": "APPROVED", "feedback": "looks good"}'))

    result = reviewer.review_code(_milestone(), "diff --git a/x b/x")

    assert result.approved
    assert result.feedback == "looks good"


def test_review_code_uses_static_system_prompt_json_mode_and_cache_key():
    backend = _FakeBackend('{"status": "APPROVED", "feedback": "ok"}')
    reviewer = ReviewerAgent(backend)

    reviewer.review_code(_milestone(), "diff --git a/x b/x")

    # Dynamic per-call data must stay in the user message, never in the cached system block.
    assert "Add feature" not in backend.last_call["system"]
    assert "Add feature" in backend.last_call["user"]
    assert backend.last_call["json_mode"] is True
    assert backend.last_call["cache_key"] == "amao-review-code"
    assert backend.last_call["images"] == ()


def test_review_code_forwards_screenshots_to_the_backend():
    backend = _FakeBackend('{"status": "APPROVED", "feedback": "ok"}')
    reviewer = ReviewerAgent(backend)

    reviewer.review_code(_milestone(), "diff --git a/x b/x", screenshots=("/tmp/shot.png",))

    assert backend.last_call["images"] == ("/tmp/shot.png",)
    assert "screenshot" in backend.last_call["user"].lower()


def test_review_code_rejects_empty_diff_without_calling_llm():
    backend = _FakeBackend("should not be used")
    reviewer = ReviewerAgent(backend)

    result = reviewer.review_code(_milestone(), "   ")

    assert not result.approved
    assert backend.last_call is None


def test_review_code_raises_on_malformed_json():
    reviewer = ReviewerAgent(_FakeBackend("not json"))

    with pytest.raises(ExecutionError):
        reviewer.review_code(_milestone(), "diff --git a/x b/x")


def test_review_code_raises_on_unexpected_status():
    reviewer = ReviewerAgent(_FakeBackend('{"status": "MAYBE", "feedback": "??"}'))

    with pytest.raises(ExecutionError):
        reviewer.review_code(_milestone(), "diff --git a/x b/x")
