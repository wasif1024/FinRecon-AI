"""Chat models for the agents: LangChain's ChatAnthropic, configured per prompt version.

The seam is a factory (manifest -> chat model), so tests substitute a scripted LangChain chat model and the agent
harness above it (Deep Agents, LangChain tools, LangGraph) runs unchanged.
"""

from typing import Protocol

from langchain_core.language_models import BaseChatModel

from finrecon_ai.agents.prompt_registry import Manifest

# Re-run a classifier decline on Anthropic's recommended model for that refusal category. ChatAnthropic sends
# requests with `betas` through client.beta.messages.create.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Haiku has no server-side fallback: sending `fallbacks` to it is a 400.
NO_FALLBACK_PREFIXES = ("claude-haiku-",)


class ChatModelFactory(Protocol):
    def __call__(self, manifest: Manifest) -> BaseChatModel:
        """A chat model configured for one prompt version (model, effort, output budget)."""
        ...


def anthropic_chat_model(manifest: Manifest, api_key: str | None = None) -> BaseChatModel:
    return claude(manifest.model, effort=manifest.effort, max_tokens=manifest.max_tokens, api_key=api_key)


def claude(model: str, *, effort: str, max_tokens: int, api_key: str | None = None) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    fallback = not model.startswith(NO_FALLBACK_PREFIXES)
    return ChatAnthropic(
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": effort},
        **({"betas": [FALLBACK_BETA], "model_kwargs": {"fallbacks": "default"}} if fallback else {}),
        max_retries=2,  # the workflow's RetryPolicy retries the whole agent step on top of this
        # Credentials resolve from ANTHROPIC_API_KEY (loaded from .env by Settings) when not passed.
        **({"api_key": api_key} if api_key else {}),
    )


def default_client() -> ChatModelFactory:
    from finrecon_ai.config import get_settings

    api_key = get_settings().anthropic_api_key or None
    return lambda manifest: anthropic_chat_model(manifest, api_key)

