import pytest

from amao.config import Config
from amao.exceptions import ConfigError


def test_validate_passes_with_required_keys():
    Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="b").validate()


def test_validate_raises_when_openai_key_missing():
    with pytest.raises(ConfigError):
        Config(OPENAI_API_KEY="", ANTHROPIC_API_KEY="b").validate()


def test_validate_raises_when_anthropic_key_missing():
    with pytest.raises(ConfigError):
        Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="").validate()


def test_validate_rejects_non_https_webhook():
    cfg = Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="b", WEBHOOK_URL="http://example.com")
    with pytest.raises(ConfigError):
        cfg.validate()


def test_validate_accepts_https_webhook():
    cfg = Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="b", WEBHOOK_URL="https://example.com")
    cfg.validate()


def test_default_models_match_default_providers():
    cfg = Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="b")

    assert cfg.PLANNER_PROVIDER == "openai"
    assert cfg.EXECUTOR_PROVIDER == "openai"
    assert cfg.REVIEWER_PROVIDER == "anthropic"
    assert cfg.PLANNER_MODEL == "gpt-4o"
    assert cfg.EXECUTOR_MODEL == "gpt-4o"
    assert cfg.REVIEWER_MODEL == "claude-3-7-sonnet-20250219"


def test_model_defaults_follow_a_rewired_provider():
    # Rewire the reviewer to OpenAI without specifying REVIEWER_MODEL: it
    # should default to OpenAI's default model, not Anthropic's.
    cfg = Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="b", REVIEWER_PROVIDER="openai")

    assert cfg.REVIEWER_MODEL == "gpt-4o"


def test_explicit_model_override_is_preserved():
    cfg = Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="b", PLANNER_MODEL="gpt-4o-mini")

    assert cfg.PLANNER_MODEL == "gpt-4o-mini"


def test_validate_rejects_unknown_provider():
    cfg = Config(OPENAI_API_KEY="a", ANTHROPIC_API_KEY="b", PLANNER_PROVIDER="cohere")

    with pytest.raises(ConfigError):
        cfg.validate()


def test_validate_only_requires_the_key_for_providers_actually_used():
    # Every role rewired to Anthropic: no OpenAI key needed at all.
    cfg = Config(
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="b",
        PLANNER_PROVIDER="anthropic",
        EXECUTOR_PROVIDER="anthropic",
        REVIEWER_PROVIDER="anthropic",
    )

    cfg.validate()  # must not raise despite a blank OPENAI_API_KEY


def test_validate_still_requires_openai_key_if_any_role_uses_it():
    cfg = Config(
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="b",
        PLANNER_PROVIDER="anthropic",
        EXECUTOR_PROVIDER="openai",  # still needs OpenAI
        REVIEWER_PROVIDER="anthropic",
    )

    with pytest.raises(ConfigError):
        cfg.validate()


@pytest.mark.parametrize(
    ("provider", "expected_model"),
    [
        ("deepseek", "deepseek-v4-flash"),
        ("moonshot", "kimi-k3"),
        ("xai", "grok-4.3"),
        ("gemini", "gemini-3.5-flash"),
    ],
)
def test_rewiring_to_a_new_provider_resolves_its_default_model(provider, expected_model):
    cfg = Config(REVIEWER_PROVIDER=provider, **{f"{provider.upper()}_API_KEY": "k"})

    assert cfg.REVIEWER_MODEL == expected_model


def test_validate_only_requires_the_new_providers_key_when_rewired():
    cfg = Config(
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="",
        DEEPSEEK_API_KEY="d",
        PLANNER_PROVIDER="deepseek",
        EXECUTOR_PROVIDER="deepseek",
        REVIEWER_PROVIDER="deepseek",
    )

    cfg.validate()  # must not raise: neither OpenAI nor Anthropic is in use


def test_validate_reports_the_new_providers_missing_key():
    cfg = Config(REVIEWER_PROVIDER="moonshot", MOONSHOT_API_KEY="")

    with pytest.raises(ConfigError, match="MOONSHOT_API_KEY"):
        cfg.validate()


def test_api_keys_maps_every_provider_to_its_configured_key():
    cfg = Config(
        OPENAI_API_KEY="o",
        ANTHROPIC_API_KEY="a",
        DEEPSEEK_API_KEY="d",
        MOONSHOT_API_KEY="m",
        XAI_API_KEY="x",
        GEMINI_API_KEY="g",
    )

    assert cfg.api_keys() == {
        "openai": "o",
        "anthropic": "a",
        "deepseek": "d",
        "moonshot": "m",
        "xai": "x",
        "gemini": "g",
    }
