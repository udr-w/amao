import os
from unittest.mock import MagicMock

import pytest

from amao.config import config
from amao.exceptions import DiffApplyError, PlanningError, TesterInfraError
from amao.llm import AnthropicBackend, OpenAIBackend
from amao.models import Milestone, MilestoneStatus, ReviewResult
from amao.orchestrator import Orchestrator
from amao.testing.models import TestOutcome


def _milestone(id=1, attempts=0, last_error=None, status=MilestoneStatus.PENDING):
    return Milestone(
        id=id, title="T", description="D", status=status, attempts=attempts, last_error=last_error
    )


@pytest.fixture
def deps():
    return {
        "planner": MagicMock(),
        "executor": MagicMock(),
        "reviewer": MagicMock(),
        "tester": MagicMock(),
        "state": MagicMock(),
        "notifier": MagicMock(),
        "git": MagicMock(),
    }


def _make(tmp_path, deps, goal="build x"):
    return Orchestrator(project_dir=str(tmp_path), project_goal=goal, **deps)


def _last_notify_call(deps):
    return deps["notifier"].notify.call_args_list[-1]


def test_oversized_goal_is_rejected_at_construction(tmp_path, deps):
    oversized_goal = "x" * (config.MAX_GOAL_CHARS + 1)

    with pytest.raises(ValueError):
        _make(tmp_path, deps, goal=oversized_goal)


def test_default_construction_wires_the_configured_provider_per_role(tmp_path, deps):
    # Only stub the non-agent collaborators -- let planner/executor/reviewer
    # build their real default backends, and check the wiring glue picked the
    # right provider per role (openai/openai/anthropic, matching config's
    # defaults).
    del deps["planner"], deps["executor"], deps["reviewer"]

    orch = Orchestrator(project_dir=str(tmp_path), project_goal="build x", **deps)

    assert isinstance(orch.planner.backend, OpenAIBackend)
    assert isinstance(orch.executor.backend, OpenAIBackend)
    assert isinstance(orch.reviewer.backend, AnthropicBackend)
    assert orch.planner.backend.model == config.PLANNER_MODEL
    assert orch.executor.backend.model == config.EXECUTOR_MODEL
    assert orch.reviewer.backend.model == config.REVIEWER_MODEL


def test_run_plans_once_when_state_is_empty(tmp_path, deps):
    deps["state"].count_milestones.return_value = 0
    deps["planner"].plan_project.return_value = [{"title": "A", "description": "d"}]
    deps["state"].get_next_pending_milestone.return_value = None

    _make(tmp_path, deps).run()

    deps["planner"].plan_project.assert_called_once()
    deps["state"].create_milestones.assert_called_once_with([{"title": "A", "description": "d"}])


def test_run_skips_planning_when_milestones_already_exist(tmp_path, deps):
    deps["state"].count_milestones.return_value = 3
    deps["state"].get_next_pending_milestone.return_value = None

    _make(tmp_path, deps).run()

    deps["planner"].plan_project.assert_not_called()


def test_planning_failure_notifies_and_stops_without_looping(tmp_path, deps):
    deps["state"].count_milestones.return_value = 0
    deps["planner"].plan_project.side_effect = PlanningError("bad json")

    _make(tmp_path, deps).run()

    deps["state"].create_milestones.assert_not_called()
    deps["state"].get_next_pending_milestone.assert_not_called()
    title, _msg = _last_notify_call(deps)[0]
    assert title == "Planning Failed"


def test_non_planning_exception_during_initial_plan_halts_gracefully(tmp_path, deps):
    # e.g. an auth/network error from the SDK -- not a PlanningError, but must
    # still notify and return cleanly rather than crash the process.
    deps["state"].count_milestones.return_value = 0
    deps["planner"].plan_project.side_effect = RuntimeError("401 Unauthorized")

    _make(tmp_path, deps).run()

    deps["state"].create_milestones.assert_not_called()
    deps["state"].get_next_pending_milestone.assert_not_called()
    title, _msg = _last_notify_call(deps)[0]
    assert title == "Planning Failed"
    assert _last_notify_call(deps)[1]["requires_human"] is True


def test_plan_exceeding_max_milestones_halts_without_creating(tmp_path, deps):
    deps["state"].count_milestones.return_value = 0
    deps["planner"].plan_project.return_value = [
        {"title": f"M{i}", "description": "d"} for i in range(config.MAX_MILESTONES + 1)
    ]

    _make(tmp_path, deps).run()

    deps["state"].create_milestones.assert_not_called()
    title, _msg = _last_notify_call(deps)[0]
    assert title == "Plan Exceeds Milestone Limit"


def test_approved_milestone_commits_and_completes(tmp_path, deps):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["git"].get_diff.return_value = "diff --git a/x b/x"
    deps["reviewer"].review_code.return_value = ReviewResult(status="APPROVED", feedback="great")

    _make(tmp_path, deps).run()

    deps["git"].commit_changes.assert_called_once()
    deps["state"].update_milestone_status.assert_any_call(task.id, MilestoneStatus.COMPLETED)
    title, _msg = _last_notify_call(deps)[0]
    assert title == "Milestone Passed"


def test_rejected_milestone_increments_attempts_without_committing(tmp_path, deps):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["git"].get_diff.return_value = "diff --git a/x b/x"
    deps["reviewer"].review_code.return_value = ReviewResult(
        status="REJECTED", feedback="fix imports"
    )

    _make(tmp_path, deps).run()

    deps["git"].commit_changes.assert_not_called()
    deps["state"].update_milestone_status.assert_any_call(
        task.id, MilestoneStatus.IN_PROGRESS, attempts=1, last_error="fix imports"
    )


