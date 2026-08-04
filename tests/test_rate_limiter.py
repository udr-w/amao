import pytest

from amao.rate_limiter import MAX_SLEEP_SECONDS, with_retry_and_backoff


class _FakeRateLimitError(Exception):
    def __init__(self, message: str = "rate limited") -> None:
        super().__init__(message)
        self.status_code = 429


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("amao.rate_limiter.time.sleep", lambda s: sleeps.append(s))
    return sleeps


def test_retries_on_status_code_429(_no_real_sleep):
    calls = {"n": 0}

    @with_retry_and_backoff(max_retries=3, initial_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeRateLimitError()
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3
    assert len(_no_real_sleep) == 2


def test_gives_up_after_max_retries(_no_real_sleep):
    @with_retry_and_backoff(max_retries=2, initial_delay=0.01)
    def always_fails():
        raise _FakeRateLimitError()

    with pytest.raises(_FakeRateLimitError):
        always_fails()


def test_string_fallback_detects_quota_message(_no_real_sleep):
    calls = {"n": 0}

    @with_retry_and_backoff(max_retries=3, initial_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("You have exceeded your current quota")
        return "ok"

    assert flaky() == "ok"


def test_non_retriable_error_raises_immediately(_no_real_sleep):
    calls = {"n": 0}

    @with_retry_and_backoff(max_retries=5, initial_delay=0.01)
    def broken():
        calls["n"] += 1
        raise ValueError("invalid milestone description")

    with pytest.raises(ValueError):
        broken()

    assert calls["n"] == 1
    assert _no_real_sleep == []


def test_sleep_time_is_capped(_no_real_sleep, monkeypatch):
    monkeypatch.setattr("amao.rate_limiter.random.uniform", lambda a, b: 0)

    @with_retry_and_backoff(max_retries=6, initial_delay=1000)
    def always_rate_limited():
        raise _FakeRateLimitError()

    with pytest.raises(_FakeRateLimitError):
        always_rate_limited()

    assert all(s <= MAX_SLEEP_SECONDS for s in _no_real_sleep)
