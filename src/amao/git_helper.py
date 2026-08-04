"""Git subprocess wrapper.

`apply_diff` is the single place that touches the filesystem on behalf of
an LLM-authored change, so all diff-safety validation lives here rather
than in the caller -- callers cannot bypass it by talking to git directly.
"""

from __future__ import annotations

import os
import re
import subprocess  # noqa: S404 -- always invoked with list args, no shell=True
from pathlib import Path

from amao.exceptions import DiffApplyError, UnsafeDiffError

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_OLD_PATH_RE = re.compile(r"^--- (?:a/(.+)|/dev/null)$")
_NEW_PATH_RE = re.compile(r"^\+\+\+ (?:b/(.+)|/dev/null)$")
_RENAME_FROM_RE = re.compile(r"^rename from (.+)$")
_RENAME_TO_RE = re.compile(r"^rename to (.+)$")
_COPY_FROM_RE = re.compile(r"^copy from (.+)$")
_COPY_TO_RE = re.compile(r"^copy to (.+)$")
_MODE_RE = re.compile(r"^(?:old mode|new mode|new file mode|deleted file mode) (\d+)$")
_SYMLINK_MODE = "120000"
_BINARY_MARKERS = ("GIT binary patch", "Binary files ")

_SINGLE_PATH_PATTERNS = (_RENAME_FROM_RE, _RENAME_TO_RE, _COPY_FROM_RE, _COPY_TO_RE)


def _validate_path(path: str, repo_root: Path) -> None:
    if path == "/dev/null":
        return
    if path.startswith("/"):
        raise UnsafeDiffError(f"Absolute path not allowed in diff: {path}")
    if any(part == ".." for part in Path(path).parts):
        raise UnsafeDiffError(f"Path traversal segment not allowed in diff: {path}")
    resolved = (repo_root / path).resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise UnsafeDiffError(f"Diff path escapes target directory: {path}")


def _validate_diff(diff_text: str, repo_dir: str) -> None:
    if not diff_text.strip():
        raise UnsafeDiffError("Diff is empty")

    for marker in _BINARY_MARKERS:
        if marker in diff_text:
            raise UnsafeDiffError("Binary content is not allowed in generated diffs")

    repo_root = Path(repo_dir).resolve()

    for line in diff_text.splitlines():
        mode_match = _MODE_RE.match(line)
        if mode_match and mode_match.group(1) == _SYMLINK_MODE:
            raise UnsafeDiffError("Symlink modes are not allowed in generated diffs")

        diff_git_match = _DIFF_GIT_RE.match(line)
        if diff_git_match:
            _validate_path(diff_git_match.group(1), repo_root)
            _validate_path(diff_git_match.group(2), repo_root)
            continue

        for pattern in _SINGLE_PATH_PATTERNS:
            m = pattern.match(line)
            if m:
                _validate_path(m.group(1), repo_root)

        old_match = _OLD_PATH_RE.match(line)
        if old_match and old_match.group(1) is not None:
            _validate_path(old_match.group(1), repo_root)

        new_match = _NEW_PATH_RE.match(line)
        if new_match and new_match.group(1) is not None:
            _validate_path(new_match.group(1), repo_root)


class GitHelper:
    def __init__(self, repo_dir: str) -> None:
        self.repo_dir = repo_dir

    def _run(self, cmd: list[str], timeout: float = 30) -> str:
        res = subprocess.run(  # noqa: S603
            cmd, cwd=self.repo_dir, capture_output=True, text=True, check=True, timeout=timeout
        )
        return res.stdout.strip()

    def init_repo(self) -> None:
        if not os.path.exists(os.path.join(self.repo_dir, ".git")):
            self._run(["git", "init"])
            self._run(["git", "config", "user.name", "OrchestratorAgent"])
            self._run(["git", "config", "user.email", "agent@orchestrator.local"])
            readme = os.path.join(self.repo_dir, "README.md")
            if not os.path.exists(readme):
                with open(readme, "w", encoding="utf-8") as f:
                    f.write("# Automated Agent Project\n")
            self._run(["git", "add", "."])
            self._run(["git", "commit", "-m", "Initial commit"])

    def get_diff(self) -> str:
        self._run(["git", "add", "-N", "."])  # track untracked files for diff
        return self._run(["git", "diff", "HEAD"])

    def commit_changes(self, message: str) -> None:
        self._run(["git", "add", "."])
        self._run(["git", "commit", "-m", message])

    def apply_diff(self, diff_text: str, max_diff_chars: int = 100_000) -> None:
        """Validate and apply a unified diff produced by the local executor.

        Defense in depth: we reject unsafe paths/symlinks/binary content
        ourselves *and* rely on git apply's own path-escape protections
        (never pass --unsafe-paths). `--recount` tolerates the slightly-off
        hunk line counts LLM-generated diffs commonly produce.
        """
        if len(diff_text) > max_diff_chars:
            raise UnsafeDiffError(
                f"Diff too large ({len(diff_text)} chars, limit is {max_diff_chars})"
            )
        _validate_diff(diff_text, self.repo_dir)

        base_cmd = ["git", "apply", "-p1", "--recount", "--whitespace=nowarn"]
        try:
            check = subprocess.run(  # noqa: S603
                [*base_cmd[:2], "--check", *base_cmd[2:]],
                cwd=self.repo_dir,
                input=diff_text,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if check.returncode != 0:
                raise DiffApplyError(check.stderr.strip() or "git apply --check failed")

            apply = subprocess.run(  # noqa: S603
                base_cmd,
                cwd=self.repo_dir,
                input=diff_text,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if apply.returncode != 0:
                raise DiffApplyError(apply.stderr.strip() or "git apply failed")
        except subprocess.TimeoutExpired as e:
            raise DiffApplyError(f"git apply timed out: {e}") from e
