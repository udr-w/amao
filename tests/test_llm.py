from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from amao.exceptions import ConfigError, ExecutionError
from amao.llm import AnthropicBackend, OpenAIBackend, build_backend


class _FakeOpenAIClient:
    def __init__(self, content):
        self._content = content
        self.last_kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


class _FakeAnthropicClient:
    """Uses a real anthropic.types.TextBlock so the backend's isinstance check passes."""

    def __init__(self, content):
        self._content = content
        self.last_kwargs = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(content=[TextBlock(type="text", text=self._content)])


def test_openai_backend_sends_system_and_user_messages():
    client = _FakeOpenAIClient("do step 1")
    backend = OpenAIBackend(client, model="gpt-4o")

    result = backend.complete(system="be terse", user="say hi", cache_key="k")

    assert result == "do step 1"
    messages = client.last_kwargs["messages"]
    assert messages == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "say hi"},
    ]
    assert client.last_kwargs["prompt_cache_key"] == "k"
    assert "response_format" not in client.last_kwargs


def test_openai_backend_json_mode_sets_response_format():
    client = _FakeOpenAIClient('{"a": 1}')
    backend = OpenAIBackend(client, model="gpt-4o")

    backend.complete(system="s", user="u", cache_key="k", json_mode=True)

    assert client.last_kwargs["response_format"] == {"type": "json_object"}


def test_openai_backend_returns_empty_string_for_none_content():
    client = _FakeOpenAIClient(None)
    backend = OpenAIBackend(client, model="gpt-4o")

    assert backend.complete(system="s", user="u", cache_key="k") == ""


def test_anthropic_backend_marks_system_as_cacheable_when_cache_key_given():
    client = _FakeAnthropicClient("ok")
    backend = AnthropicBackend(client, model="claude-3-7-sonnet-20250219")

    backend.complete(system="be terse", user="say hi", cache_key="k")

    system = client.last_kwargs["system"]
    assert system == [{"type": "text", "text": "be terse", "cache_control": {"type": "ephemeral"}}]
    assert client.last_kwargs["messages"] == [{"role": "user", "content": "say hi"}]


def test_anthropic_backend_skips_cache_control_without_cache_key():
    client = _FakeAnthropicClient("ok")
    backend = AnthropicBackend(client, model="claude-3-7-sonnet-20250219")

    backend.complete(system="be terse", user="say hi", cache_key="")

    assert client.last_kwargs["system"] == "be terse"


def test_anthropic_backend_ignores_json_mode_flag():
    client = _FakeAnthropicClient('{"a": 1}')
    backend = AnthropicBackend(client, model="claude-3-7-sonnet-20250219")

    result = backend.complete(system="s", user="u", cache_key="k", json_mode=True)

    assert result == '{"a": 1}'  # relies on prompt instructions, not an API param


def test_anthropic_backend_raises_on_unexpected_content_block_type():
    client = _FakeAnthropicClient("ignored")
    client.messages = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(content=[object()]))
    backend = AnthropicBackend(client, model="claude-3-7-sonnet-20250219")

    with pytest.raises(ExecutionError):
        backend.complete(system="s", user="u", cache_key="k")


def test_build_backend_openai():
    backend = build_backend(
        "openai", "gpt-4o", openai_api_key="x", anthropic_api_key="y", timeout=10
    )

    assert isinstance(backend, OpenAIBackend)
    assert backend.model == "gpt-4o"


def test_build_backend_anthropic():
    backend = build_backend(
        "anthropic",
        "claude-3-7-sonnet-20250219",
        openai_api_key="x",
        anthropic_api_key="y",
        timeout=10,
    )

    assert isinstance(backend, AnthropicBackend)
    assert backend.model == "claude-3-7-sonnet-20250219"


def test_build_backend_rejects_unknown_provider():
    with pytest.raises(ConfigError):
        build_backend("cohere", "some-model", openai_api_key="x", anthropic_api_key="y", timeout=10)
