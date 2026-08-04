"""LLM backend abstraction: decouples an agent *role* (planner/executor/reviewer)
from a *provider* (OpenAI/Anthropic), so any role can be pointed at either
provider via config -- e.g. making Claude the executor instead of the
reviewer -- without agents.py knowing which provider it's talking to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import anthropic
from openai import OpenAI

from amao.exceptions import ConfigError, ExecutionError

DEFAULT_MODELS_BY_PROVIDER = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-7-sonnet-20250219",
}


class LLMBackend(ABC):
    """A single provider-agnostic call: static system instructions + dynamic
    user content in, plain text out. Agents never see provider-specific
    request/response shapes.
    """

    @abstractmethod
    def complete(self, *, system: str, user: str, cache_key: str, json_mode: bool = False) -> str:
        """cache_key: a stable identifier for this call site, used to enable
        caching of the (identical, static) `system` content -- OpenAI's
        prompt_cache_key for routing, Anthropic's cache_control breakpoint.
        json_mode: request strict JSON output where the provider supports it
        natively (OpenAI); ignored otherwise (Anthropic relies on the
        prompt's own instructions plus the caller's own JSON parsing).
        """


class OpenAIBackend(LLMBackend):
    def __init__(self, client: OpenAI, model: str) -> None:
        self.client = client
        self.model = model

    def complete(self, *, system: str, user: str, cache_key: str, json_mode: bool = False) -> str:
        # The SDK's overloads require message dicts typed with Literal role
        # fields; a plain runtime-built list can't satisfy that statically
        # even though it matches the actual API contract exactly.
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if json_mode:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[call-overload]
                response_format={"type": "json_object"},
                prompt_cache_key=cache_key,
            )
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                prompt_cache_key=cache_key,
            )
        return response.choices[0].message.content or ""


class AnthropicBackend(LLMBackend):
    def __init__(self, client: anthropic.Anthropic, model: str, max_tokens: int = 4096) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, *, system: str, user: str, cache_key: str, json_mode: bool = False) -> str:
        # Anthropic has no app-supplied cache-key concept like OpenAI's -- it
        # caches by content hash under the hood. A non-empty `cache_key` here
        # just signals "mark this system block as cacheable"; the >=1024-token
        # (>=4096 on some newer model generations) minimum per breakpoint still
        # applies -- below that, this is a harmless no-op, not an error.
        if cache_key:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
        else:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        content_block = response.content[0]
        if not isinstance(content_block, anthropic.types.TextBlock):
            raise ExecutionError(
                f"Provider returned an unexpected content block type: "
                f"{type(content_block).__name__}"
            )
        return content_block.text


def build_backend(
    provider: str,
    model: str,
    *,
    openai_api_key: str,
    anthropic_api_key: str,
    timeout: float,
) -> LLMBackend:
    if provider == "openai":
        return OpenAIBackend(OpenAI(api_key=openai_api_key, timeout=timeout), model=model)
    if provider == "anthropic":
        return AnthropicBackend(
            anthropic.Anthropic(api_key=anthropic_api_key, timeout=timeout), model=model
        )
    raise ConfigError(
        f"Unknown provider {provider!r}; expected one of {sorted(DEFAULT_MODELS_BY_PROVIDER)}"
    )
