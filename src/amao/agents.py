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


class PlannerAgent:
    def __init__(self, client: OpenAI, model: str) -> None:
        self.client = client
        self.model = model

    @with_retry_and_backoff()
    def plan_project(self, project_goal: str, max_milestones: int) -> list[dict[str, str]]:
        prompt = f"""
        You are an elite Software Architect. Break down the following project goal into discrete,
        sequential coding milestones. Each milestone must be actionable for an automated code
        generator. Produce no more than {max_milestones} milestones.

        Project Goal: {project_goal}

        Respond STRICTLY with JSON of the shape:
        {{"milestones": [{{"title": "Milestone 1 title", "description": "Detailed specs"}}, ...]}}
        """
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
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
        prompt = f"""
        You are directing a local automated coding agent.
        Create precise, step-by-step instructions for completing this milestone.

        Milestone Title: {milestone.title}
        Requirements: {milestone.description}
        {"Previous Review Feedback (MUST FIX): " + feedback if feedback else ""}

        Output ONLY the prompt to send to the local coder.
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
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
        )
        content = response.choices[0].message.content
        if not content:
            raise ExecutionError("Local executor returned an empty response")

        diff_text = _strip_code_fence(content)
        self.git.apply_diff(diff_text, max_diff_chars=self.max_diff_chars)
        return ExecutionResult(status="SUCCESS", diff=diff_text)


class ReviewerAgent:
    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self.client = client
        self.model = model

    @with_retry_and_backoff()
    def review_code(self, milestone: Milestone, git_diff: str) -> ReviewResult:
        if not git_diff.strip():
            return ReviewResult(status="REJECTED", feedback="No changes were made in git diff.")

        prompt = f"""
        You are a Principal Code Reviewer. Analyze the following Git Diff against the milestone
        requirements.

        Milestone: {milestone.title}
        Expected: {milestone.description}

        Git Diff:
        ```diff
        {git_diff}
        ```

        Respond STRICTLY in JSON:
        {{
            "status": "APPROVED" | "REJECTED",
            "feedback": "Concise, actionable feedback if rejected, or confirmation if approved."
        }}
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
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
