"""Structured outputs the agents must return, and strict JSON-schema conversion for the Claude API."""

import copy
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from finrecon_ai.db.enums import ResolutionType

Confidence = Literal["low", "medium", "high"]
# Stored as NUMERIC in the database. Coarse on purpose: model confidence is not well calibrated.
CONFIDENCE_SCORE: dict[str, Decimal] = {"low": Decimal("0.3"), "medium": Decimal("0.6"), "high": Decimal("0.9")}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(_Strict):
    label: str  # E1, E2, ... referenced by findings
    kind: Literal["transaction", "historical_case", "policy_passage"]
    id: str  # exact ID from a tool result
    why_relevant: str


class Finding(_Strict):
    type: Literal["verified_fact", "hypothesis", "outstanding_requirement"]
    statement: str
    confidence: Confidence | None
    evidence_labels: list[str]


class InvestigationReport(_Strict):
    summary: str
    evidence: list[EvidenceItem]
    findings: list[Finding]
    possible_explanation: str
    confidence: Confidence
    recommended_next_action: str


class RiskAssessment(_Strict):
    summary: str
    evidence: list[EvidenceItem]
    indicators: list[Finding]
    referral_recommended: bool
    referral_reason: str | None
    confidence: Confidence


class ResolutionProposal(_Strict):
    resolution_type: ResolutionType  # same enum the database stores
    summary: str
    rationale: str
    action_steps: list[str]
    contact: Literal["paygate", "bank", "merchant", "internal", "none"]
    evidence: list[EvidenceItem]
    policy_labels: list[str]
    confidence: Confidence


OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "investigation_report": InvestigationReport,
    "risk_assessment": RiskAssessment,
    "resolution_proposal": ResolutionProposal,
}

# Keywords structured outputs may reject; validation of these happens in Pydantic after parsing.
_UNSUPPORTED = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
                "pattern", "minItems", "maxItems", "format", "default", "title")


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schema -> strict schema: every object closed and every property required (nullable if optional)."""
    schema = copy.deepcopy(model.model_json_schema())

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key in _UNSUPPORTED:
                node.pop(key, None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for key, value in node.items():
                if key in ("properties", "$defs"):  # keys here are names, not schema keywords
                    for sub in value.values():
                        visit(sub)
                else:
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)
    return schema
