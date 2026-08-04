from __future__ import annotations

import logging
import os

from amao.agents import LocalExecutorAgent, PlannerAgent, ReviewerAgent
from amao.config import config
from amao.exceptions import RecoverableExecutionError
from amao.git_helper import GitHelper
from amao.llm import LLMBackend, build_backend
from amao.models import Milestone, MilestoneStatus
from amao.notifier import Notifier
from amao.state_manager import StateManager

logger = logging.getLogger(__name__)

_ERROR_FEEDBACK_CHARS = 4000  # cap what we feed back into the next planner prompt


class Orchestrator:
    """Main pipeline loop: plan once, then execute/review/commit each milestone.

    Only executor-diff content failures (UnsafeDiffError, DiffApplyError,
    ExecutionError -- all RecoverableExecutionError) count against a
    milestone's attempt budget and get fed back to the planner as feedback.
    Everything else (planner failures, git/DB errors, exhausted rate-limit
    retries) halts the run and notifies a human -- a deliberate fail-closed
    default rather than silently retrying infra problems.
    """

    def __init__(
        self,
        project_dir: str,
        project_goal: str,
        planner: PlannerAgent | None = None,
        executor: LocalExecutorAgent | None = None,
        reviewer: ReviewerAgent | None = None,
        state: StateManager | None = None,
        notifier: Notifier | None = None,
        git: GitHelper | None = None,
    ) -> None:
        config.validate()
        if len(project_goal) > config.MAX_GOAL_CHARS:
            raise ValueError(
                f"project_goal is {len(project_goal)} chars, exceeding the "
                f"configured limit of {config.MAX_GOAL_CHARS}"
            )

        self.project_dir = os.path.abspath(project_dir)
        self.project_goal = project_goal
        os.makedirs(self.project_dir, exist_ok=True)

        self.state = state or StateManager(os.path.join(self.project_dir, config.DB_FILENAME))
        self.notifier = notifier or Notifier(config.WEBHOOK_URL)
        self.git = git or GitHelper(self.project_dir)

        api_keys = config.api_keys()

        def backend_for(role_provider: str, role_model: str) -> LLMBackend:
            return build_backend(
                role_provider,
                role_model,
                api_keys=api_keys,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
            )

        self.planner = planner or PlannerAgent(
            backend_for(config.PLANNER_PROVIDER, config.PLANNER_MODEL)
        )
        self.executor = executor or LocalExecutorAgent(
            self.git,
            backend_for(config.EXECUTOR_PROVIDER, config.EXECUTOR_MODEL),
            max_diff_chars=config.MAX_DIFF_CHARS,
        )
        self.reviewer = reviewer or ReviewerAgent(
            backend_for(config.REVIEWER_PROVIDER, config.REVIEWER_MODEL)
        )

    def run(self) -> None:
        logger.info("Starting Agent Orchestrator Loop...")
        self.git.init_repo()

        if self.state.count_milestones() == 0:
            logger.info("Generating milestone breakdown from Architect/Planner...")
            try:
                milestones = self.planner.plan_project(self.project_goal, config.MAX_MILESTONES)
            except Exception as e:
                # Not just PlanningError: an exhausted rate-limit retry, an auth
                # error, etc. must halt gracefully too, not crash the process --
                # there's no milestone yet to attach the failure to.
                self.notifier.notify("Planning Failed", str(e), requires_human=True)
                logger.error("Could not generate an initial plan: %s", e)
                return

            if len(milestones) > config.MAX_MILESTONES:
                msg = (
                    f"Planner returned {len(milestones)} milestones, exceeding the "
                    f"configured limit of {config.MAX_MILESTONES}."
                )
                self.notifier.notify("Plan Exceeds Milestone Limit", msg, requires_human=True)
                logger.error(msg)
                return

            self.state.create_milestones(milestones)
            logger.info("Planned %d milestones.", len(milestones))

        next_task = self.state.get_next_pending_milestone()
        while next_task:
            next_task = self._process_milestone(next_task)

        logger.info("Orchestrator execution complete or paused for human input.")

    def _process_milestone(self, task: Milestone) -> Milestone | None:
        logger.info(
            "Starting Milestone [%d]: %s (Attempt %d/%d)",
            task.id,
            task.title,
            task.attempts + 1,
            config.MAX_REVIEW_ATTEMPTS,
        )

        if task.attempts >= config.MAX_REVIEW_ATTEMPTS:
            msg = (
                f"Milestone '{task.title}' failed after {task.attempts} review attempts. "
                f"Last error: {task.last_error}"
            )
            self.state.update_milestone_status(task.id, MilestoneStatus.HALTED)
            self.notifier.notify("Loop Guard Triggered", msg, requires_human=True)
            logger.error("Pipeline paused. Waiting for human resolution...")
            return None

        self.state.update_milestone_status(task.id, MilestoneStatus.IN_PROGRESS)

        try:
            exec_prompt = self.planner.generate_task_prompt(task, feedback=task.last_error or "")
            self.state.log(task.id, "PROMPT_GENERATED", exec_prompt)

            logger.info("Executing local code modifications...")
            self.executor.execute_prompt(exec_prompt)

            diff = self.git.get_diff()
            review_diff = diff
            if len(review_diff) > config.MAX_DIFF_CHARS:
                review_diff = review_diff[: config.MAX_DIFF_CHARS] + "\n... [diff truncated]"
                logger.warning(
                    "Diff for milestone [%d] exceeds MAX_DIFF_CHARS; truncated before review.",
                    task.id,
                )

            logger.info("Submitting Git diff to Reviewer...")
            review = self.reviewer.review_code(task, review_diff)
            self.state.log(
                task.id, "REVIEW_COMPLETED", {"status": review.status, "feedback": review.feedback}
            )

            if review.approved:
                logger.info("Milestone [%s] APPROVED!", task.title)
                self.git.commit_changes(f"feat: completed {task.title}")
                self.state.update_milestone_status(task.id, MilestoneStatus.COMPLETED)
                self.notifier.notify("Milestone Passed", f"Completed: {task.title}")
            else:
                logger.warning("Milestone [%s] REJECTED. Feedback: %s", task.title, review.feedback)
                self.state.update_milestone_status(
                    task.id,
                    MilestoneStatus.IN_PROGRESS,
                    attempts=task.attempts + 1,
                    last_error=review.feedback,
                )

        except RecoverableExecutionError as e:
            feedback = str(e)[:_ERROR_FEEDBACK_CHARS]
            logger.warning("Recoverable failure on milestone [%s]: %s", task.title, feedback)
            self.state.log(task.id, "RECOVERABLE_ERROR", feedback)
            self.state.update_milestone_status(
                task.id,
                MilestoneStatus.IN_PROGRESS,
                attempts=task.attempts + 1,
                last_error=feedback,
            )
        except Exception as e:
            logger.error("Unexpected error processing milestone [%s]: %s", task.title, e)
            self.state.update_milestone_status(task.id, MilestoneStatus.HALTED, last_error=str(e))
            self.notifier.notify("System Exception", str(e), requires_human=True)
            return None

        return self.state.get_next_pending_milestone()
