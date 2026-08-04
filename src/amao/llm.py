"""LLM backend abstraction: decouples an agent *role* (planner/executor/reviewer)
from a *provider* (OpenAI, Anthropic, DeepSeek, Moonshot/Kimi, xAI/Grok,
Google/Gemini, ...), so any role can be pointed at any supported provider via
config -- e.g. making Claude the executor instead of the reviewer -- without
agents.py knowing which provider it's talking to.

DeepSeek, Moonshot, xAI, and Gemini are all reached through OpenAIBackend --
they each expose an OpenAI-Chat-Completions-compatible endpoint, so a base_url
swap is the entire integration. Model names for these providers churn faster
than OpenAI's/Anthropic's (DeepSeek deprecated `deepseek-chat`/`deepseek-reasoner`
in favor of versioned names on 2026-07-24, for example) -- if a default here
starts 404ing, check the provider's current docs and override via the matching
env var rather than assuming the default is stale everywhere.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass

import anthropic
from openai import NOT_GIVEN, OpenAI

from amao.exceptions import ConfigError, ExecutionError


def _image_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


@dataclass(frozen=True)
class ProviderSpec:
    default_model: str
    api_key_env: str
    kind: str  # "openai" | "anthropic" -- which backend class this provider uses
    base_url: str | None = None  # None means the provider's native/default endpoint
    supports_prompt_cache_key: bool = False  # OpenAI-specific cache-routing hint


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        default_model="gpt-4o",
        api_key_env="OPENAI_API_KEY",
        kind="openai",
        supports_prompt_cache_key=True,
    ),
    "anthropic": ProviderSpec(
        default_model="claude-3-7-sonnet-20250219",
        api_key_env="ANTHROPIC_API_KEY",
        kind="anthropic",
    ),
    "deepseek": ProviderSpec(
        default_model="deepseek-v4-flash",
        api_key_env="DEEPSEEK_API_KEY",
        kind="openai",
        base_url="https://api.deepseek.com/v1",
    ),
    "moonshot": ProviderSpec(
        default_model="kimi-k3",
        api_key_env="MOONSHOT_API_KEY",
        kind="openai",
        base_url="https://api.moonshot.ai/v1",
    ),
    "xai": ProviderSpec(
        default_model="grok-4.3",
        api_key_env="XAI_API_KEY",
        kind="openai",
        base_url="https://api.x.ai/v1",
    ),
    "gemini": ProviderSpec(
        default_model="gemini-3.5-flash",
        api_key_env="GEMINI_API_KEY",
        kind="openai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
}

# Back-compat alias for callers that only care about the default model per provider.
DEFAULT_MODELS_BY_PROVIDER = {name: spec.default_model for name, spec in PROVIDERS.items()}


class LLMBackend(ABC):
    """A single provider-agnostic call: static system instructions + dynamic
    user content in, plain text out. Agents never see provider-specific
    request/response shapes.
    """

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        cache_key: str,
        json_mode: bool = False,
        images: tuple[str, ...] = (),
    ) -> str:
        """cache_key: a stable identifier for this call site, used to enable
        caching of the (identical, static) `system` content -- OpenAI's
        prompt_cache_key for routing, Anthropic's cache_control breakpoint.
        json_mode: request strict JSON output where the provider supports it
        natively (OpenAI-shaped APIs); ignored otherwise (Anthropic relies on
        the prompt's own instructions plus the caller's own JSON parsing).
        images: local PNG file paths (e.g. a UI screenshot) attached to the
        user message. Whether the *model* actually supports vision is on the
        caller to get right by picking a vision-capable model -- amao has no
        registry of which models do and doesn't try to guess; an
        unsupported combination will surface as a provider API error.
        """


class OpenAIBackend(LLMBackend):
    """Talks to any OpenAI-Chat-Completions-compatible endpoint: real OpenAI,
    or DeepSeek/Moonshot/xAI/Gemini via a base_url swap on the same client.
    `supports_cache_key` gates `prompt_cache_key` -- it's an OpenAI-specific
    cache-routing hint, not part of the common wire format these providers
    implement, so it's only sent to the real OpenAI endpoint.
    """

    def __init__(self, client: OpenAI, model: str, supports_cache_key: bool = True) -> None:
        self.client = client
        self.model = model
        self.supports_cache_key = supports_cache_key

    def complete(
        self,
        *,
        system: str,
        user: str,
        cache_key: str,
        json_mode: bool = False,
        images: tuple[str, ...] = (),
    ) -> str:
        user_content: object = user
        if images:
            parts: list[dict[str, object]] = [{"type": "text", "text": user}]
            for path in images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{_image_base64(path)}"},
                    }
                )
            user_content = parts

        # The SDK's overloads require message dicts typed with Literal role
        # fields; a plain runtime-built list can't satisfy that statically
        # even though it matches the actual API contract exactly.
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        prompt_cache_key = cache_key if self.supports_cache_key else NOT_GIVEN
        if json_mode:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[call-overload]
                response_format={"type": "json_object"},
                prompt_cache_key=prompt_cache_key,
            )
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                prompt_cache_key=prompt_cache_key,  # type: ignore[arg-type]
            )
        return response.choices[0].message.content or ""


class AnthropicBackend(LLMBackend):
    def __init__(self, client: anthropic.Anthropic, model: str, max_tokens: int = 4096) -> None:
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def complete(
        self,
        *,
        system: str,
        user: str,
        cache_key: str,
        json_mode: bool = False,
        images: tuple[str, ...] = (),
    ) -> str:
        user_content: object = user
        if images:
            parts: list[dict[str, object]] = [{"type": "text", "text": user}]
            for path in images:
                parts.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _image_base64(path),
                        },
                    }
                )
            user_content = parts

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
                messages=[{"role": "user", "content": user_content}],  # type: ignore[typeddict-item]
            )
        else:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],  # type: ignore[typeddict-item]
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
    api_keys: dict[str, str],
    timeout: float,
) -> LLMBackend:
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ConfigError(f"Unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}")

    api_key = api_keys.get(provider, "")
    if spec.kind == "anthropic":
        return AnthropicBackend(anthropic.Anthropic(api_key=api_key, timeout=timeout), model=model)
    return OpenAIBackend(
        OpenAI(api_key=api_key, base_url=spec.base_url, timeout=timeout),
        model=model,
        supports_cache_key=spec.supports_prompt_cache_key,
    )
