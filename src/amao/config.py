from __future__ import annotations

import os
from dataclasses import dataclass, field

from amao.exceptions import ConfigError
from amao.llm import PROVIDERS

_ROLES = ("PLANNER", "EXECUTOR", "REVIEWER")


@dataclass(frozen=True)
class Config:
    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    DEEPSEEK_API_KEY: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    MOONSHOT_API_KEY: str = field(default_factory=lambda: os.getenv("MOONSHOT_API_KEY", ""))
    XAI_API_KEY: str = field(default_factory=lambda: os.getenv("XAI_API_KEY", ""))
    GEMINI_API_KEY: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
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

    # The Tester agent runs a project's own tests in a disposable Docker
    # container before the Reviewer sees the diff -- see TESTER_AGENT_PLAN.md.
    # Off by default during rollout: it's a new hard runtime prerequisite
    # (Docker) and a new pipeline stage that didn't exist before amao added it.
    ENABLE_TESTER: bool = field(
        default_factory=lambda: os.getenv("ENABLE_TESTER", "false").lower() == "true"
    )
    TESTER_TIMEOUT_SECONDS: float = field(
        default_factory=lambda: float(os.getenv("TESTER_TIMEOUT_SECONDS", "300"))
    )
    MAX_TEST_OUTPUT_CHARS: int = field(
        default_factory=lambda: int(os.getenv("MAX_TEST_OUTPUT_CHARS", "20000"))
    )
    # A separate, narrower opt-in on top of ENABLE_TESTER: generates a
    # Gherkin/behave UI scenario from the milestone description via an extra
    # LLM call. Inert if ENABLE_TESTER is false regardless of this setting --
    # the whole Tester stage is skipped in that case. Off by default: this is
    # the least proven layer of the Tester (see TESTER_AGENT_PLAN.md).
    ENABLE_BDD: bool = field(
        default_factory=lambda: os.getenv("ENABLE_BDD", "false").lower() == "true"
    )

    DB_FILENAME: str = "orchestrator_state.db"

    # Which provider powers each agent role -- any key in amao.llm.PROVIDERS
    # ("openai", "anthropic", "deepseek", "moonshot", "xai", "gemini").
    # Defaults reproduce amao's original wiring (OpenAI plans + executes,
    # Anthropic reviews) -- e.g. set REVIEWER_PROVIDER=deepseek to make
    # DeepSeek the reviewer instead of Claude.
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
                spec = PROVIDERS.get(getattr(self, provider_field))
                object.__setattr__(self, model_field, spec.default_model if spec else "")

    def api_keys(self) -> dict[str, str]:
        """Provider name -> API key, for amao.llm.build_backend()."""
        return {name: getattr(self, spec.api_key_env) for name, spec in PROVIDERS.items()}

    def validate(self) -> None:
        """Fail fast when required secrets/config are missing.

        Called by the Orchestrator constructor (not just the CLI/demo entry
        point) so any programmatic use of this library is protected too.
        Only requires the API key(s) actually needed by the providers
        selected for each role -- rewiring every role to one provider
        shouldn't require an unused key for another.
        """
        providers = {self.PLANNER_PROVIDER, self.EXECUTOR_PROVIDER, self.REVIEWER_PROVIDER}
        unknown = providers - set(PROVIDERS)
        if unknown:
            raise ConfigError(
                f"Unknown provider(s) {sorted(unknown)}; expected one of {sorted(PROVIDERS)}"
            )

        missing = sorted(
            {
                PROVIDERS[p].api_key_env
                for p in providers
                if not getattr(self, PROVIDERS[p].api_key_env)
            }
        )
        if missing:
            raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))

        if self.WEBHOOK_URL and not self.WEBHOOK_URL.startswith("https://"):
            raise ConfigError("NOTIFIER_WEBHOOK_URL must use https://")


config = Config()
