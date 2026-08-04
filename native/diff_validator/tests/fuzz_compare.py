#!/usr/bin/env python3
"""Differential fuzz test: does the C++ diff_validator agree with amao's
REAL Python _validate_diff (git_helper.py) on whether a diff is safe?

This is the evidence behind NATIVE_EXTENSIONS.md's case-study conclusion --
run it, don't just read the C++ and assume it's equivalent. Only compares
"did both sides agree it's unsafe" (a boolean), never exact error message
text, since message wording was never part of the security boundary.

Usage: python3 native/diff_validator/tests/fuzz_compare.py
"""

from __future__ import annotations

import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

import diff_validator  # type: ignore[import-not-found]

from amao.git_helper import _validate_diff  # noqa: E402


def _make_diff(path_a: str, path_b: str, extra_lines: list[str] | None = None) -> str:
    lines = [
        f"diff --git a/{path_a} b/{path_b}",
        f"--- a/{path_a}",
        f"+++ b/{path_b}",
        "@@ -1 +1 @@",
        "-old line",
        "+new line",
    ]
    if extra_lines:
        lines = lines[:1] + extra_lines + lines[1:]
    return "\n".join(lines) + "\n"


def _random_segment(rng: random.Random) -> str:
    choices = [
        "src",
        "file.py",
        "sub",
        "dir",
        "..",
        ".",
        "a b",
        "üñïçödé.txt",
        "file with spaces.py",
        "",
        "...",
        "....",
        ".git",
    ]
    return rng.choice(choices)


def _random_path(rng: random.Random) -> str:
    depth = rng.randint(1, 4)
    segments = [_random_segment(rng) for _ in range(depth)]
    path = "/".join(s for s in segments if s)
    if rng.random() < 0.1:
        path = "/" + path  # absolute path attack
    if not path:
        path = "file.txt"
    return path


def _check_real_symlink_cases(repo_dir: str) -> list[str]:
    """Targeted (non-random) cases exercising REAL filesystem symlinks --
    the one area where Python's Path.resolve(strict=False) and C++'s
    std::filesystem::weakly_canonical are most likely to genuinely diverge,
    since a purely lexical/nonexistent-path fuzz never touches this at all.
    """
    disagreements = []

    def check(name: str, diff_text: str) -> None:
        python_unsafe = False
        try:
            _validate_diff(diff_text, repo_dir)
        except Exception:  # noqa: BLE001
            python_unsafe = True
        cpp_reason = diff_validator.validate_diff(diff_text, repo_dir)
        cpp_unsafe = cpp_reason != ""
        if python_unsafe != cpp_unsafe:
            disagreements.append(
                f"[symlink case: {name}] python_unsafe={python_unsafe} "
                f"cpp_unsafe={cpp_unsafe} (cpp_reason={cpp_reason!r})"
            )

    # A dangling symlink (target doesn't exist) whose text points outside
    # repo_dir. KNOWN DIVERGENCE (see NATIVE_EXTENSIONS.md): Python's
    # resolve() substitutes a symlink's target text even if that target
    # doesn't itself exist, so it correctly follows this to outside
    # repo_dir. weakly_canonical's "does this prefix exist" check follows
    # symlinks to determine existence too, so a dangling symlink reads as
    # "doesn't exist" and its text is never substituted -- the path is
    # instead appended lexically, which looks like it's still under
    # repo_dir even though the real symlink, if it pointed somewhere real,
    # would escape.
    dangling = os.path.join(repo_dir, "dangling")
    if not os.path.islink(dangling):
        os.symlink("/nonexistent/target/xyz", dangling)
    check(
        "dangling symlink pointing outside repo",
        _make_diff("dangling/f.txt", "dangling/f.txt"),
    )

    # A real, resolvable symlink pointing outside repo_dir -- both sides
    # agree this is unsafe (confirmed above; kept here as a regression check).
    outside_dir = tempfile.mkdtemp()
    real_escape = os.path.join(repo_dir, "real_escape_link")
    if not os.path.islink(real_escape):
        os.symlink(outside_dir, real_escape)
    check(
        "real symlink pointing outside repo (resolvable)",
        _make_diff("real_escape_link/f.txt", "real_escape_link/f.txt"),
    )

    # A symlink pointing to a real location INSIDE repo_dir -- both sides
    # should agree this is safe.
    inside_target = os.path.join(repo_dir, "real_subdir")
    os.makedirs(inside_target, exist_ok=True)
    internal_link = os.path.join(repo_dir, "internal_link")
    if not os.path.islink(internal_link):
        os.symlink(inside_target, internal_link)
    check(
        "symlink pointing inside repo (safe)",
        _make_diff("internal_link/f.txt", "internal_link/f.txt"),
    )

    return disagreements


def run_fuzz(iterations: int = 2000, seed: int = 1337) -> tuple[int, list[str]]:
    rng = random.Random(seed)
    disagreements: list[str] = []

    with tempfile.TemporaryDirectory() as repo_dir:
        disagreements.extend(_check_real_symlink_cases(repo_dir))

        for i in range(iterations):
            kind = rng.random()
            if kind < 0.4:
                # plain, safe-ish random paths (may still be unsafe -- e.g. traversal)
                path_a = _random_path(rng)
                path_b = _random_path(rng)
                extra = None
            elif kind < 0.55:
                # deliberate path traversal attack
                depth = rng.randint(1, 5)
                path_a = "/".join([".."] * depth + ["etc", "passwd"])
                path_b = path_a
                extra = None
            elif kind < 0.65:
                # deliberate absolute path attack
                path_a = "/etc/passwd"
                path_b = path_a
                extra = None
            elif kind < 0.75:
                # symlink mode attack
                path_a = path_b = "innocuous.txt"
                extra = ["new file mode 120000"]
            elif kind < 0.85:
                # binary marker attack
                path_a = path_b = "innocuous.txt"
                extra = ["GIT binary patch"]
            elif kind < 0.95:
                # /dev/null (valid: new/deleted file), should be safe
                path_a = "/dev/null" if rng.random() < 0.5 else _random_path(rng)
                path_b = "/dev/null" if path_a != "/dev/null" else _random_path(rng)
                extra = None
            else:
                # empty diff
                path_a = path_b = ""
                extra = None

            diff_text = "" if (path_a == "" and path_b == "") else _make_diff(path_a, path_b, extra)

            python_unsafe = False
            try:
                _validate_diff(diff_text, repo_dir)
            except Exception:  # noqa: BLE001 -- comparing raise/no-raise only
                python_unsafe = True

            cpp_reason = diff_validator.validate_diff(diff_text, repo_dir)
            cpp_unsafe = cpp_reason != ""

            if python_unsafe != cpp_unsafe:
                disagreements.append(
                    f"#{i}: diff={diff_text!r} python_unsafe={python_unsafe} "
                    f"cpp_unsafe={cpp_unsafe} (cpp_reason={cpp_reason!r})"
                )

    return iterations + 4, disagreements


if __name__ == "__main__":
    total, disagreements = run_fuzz()
    print(f"Ran {total} synthetic diffs.")
    print(f"Disagreements: {len(disagreements)}")
    for d in disagreements[:20]:
        print(" -", d)
    if len(disagreements) > 20:
        print(f"   ... and {len(disagreements) - 20} more")
    sys.exit(1 if disagreements else 0)