def test_loop_guard_halts_after_max_attempts_without_executing(tmp_path, deps):
    task = _milestone(attempts=config.MAX_REVIEW_ATTEMPTS, last_error="still broken")
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task]

    _make(tmp_path, deps).run()

    deps["state"].update_milestone_status.assert_any_call(task.id, MilestoneStatus.HALTED)
    deps["planner"].generate_task_prompt.assert_not_called()
    title, _msg = _last_notify_call(deps)[0]
    assert title == "Loop Guard Triggered"
    assert _last_notify_call(deps)[1]["requires_human"] is True


def test_recoverable_diff_error_increments_attempts_without_halting(tmp_path, deps):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["executor"].execute_prompt.side_effect = DiffApplyError("bad hunk")

    _make(tmp_path, deps).run()

    deps["state"].update_milestone_status.assert_any_call(
        task.id, MilestoneStatus.IN_PROGRESS, attempts=1, last_error="bad hunk"
    )
    deps["notifier"].notify.assert_not_called()
    deps["git"].commit_changes.assert_not_called()


def test_unexpected_error_halts_and_notifies_human(tmp_path, deps):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task]
    deps["planner"].generate_task_prompt.side_effect = RuntimeError("boom")

    _make(tmp_path, deps).run()

    deps["state"].update_milestone_status.assert_any_call(
        task.id, MilestoneStatus.HALTED, last_error="boom"
    )
    title, _msg = _last_notify_call(deps)[0]
    assert title == "System Exception"
    assert _last_notify_call(deps)[1]["requires_human"] is True


def test_oversized_diff_is_truncated_before_review(tmp_path, deps):
    # Config is frozen (by design, to prevent accidental mutation), so exceed the
    # real default limit rather than trying to monkeypatch an immutable instance.
    task = _milestone()
    oversized = "x" * (config.MAX_DIFF_CHARS + 1000)
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["git"].get_diff.return_value = oversized
    deps["reviewer"].review_code.return_value = ReviewResult(status="APPROVED", feedback="ok")

    _make(tmp_path, deps).run()

    reviewed_diff = deps["reviewer"].review_code.call_args[0][1]
    assert len(reviewed_diff) < len(oversized)
    assert reviewed_diff.endswith("[diff truncated]")


@pytest.fixture
def tester_enabled():
    # Config is frozen; ENABLE_TESTER defaults to false, so flip it directly
    # for the duration of a test rather than trying to construct a second
    # Config instance -- Orchestrator always reads the module-level singleton.
    object.__setattr__(config, "ENABLE_TESTER", True)
    yield
    object.__setattr__(config, "ENABLE_TESTER", False)


def test_tester_disabled_by_default_never_invoked(tmp_path, deps):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["git"].get_diff.return_value = "diff --git a/x b/x"
    deps["reviewer"].review_code.return_value = ReviewResult(status="APPROVED", feedback="ok")

    _make(tmp_path, deps).run()

    deps["tester"].test_project.assert_not_called()


def test_passing_tests_attach_evidence_to_the_reviewer_call(tmp_path, deps, tester_enabled):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["git"].get_diff.return_value = "diff --git a/x b/x"
    deps["tester"].test_project.return_value = TestOutcome(
        ran=True, passed=True, summary="pytest: PASSED", output="3 passed"
    )
    deps["reviewer"].review_code.return_value = ReviewResult(status="APPROVED", feedback="ok")

    _make(tmp_path, deps).run()

    deps["tester"].test_project.assert_called_once_with(os.path.abspath(str(tmp_path)))
    evidence = deps["reviewer"].review_code.call_args.kwargs["test_evidence"]
    assert "pytest: PASSED" in evidence
    assert "3 passed" in evidence


def test_no_applicable_tests_falls_through_to_reviewer_with_no_evidence(
    tmp_path, deps, tester_enabled
):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["git"].get_diff.return_value = "diff --git a/x b/x"
    deps["tester"].test_project.return_value = TestOutcome(
        ran=False, passed=True, summary="No applicable test strategy was detected.", output=""
    )
    deps["reviewer"].review_code.return_value = ReviewResult(status="APPROVED", feedback="ok")

    _make(tmp_path, deps).run()

    assert deps["reviewer"].review_code.call_args.kwargs["test_evidence"] is None


def test_failing_tests_short_circuit_to_rejected_without_calling_reviewer(
    tmp_path, deps, tester_enabled
):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task, None]
    deps["git"].get_diff.return_value = "diff --git a/x b/x"
    deps["tester"].test_project.return_value = TestOutcome(
        ran=True, passed=False, summary="pytest: FAILED (exit 1)", output="AssertionError: boom"
    )

    _make(tmp_path, deps).run()

    deps["reviewer"].review_code.assert_not_called()
    deps["git"].commit_changes.assert_not_called()
    deps["state"].update_milestone_status.assert_any_call(
        task.id,
        MilestoneStatus.IN_PROGRESS,
        attempts=1,
        last_error=("Automated tests failed (pytest: FAILED (exit 1)):\nAssertionError: boom"),
    )


def test_tester_infra_error_halts_and_notifies_like_other_infra_failures(
    tmp_path, deps, tester_enabled
):
    task = _milestone()
    deps["state"].count_milestones.return_value = 1
    deps["state"].get_next_pending_milestone.side_effect = [task]
    deps["git"].get_diff.return_value = "diff --git a/x b/x"
    deps["tester"].test_project.side_effect = TesterInfraError("docker is not installed")

    _make(tmp_path, deps).run()

    deps["reviewer"].review_code.assert_not_called()
    deps["state"].update_milestone_status.assert_any_call(
        task.id, MilestoneStatus.HALTED, last_error="docker is not installed"
    )
    title, _msg = _last_notify_call(deps)[0]
    assert title == "System Exception"
    assert _last_notify_call(deps)[1]["requires_human"] is True
