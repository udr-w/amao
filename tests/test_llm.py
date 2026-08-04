from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from amao.exceptions import ConfigError, ExecutionError
from amao.llm import PROVIDERS, AnthropicBackend, OpenAIBackend, build_backend


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


def test_openai_backend_omits_cache_key_when_unsupported():
    # OpenAI-compatible third-party providers (DeepSeek, Moonshot, xAI,
    # Gemini) don't implement OpenAI's prompt_cache_key hint -- it must not
    # be sent to them.
    client = _FakeOpenAIClient("ok")
    backend = OpenAIBackend(client, model="deepseek-v4-flash", supports_cache_key=False)

    backend.complete(system="s", user="u", cache_key="some-key")

    from openai import NOT_GIVEN

    assert client.last_kwargs["prompt_cache_key"] is NOT_GIVEN


def test_openai_backend_attaches_images_as_content_parts(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"fake-png-bytes")
    client = _FakeOpenAIClient("ok")
    backend = OpenAIBackend(client, model="gpt-4o")

    backend.complete(system="s", user="describe this", cache_key="k", images=(str(image_path),))

    content = client.last_kwargs["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "describe this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_backend_plain_string_content_when_no_images():
    client = _FakeOpenAIClient("ok")
    backend = OpenAIBackend(client, model="gpt-4o")

    backend.complete(system="s", user="u", cache_key="k")

    assert client.last_kwargs["messages"][1]["content"] == "u"


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


def test_anthropic_backend_attaches_images_as_content_parts(tmp_path):
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"fake-png-bytes")
    client = _FakeAnthropicClient("ok")
    backend = AnthropicBackend(client, model="claude-3-7-sonnet-20250219")

    backend.complete(system="s", user="describe this", cache_key="k", images=(str(image_path),))

    content = client.last_kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "describe this"}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/png"


def test_anthropic_backend_raises_on_unexpected_content_block_type():
    client = _FakeAnthropicClient("ignored")
    client.messages = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(content=[object()]))
    backend = AnthropicBackend(client, model="claude-3-7-sonnet-20250219")

    with pytest.raises(ExecutionError):
        backend.complete(system="s", user="u", cache_key="k")


_ALL_KEYS = {
    "openai": "openai-key",
    "anthropic": "anthropic-key",
    "deepseek": "deepseek-key",
    "moonshot": "moonshot-key",
    "xai": "xai-key",
    "gemini": "gemini-key",
}


def test_build_backend_openai_supports_cache_key():
    backend = build_backend("openai", "gpt-4o", api_keys=_ALL_KEYS, timeout=10)

    assert isinstance(backend, OpenAIBackend)
    assert backend.model == "gpt-4o"
    assert backend.supports_cache_key is True
    assert backend.client.api_key == "openai-key"


def test_build_backend_anthropic():
    backend = build_backend(
        "anthropic", "claude-3-7-sonnet-20250219", api_keys=_ALL_KEYS, timeout=10
    )

    assert isinstance(backend, AnthropicBackend)
    assert backend.model == "claude-3-7-sonnet-20250219"
    assert backend.client.api_key == "anthropic-key"


@pytest.mark.parametrize("provider", ["deepseek", "moonshot", "xai", "gemini"])
def test_build_backend_openai_compatible_providers(provider):
    spec = PROVIDERS[provider]
    backend = build_backend(provider, spec.default_model, api_keys=_ALL_KEYS, timeout=10)

    assert isinstance(backend, OpenAIBackend)
    assert backend.model == spec.default_model
    assert backend.supports_cache_key is False  # not the real OpenAI endpoint
    assert str(backend.client.base_url).rstrip("/") == spec.base_url.rstrip("/")
    assert backend.client.api_key == _ALL_KEYS[provider]


def test_build_backend_rejects_unknown_provider():
    with pytest.raises(ConfigError):
        build_backend("cohere", "some-model", api_keys=_ALL_KEYS, timeout=10)


def test_build_backend_never_raises_keyerror_for_a_missing_key():
    # api_keys.get(provider, "") must never KeyError even if a provider's key
    # was never set anywhere -- Config.validate() is the real safety net that
    # keeps build_backend() from ever being called this way in production;
    # this just proves *our* dict lookup can't be the failure mode.
    # (The openai SDK itself has, as of 2.x, started eagerly rejecting a
    # blank api_key at client-construction time -- that's a clearer failure
    # than a raw KeyError would be, so this is an accepted, not a regression.)
    with pytest.raises(Exception, match="credentials|api_key"):
        build_backend("deepseek", "deepseek-v4-flash", api_keys={}, timeout=10)
