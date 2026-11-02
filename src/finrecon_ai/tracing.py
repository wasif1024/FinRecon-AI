"""LangSmith tracing setup.

The app reads `.env` through pydantic-settings, which does not export values to the process environment; LangSmith
and LangGraph read their configuration from os.environ. configure_tracing() bridges the two, once at startup.

With LANGSMITH_HIDE_CONTENT=true, traces keep their structure, timings, token counts and metadata but carry no
inputs or outputs: the switch to flip before any real customer data flows through the agents.
"""

import os

from finrecon_ai.log import get_logger

log = get_logger(__name__)


def configure_tracing(settings) -> bool:
    """Returns True when traces will be sent to LangSmith."""
    enabled = bool(settings.langsmith_tracing and settings.langsmith_api_key)
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if not enabled:
        return False
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    hide = "true" if settings.langsmith_hide_content else "false"
    os.environ["LANGSMITH_HIDE_INPUTS"] = hide
    os.environ["LANGSMITH_HIDE_OUTPUTS"] = hide
    log.info("tracing.enabled", project=settings.langsmith_project, content_hidden=settings.langsmith_hide_content)
    return True


def tracing_enabled() -> bool:
    from langsmith.utils import tracing_is_enabled

    return bool(tracing_is_enabled())
