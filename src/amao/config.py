from __future__ import annotations

import os
from dataclasses import dataclass, field

from amao.exceptions import ConfigError
from amao.llm import DEFAULT_MODELS_BY_PROVIDER

_ROLES = ("PLANNER", "EXECUTOR", "REVIEWER")


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

    # Which provider powers each agent role. Defaults reproduce amao's
    # original wiring (OpenAI plans + executes, Anthropic reviews) -- set
    # any of these to "openai" or "anthropic" to rewire the pipeline, e.g.
    # REVIEWER_PROVIDER=openai to make GPT the reviewer instead of Claude.
    PLANNER_PROVIDER: str = field(default_factory=lambda: os.getenv("PLANNER_PROVIDER", "openai"))
    EXECUTOR_PROVIDER: str = field(default_factory=lambda: os.getenv("EXECUTOR_PROVIDER", "openai"))
    REVIEWER_PROVIDER: str = field(
        default_factory=lambda: os.getenv("REVIEWER_PROVIDER", "anthropic")
    )

    # Empty string means "use the default model for the selected provider"
    # (resolved in __post_init__, since a default_factory can't see other fields).
    PLANNER_MODEL: str = field(default_factory=lambda: os.getenv("PLANNER_MODEL", ""))
    EXECUTOR_MODEL: str = field(default_factory=lambda: os.getenv("EXECUTOR_MODEL", ""))
    REVIEWER_MODEL: str = field(default_factory=lambda: os.getenv("REVIEWER_MODEL", ""))

    def __post_init__(self) -> None:
        for role in _ROLES:
            model_field, provider_field = f"{role}_MODEL", f"{role}_PROVIDER"
            if not getattr(self, model_field):
                provider = getattr(self, provider_field)
                object.__setattr__(self, model_field, DEFAULT_MODELS_BY_PROVIDER.get(provider, ""))

    def validate(self) -> None:
        """Fail fast when required secrets/config are missing.

        Called by the Orchestrator constructor (not just the CLI/demo entry
        point) so any programmatic use of this library is protected too.
        Only requires the API key(s) actually needed by the providers
        selected for each role -- rewiring every role to one provider
        shouldn't require an unused key for the other.
        """
        providers = {self.PLANNER_PROVIDER, self.EXECUTOR_PROVIDER, self.REVIEWER_PROVIDER}
        unknown = providers - set(DEFAULT_MODELS_BY_PROVIDER)
        if unknown:
            raise ConfigError(
                f"Unknown provider(s) {sorted(unknown)}; expected one of "
                f"{sorted(DEFAULT_MODELS_BY_PROVIDER)}"
            )

        missing = []
        if "openai" in providers and not self.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if "anthropic" in providers and not self.ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
        if missing:
            raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))

        if self.WEBHOOK_URL and not self.WEBHOOK_URL.startswith("https://"):
            raise ConfigError("NOTIFIER_WEBHOOK_URL must use https://")


config = Config()
