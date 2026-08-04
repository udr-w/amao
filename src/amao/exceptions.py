"""Exception hierarchy used to classify pipeline failures.

RecoverableExecutionError and its subclasses represent failures that are
about the *content* an agent produced (a bad diff, a malformed LLM
response) and are fed back into the review/retry loop like a rejected
review. Anything else propagates to the orchestrator's catch-all, which
halts the milestone and notifies a human -- that fail-closed default is
intentional: infra/config/database errors should never be silently
retried against a milestone's attempt budget.
"""

from __future__ import annotations


class AmaoError(Exception):
    """Base class for all amao-specific errors."""


class ConfigError(AmaoError):
    """Required configuration (e.g. an API key) is missing or invalid."""


class PlanningError(AmaoError):
    """The planner produced an unusable response. Not milestone-scoped, so it
    cannot be retried as a rejected attempt -- it halts the whole run."""


class RecoverableExecutionError(AmaoError):
    """A milestone-level failure that should count as a rejected attempt."""


class UnsafeDiffError(RecoverableExecutionError):
    """A diff from the executor attempted to touch paths outside the repo."""


class DiffApplyError(RecoverableExecutionError):
    """`git apply` rejected an otherwise well-formed diff."""


class ExecutionError(RecoverableExecutionError):
    """The local executor could not produce a usable diff (e.g. bad JSON)."""


class TesterInfraError(AmaoError):
    """The test sandbox itself couldn't run (e.g. Docker missing, image pull
    failed) -- distinct from the tests running and failing, which is a normal
    TestOutcome, not an exception. Halts like other infra failures rather
    than consuming a milestone's attempt budget."""
