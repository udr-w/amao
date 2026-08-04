from __future__ import annotations

import os
from dataclasses import dataclass, field

from amao.exceptions import ConfigError


@dataclass(frozen=True)
class Config:
    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    WEBHOOK_URL: str = field(default_factory=lambda: os.getenv("NOTIFIER_WEBHOOK_URL", ""))

    MAX_REVIEW_ATTEMPTS: int = field(
        default_factory=lambda: int(os.getenv("MAX_REVIEW_ATTEMPTS", "3"))
    )
    MAX_MILESTONES: int = field(default_factory=lambda: int(os.getenv("MAX_MILESTONES", "50")))
    MAX_DIFF_CHARS: int = field(default_factory=lambda: int(os.getenv("MAX_DIFF_CHARS", "100000")))
    MAX_GOAL_CHARS: int = field(default_factory=lambda: int(os.getenv("MAX_GOAL_CHARS", "10000")))
    REQUEST_TIMEOUT_SECONDS: float = field(
        default_factory=lambda: float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
    )

    DB_FILENAME: str = "orchestrator_state.db"
    PLANNER_MODEL: str = field(default_factory=lambda: os.getenv("PLANNER_MODEL", "gpt-4o"))
    REVIEWER_MODEL: str = field(
        default_factory=lambda: os.getenv("REVIEWER_MODEL", "claude-3-7-sonnet-20250219")
    )

    def validate(self) -> None:
        """Fail fast when required secrets are missing.

        Called by the Orchestrator constructor (not just the CLI/demo entry
        point) so any programmatic use of this library is protected too.
        """
        missing = [
            name
            for name, value in (
                ("OPENAI_API_KEY", self.OPENAI_API_KEY),
                ("ANTHROPIC_API_KEY", self.ANTHROPIC_API_KEY),
            )
            if not value
        ]
        if missing:
            raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))
        if self.WEBHOOK_URL and not self.WEBHOOK_URL.startswith("https://"):
            raise ConfigError("NOTIFIER_WEBHOOK_URL must use https://")


config = Config()
