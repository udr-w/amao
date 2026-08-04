from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TestOutcome:
    """Result of running every applicable TestStrategy against a project.

    `ran=False` means no strategy detected as applicable -- there was
    nothing to test, which is not the same as a failure. `passed` is only
    meaningful when `ran` is True.
    """

    ran: bool
    passed: bool
    summary: str
    output: str
    strategy_names: tuple[str, ...] = field(default_factory=tuple)
