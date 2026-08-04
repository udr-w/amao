import subprocess

import pytest

from amao.exceptions import DiffApplyError, UnsafeDiffError
from amao.git_helper import GitHelper


@pytest.fixture
def repo(tmp_path):
    g = GitHelper(str(tmp_path))
    g.init_repo()
    return g


def _log(tmp_path) -> str:
    result = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    return result.stdout


def test_init_repo_creates_git_dir_and_initial_commit(tmp_path):
    g = GitHelper(str(tmp_path))
    g.init_repo()

    assert (tmp_path / ".git").exists()
    assert "Initial commit" in _log(tmp_path)


def test_init_repo_is_idempotent(repo):
    repo.init_repo()  # second call must not raise


def test_get_diff_empty_when_no_changes(repo):
    assert repo.get_diff() == ""


def test_get_diff_detects_new_untracked_file(repo, tmp_path):
    (tmp_path / "untracked.txt").write_text("data")

    assert "untracked.txt" in repo.get_diff()


def test_commit_changes(repo, tmp_path):
    (tmp_path / "untracked.txt").write_text("data")
    repo.get_diff()

    repo.commit_changes("feat: add file")

    assert "feat: add file" in _log(tmp_path)


_VALID_NEW_FILE_DIFF = (
    "diff --git a/hello.txt b/hello.txt\n"
    "new file mode 100644\n"
    "index 0000000..e69de29\n"
    "--- /dev/null\n"
    "+++ b/hello.txt\n"
    "@@ -0,0 +1 @@\n"
    "+hello world\n"
)


def test_apply_diff_creates_new_file(repo, tmp_path):
    repo.apply_diff(_VALID_NEW_FILE_DIFF)

    assert (tmp_path / "hello.txt").read_text() == "hello world\n"


def test_apply_diff_rejects_absolute_path(repo):
    diff = (
        "diff --git a/evil.txt b/evil.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b//etc/evil.txt\n"
        "@@ -0,0 +1 @@\n"
        "+pwned\n"
    )
    with pytest.raises(UnsafeDiffError):
        repo.apply_diff(diff)


def test_apply_diff_rejects_path_traversal(repo):
    diff = (
        "diff --git a/x b/../../etc/passwd\n"
        "--- a/x\n"
        "+++ b/../../etc/passwd\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    with pytest.raises(UnsafeDiffError):
        repo.apply_diff(diff)


def test_apply_diff_rejects_symlink_mode(repo):
    diff = (
        "diff --git a/link b/link\n"
        "new file mode 120000\n"
        "index 0000000..abc1234\n"
        "--- /dev/null\n"
        "+++ b/link\n"
        "@@ -0,0 +1 @@\n"
        "+../../../etc/passwd\n"
    )
    with pytest.raises(UnsafeDiffError):
        repo.apply_diff(diff)


def test_apply_diff_rejects_binary_content(repo):
    diff = (
        "diff --git a/image.png b/image.png\n"
        "new file mode 100644\n"
        "index 0000000..abc1234\n"
        "GIT binary patch\n"
        "literal 10\n"
    )
    with pytest.raises(UnsafeDiffError):
        repo.apply_diff(diff)


def test_apply_diff_rejects_oversized_diff(repo):
    with pytest.raises(UnsafeDiffError):
        repo.apply_diff("x" * 200, max_diff_chars=100)


def test_apply_diff_rejects_empty_diff(repo):
    with pytest.raises(UnsafeDiffError):
        repo.apply_diff("   \n  ")


def test_apply_diff_raises_diff_apply_error_on_malformed_patch(repo):
    diff = (
        "diff --git a/hello.txt b/hello.txt\n"
        "--- a/hello.txt\n"
        "+++ b/hello.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "-this does not exist\n"
        "+replacement\n"
    )
    with pytest.raises(DiffApplyError):
        repo.apply_diff(diff)


def test_apply_diff_does_not_write_outside_repo_on_rejection(repo, tmp_path):
    diff = (
        "diff --git a/evil.txt b/evil.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b//etc/evil.txt\n"
        "@@ -0,0 +1 @@\n"
        "+pwned\n"
    )
    with pytest.raises(UnsafeDiffError):
        repo.apply_diff(diff)

    assert not (tmp_path / "evil.txt").exists()
