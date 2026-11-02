"""Scripted stand-ins for the Claude API used by agent tests."""

import json
import uuid
from types import SimpleNamespace


def usage(i=100, o=50):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=10, cache_creation_input_tokens=0)


def tool_use(name, args=None, block_id=None):
    return SimpleNamespace(type="tool_use", id=block_id or f"toolu_{uuid.uuid4().hex[:8]}", name=name,
                           input=args or {})


def reply(stop_reason, *content, stop_details=None):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage(), stop_details=stop_details)


def final(report: dict):
    return reply("end_turn", SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=json.dumps(report)))


REPORT = {
    "summary": "Settlement fee is 1.06 higher than the gateway fee.",
    "evidence": [],
    "findings": [{"type": "hypothesis", "statement": "Gateway surcharge.", "confidence": "medium",
                  "evidence_labels": []}],
    "possible_explanation": "A surcharge was applied at settlement.",
    "confidence": "medium",
    "recommended_next_action": "Request the gateway fee schedule.",
}


class FakeClient:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[dict] = []

    def create(self, **params):
        self.calls.append({**params, "messages": list(params["messages"])})
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(params) if callable(step) else step


class AgentScripts:
    """Fake Claude for whole workflows: picks the script by which agent's system prompt is in the request.

    Each script is a list of steps; a step is a response or a callable(params) -> response, so a step can read
    earlier tool results from params["messages"].
    """

    def __init__(self, **scripts):
        from finrecon_ai.agents.prompt_registry import load_prompt
        from finrecon_ai.db.enums import AgentName

        self.by_system = {load_prompt(AgentName(name)).system_text: list(steps) for name, steps in scripts.items()}
        self.names = {load_prompt(AgentName(name)).system_text: name for name in scripts}
        self.calls: list[str] = []

    def create(self, **params):
        system = params["system"][0]["text"]
        self.calls.append(self.names[system])
        step = self.by_system[system].pop(0)
        if isinstance(step, Exception):
            raise step
        return step(params) if callable(step) else step


def last_tool_results(params) -> list[dict]:
    """Parsed JSON of the tool results in the latest user message."""
    content = params["messages"][-1]["content"]
    return [json.loads(r["content"]) for r in content if r.get("type") == "tool_result" and not r.get("is_error")]
