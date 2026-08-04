from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

MAX_SLEEP_SECONDS = 60.0


def _is_quota_exhausted_error(exc: Exception) -> bool:
    """A billing/quota error, unlike a transient rate limit, will never
    resolve itself no matter how long you wait -- retrying it is pure
    wasted time. Confirmed against a real OpenAI 429 response body: quota
    exhaustion carries `code: "insufficient_quota"` and the message "You
    exceeded your current quota, please check your plan and billing
    details," distinct from a transient `rate_limit_exceeded`. The openai
    SDK exposes the parsed `code` as an attribute; the string fallback
    covers transports/providers that don't.
    """
    if getattr(exc, "code", None) == "insufficient_quota":
        return True
    err_str = str(exc).lower()
    return "insufficient_quota" in err_str or "exceeded your current quota" in err_str


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect a transient rate-limit error without hard-coupling to a
    specific SDK. Both the openai and anthropic SDKs set a `.status_code`
    attribute on their error objects, so that duck-typed check is the
    primary, reliable signal. Falling back to string matching only covers
    transports/errors that don't expose status_code at all. Deliberately
    does not match on "quota" -- see _is_quota_exhausted_error, checked
    first by the caller, for that distinct and non-retryable case.
    """
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    err_str = str(exc).lower()
    return "429" in err_str or "rate limit" in err_str


def with_retry_and_backoff(max_retries: int = 5, initial_delay: float = 2.0) -> Callable[[_F], _F]:
    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if _is_quota_exhausted_error(e):
                        logger.error(
                            "Quota/billing exhausted in %s -- retrying will not help. "
                            "Check your provider's plan/billing. %s",
                            func.__name__,
                            e,
                        )
                        raise
                    if _is_rate_limit_error(e) and attempt < max_retries:
                        sleep_time = min(
                            delay * (2 ** (attempt - 1)) + random.uniform(0, 1),
                            MAX_SLEEP_SECONDS,
                        )
                        logger.warning(
                            "Rate limit detected in %s. Attempt %d/%d. Sleeping for %.2fs...",
                            func.__name__,
                            attempt,
                            max_retries,
                            sleep_time,
                        )
                        time.sleep(sleep_time)
                    else:
                        logger.error("Execution error in %s: %s", func.__name__, e)
                        raise
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
