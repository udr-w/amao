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


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect a rate-limit/quota error without hard-coupling to a specific SDK.

    Both the openai and anthropic SDKs set a `.status_code` attribute on
    their error objects, so that duck-typed check is the primary, reliable
    signal. Falling back to string matching only covers transports/errors
    that don't expose status_code at all.
    """
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True
    err_str = str(exc).lower()
    return "429" in err_str or "rate limit" in err_str or "quota" in err_str


def with_retry_and_backoff(max_retries: int = 5, initial_delay: float = 2.0) -> Callable[[_F], _F]:
    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
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
