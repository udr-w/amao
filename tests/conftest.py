"""Shared test fixtures.

Sets dummy credentials before `amao` is imported anywhere else in the test
session: `Config()` captures os.environ via default_factory the moment
`amao.config` is first imported, so these must be set before that happens.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")

import pytest  # noqa: E402


@pytest.fixture
def repo_dir(tmp_path):
    return tmp_path
