"""Thin seam between the agent runtime and the Claude API, so tests can script responses."""

from typing import Any, Protocol

import anthropic

# Re-run a classifier decline on Anthropic's recommended model for that refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMClient(Protocol):
    def create(self, **params: Any) -> Any:
        """Same parameters as client.beta.messages.create; returns a Message-like object."""
        ...


class AnthropicClient:
    def __init__(self, client: anthropic.Anthropic | None = None):
        # Credentials resolve from ANTHROPIC_API_KEY (loaded from .env by Settings) or an `ant auth` profile.
        self._client = client or anthropic.Anthropic()

    def create(self, **params: Any) -> Any:
        return self._client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)


def default_client() -> LLMClient:
    from finrecon_ai.config import get_settings

    settings = get_settings()
    return AnthropicClient(anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key
                           else None)
