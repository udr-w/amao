from __future__ import annotations

import json
import re

import anthropic
from openai import OpenAI

from amao.exceptions import ExecutionError, PlanningError
from amao.git_helper import GitHelper
from amao.models import ExecutionResult, Milestone, ReviewResult
from amao.rate_limiter import with_retry_and_backoff

_CODE_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Pull content out of a ```lang\\n...\\n``` fence if present, else return as-is.

    Robust against models wrapping their answer in markdown even when told
    not to -- avoids the brittleness of naive `.split("```")` indexing. Only
    strips wrapping blank *lines*, not all whitespace: a blanket `.strip()`
    would silently drop a diff's required trailing newline and corrupt the
    patch (git apply then fails with "corrupt patch").
    """
    match = _CODE_FENCE_RE.search(text)
    content = match.group(1) if match else text
    content = content.strip("\n")
    if content and not content.endswith("\n"):
        content += "\n"
    return content


_PLAN_PROJECT_SYSTEM_PROMPT = (
    "You are an elite Software Architect. Break down project goals into discrete, "
    "sequential coding milestones, each actionable for an automated code generator. "
    'Respond STRICTLY with JSON of the shape: {"milestones": '
    '[{"title": "Milestone title", "description": "Detailed specs"}, ...]}'
)

_GENERATE_TASK_PROMPT_SYSTEM_PROMPT = (
    "You are directing a local automated coding agent. Given a milestone title, its "
    "requirements, and optional prior review feedback, output ONLY precise, "
    "step-by-step instructions for completing that milestone -- no other commentary."
)


class PlannerAgent:
    """Static instructions live in a `system` message, separate from the per-call
    milestone/goal data. OpenAI's automatic prompt caching keys off an identical
    prefix, so this is what lets `generate_task_prompt` -- called once per
    milestone attempt, i.e. repeatedly within a run -- actually get cached once
    the prompt is long enough. `prompt_cache_key` further improves cache-hit
    routing per call type.
    """

    def __init__(self, client: OpenAI, model: str) -> None:
        self.client = client
        self.model = model

    @with_retry_and_backoff()
    def plan_project(self, project_goal: str, max_milestones: int) -> list[dict[str, str]]:
        user_prompt = (
            f"Project Goal: {project_goal}\nProduce no more than {max_milestones} milestones."
        )
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PLAN_PROJECT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            prompt_cache_key="amao-plan-project",
        )
        content = response.choices[0].message.content or ""
        try:
            data = json.loads(content)
            milestones = data["milestones"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise PlanningError(f"Planner returned an unparseable milestone plan: {e}") from e

        if not isinstance(milestones, list) or not all(
            isinstance(m, dict) and "title" in m and "description" in m for m in milestones
        ):
            raise PlanningError("Planner response did not match the expected milestone schema")
        return milestones

    @with_retry_and_backoff()
    def generate_task_prompt(self, milestone: Milestone, feedback: str = "") -> str:
        user_prompt = f"Milestone Title: {milestone.title}\nRequirements: {milestone.description}"
        if feedback:
            user_prompt += f"\nPrevious Review Feedback (MUST FIX): {feedback}"
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _GENERATE_TASK_PROMPT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            prompt_cache_key="amao-generate-task-prompt",
        )
        content = response.choices[0].message.content
        if not content:
            raise PlanningError("Planner returned an empty task prompt")
        return content.strip()


_EXECUTOR_SYSTEM_PROMPT = (
    "You are a local coding engine operating inside an existing git repository. "
    "Make the requested changes and respond ONLY with a valid unified diff in git diff "
    "format representing the file changes needed.\n"
    "Rules:\n"
    "- Every file path MUST use the standard 'a/<relative-path>' and 'b/<relative-path>' "
    "prefixes, with paths relative to the repository root and no leading '/' or '..' segments.\n"
    "- Do not create symlinks and do not include binary content.\n"
    "- Do not wrap the diff in markdown code fences and do not add any explanation."
)


class LocalExecutorAgent:
    """Turns a task prompt into a unified diff and applies it via GitHelper.

    GitHelper.apply_diff is what actually enforces path/symlink/binary
    safety -- this class cannot bypass that by writing files directly.
    """

    def __init__(
        self,
        git: GitHelper,
        client: OpenAI,
        model: str,
        max_diff_chars: int,
    ) -> None:
        self.git = git
        self.client = client
        self.model = model
        self.max_diff_chars = max_diff_chars

    def execute_prompt(self, prompt: str) -> ExecutionResult:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _EXECUTOR_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            prompt_cache_key="amao-execute-prompt",
        )
        content = response.choices[0].message.content
        if not content:
            raise ExecutionError("Local executor returned an empty response")

        diff_text = _strip_code_fence(content)
        self.git.apply_diff(diff_text, max_diff_chars=self.max_diff_chars)
        return ExecutionResult(status="SUCCESS", diff=diff_text)


_REVIEWER_SYSTEM_PROMPT = (
    "You are a Principal Code Reviewer. Analyze the given Git diff against the stated "
    "milestone requirements. Respond STRICTLY in JSON:\n"
    '{"status": "APPROVED" | "REJECTED", "feedback": "Concise, actionable feedback if '
    'rejected, or confirmation if approved."}'
)


class ReviewerAgent:
    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self.client = client
        self.model = model

    @with_retry_and_backoff()
    def review_code(self, milestone: Milestone, git_diff: str) -> ReviewResult:
        if not git_diff.strip():
            return ReviewResult(status="REJECTED", feedback="No changes were made in git diff.")

        user_prompt = (
            f"Milestone: {milestone.title}\n"
            f"Expected: {milestone.description}\n\n"
            f"Git Diff:\n```diff\n{git_diff}\n```"
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            # Anthropic requires >=1024 tokens (>=4096 on some newer model
            # generations) per cache breakpoint before a block actually gets
            # cached -- below that it just isn't cached, no error. This system
            # prompt is short today, but marking it correctly now means caching
            # engages automatically, with no further code change, the moment
            # it's grown (e.g. a fuller review rubric) past that threshold.
            system=[
                {
                    "type": "text",
                    "text": _REVIEWER_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        content_block = response.content[0]
        if not isinstance(content_block, anthropic.types.TextBlock):
            raise ExecutionError(
                f"Reviewer returned an unexpected content block type: "
                f"{type(content_block).__name__}"
            )
        payload = _strip_code_fence(content_block.text)
        try:
            data = json.loads(payload)
            status = data["status"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ExecutionError(f"Reviewer returned an unparseable response: {e}") from e

        if status not in ("APPROVED", "REJECTED"):
            raise ExecutionError(f"Reviewer returned an unexpected status: {status!r}")
        return ReviewResult(status=status, feedback=data.get("feedback", ""))
