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
