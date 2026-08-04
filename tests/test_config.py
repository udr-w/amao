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
