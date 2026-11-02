"""Scripted stand-ins for Claude used by agent tests: a LangChain chat model that Deep Agents drives like the real one.

A script is a list of steps; a step is an AIMessage (built with `reply`/`final`) or a callable(request) -> AIMessage,
so a step can read earlier tool results. `request` is {"system": str, "messages": [...], "tools": [names], ...}.
"""

import json
import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field


def usage(i=100, o=50):
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o, "input_token_details": {"cache_read": 10}}


def tool_use(name, args=None, block_id=None):
    return {"name": name, "args": args or {}, "id": block_id or f"toolu_{uuid.uuid4().hex[:8]}", "type": "tool_call"}


def reply(stop_reason, *content, stop_details=None):
    """An assistant turn. `content` holds tool calls (from `tool_use`) and/or text (str or {"type": "text"})."""
    is_call = [isinstance(c, dict) and c.get("type") == "tool_call" for c in content]
    calls = [c for c, call in zip(content, is_call, strict=True) if call]
    texts = [c if isinstance(c, str) else c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")
             for c, call in zip(content, is_call, strict=True) if not call]
    text = "".join(t for t in texts if t)
    meta = {"stop_reason": stop_reason, "model_name": "claude-opus-5-5"}
    if stop_details is not None:
        meta["stop_details"] = stop_details
    return AIMessage(content=text, tool_calls=calls, usage_metadata=usage(), response_metadata=meta)


def final(report: dict):
    return reply("end_turn", json.dumps(report))


REPORT = {
    "summary": "Settlement fee is 1.06 higher than the gateway fee.",
    "evidence": [],
    "findings": [{"type": "hypothesis", "statement": "Gateway surcharge.", "confidence": "medium",
                  "evidence_labels": []}],
    "possible_explanation": "A surcharge was applied at settlement.",
    "confidence": "medium",
    "recommended_next_action": "Request the gateway fee schedule.",
}


def _text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(b.get("text", "") for b in message.content if isinstance(b, dict))


class ScriptedChatModel(BaseChatModel):
    """Pops the next step from `script_for(request)`'s script on every call. Reports itself as an Anthropic model so
    Deep Agents applies the same harness profile as in production."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    script_for: Any  # callable(request) -> list of steps
    record: Any  # list shared with the factory (typed Any so pydantic keeps the same object)
    bound_tools: list = Field(default_factory=list)
    bound_kwargs: dict = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _get_ls_params(self, stop=None, **kwargs):
        return {"ls_provider": "anthropic", "ls_model_name": "claude-opus-5-5", "ls_model_type": "chat"}

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"bound_tools": list(tools), "bound_kwargs": kwargs})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = next((_text(m) for m in messages if isinstance(m, SystemMessage)), "")
        names = [t.get("name") if isinstance(t, dict) else getattr(t, "name", None) for t in self.bound_tools]
        request = {"system": system, "messages": [m for m in messages if not isinstance(m, SystemMessage)],
                   "tools": names, "kwargs": {**self.bound_kwargs, **kwargs}, "raw_tools": self.bound_tools}
        steps = self.script_for(request)
        self.record.append(request)
        step = steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        message = step(request) if callable(step) else step
        return ChatResult(generations=[ChatGeneration(message=message)])


class FakeClient:
    """A chat-model factory with one script, for single-agent tests. `calls` holds every request."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[dict] = []
        self.manifests: list = []

    def __call__(self, manifest):
        self.manifests.append(manifest)
        return ScriptedChatModel(script_for=lambda request: self.steps, record=self.calls)


class AgentScripts:
    """Fake Claude for whole workflows: picks the script by which agent's (or subagent's) prompt is in the request.

    Keys are agent names (`investigation=[...]`) or `<agent>__<subagent>` with dashes as underscores
    (`investigation__policy_researcher=[...]`). `calls` lists which script answered each request.
    """

    def __init__(self, **scripts):
        from finrecon_ai.agents.prompt_registry import load_prompt
        from finrecon_ai.db.enums import AgentName

        self.prompts: list[tuple[str, str]] = []  # (key, prompt text), subagents first: they are more specific
        for key in scripts:
            agent, _, sub = key.partition("__")
            spec = load_prompt(AgentName(agent))
            text = spec.subagent_texts[sub.replace("_", "-")] if sub else spec.system_text
            self.prompts.append((key, text.strip()))
        self.prompts.sort(key=lambda kv: "__" not in kv[0])
        self.scripts = {key: list(steps) for key, steps in scripts.items()}
        self.calls: list[str] = []
        self.requests: list[dict] = []

    def __call__(self, manifest):
        return ScriptedChatModel(script_for=self._script_for, record=self.requests)

    def _script_for(self, request) -> list:
        for key, text in self.prompts:
            if text[:400] in request["system"]:
                self.calls.append(key)
                return self.scripts[key]
        raise AssertionError(f"no script for system prompt starting {request['system'][:80]!r}")


def last_tool_results(request) -> list[dict]:
    """Parsed JSON of the tool results answering the latest assistant turn."""
    results = []
    for message in reversed(request["messages"]):
        if not isinstance(message, ToolMessage):
            break
        if message.status != "error":
            results.append(json.loads(message.content))
    return list(reversed(results))


def tool_errors(request) -> list[str]:
    return [m.content for m in request["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
