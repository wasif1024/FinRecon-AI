"""Write agent output to the case, deterministically and only what can be verified (FR-05, FR-06, FR-10).

Agents never write to the database. Their structured output comes here, where:
- every cited ID must resolve to something a tool returned during that same run, or it is dropped;
- a "verified fact" whose evidence did not survive is downgraded to a hypothesis and marked as such;
- amounts and currencies on recommendations come from the reconciliation engine, never from the model.
Everything that was not accepted is listed in the result and the audit event, so nothing disappears silently.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from finrecon_ai.agents.schemas import CONFIDENCE_SCORE, EvidenceItem, Finding, ResolutionProposal
from finrecon_ai.agents.tools import ToolContext
from finrecon_ai.audit import record_event
from finrecon_ai.db.enums import ActorType, EvidenceType, FindingType, RecommendationStatus
from finrecon_ai.db.models import (
    AgentExecutionRecord,
    InvestigationEvidence,
    InvestigationFinding,
    PolicyDocument,
    ResolutionRecommendation,
)
from finrecon_ai.log import get_logger

log = get_logger(__name__)

UNVERIFIED_PREFIX = "[Unverified: cited evidence could not be confirmed] "
EVIDENCE_TYPES = {
    "transaction": EvidenceType.TRANSACTION,
    "historical_case": EvidenceType.HISTORICAL_CASE,
    "policy_passage": EvidenceType.POLICY_PASSAGE,
}


@dataclass
class PersistedOutput:
    evidence: dict[str, uuid.UUID] = field(default_factory=dict)  # label -> investigation_evidence.id
    findings: list[uuid.UUID] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    recommendation_id: uuid.UUID | None = None


def persist_findings(session: Session, ctx: ToolContext, execution: AgentExecutionRecord,
                     evidence: list[EvidenceItem], findings: list[Finding]) -> PersistedOutput:
    result = PersistedOutput()
    _persist_evidence(session, ctx, execution, evidence, result)

    for finding in findings:
        ids = [result.evidence[label] for label in finding.evidence_labels if label in result.evidence]
        unknown = [label for label in finding.evidence_labels if label not in result.evidence]
        if unknown:
            result.rejected.append(f"finding cites unknown or rejected evidence {unknown}: {finding.statement[:80]}")
        finding_type = FindingType(finding.type)
        statement = finding.statement
        if finding_type is FindingType.VERIFIED_FACT and not ids:
            finding_type, statement = FindingType.HYPOTHESIS, UNVERIFIED_PREFIX + statement
            result.rejected.append(f"verified fact downgraded to hypothesis (no verifiable evidence): "
                                   f"{finding.statement[:80]}")
        row = InvestigationFinding(
            tenant_id=ctx.tenant_id, case_id=ctx.case.id, agent_execution_id=execution.id, finding_type=finding_type,
            statement=statement, evidence_ids=ids,
            confidence=CONFIDENCE_SCORE[finding.confidence] if finding.confidence else None,
        )
        session.add(row)
        session.flush()
        result.findings.append(row.id)

    _audit(session, ctx, execution, "agent.findings_recorded", {
        "evidence": len(result.evidence), "findings": len(result.findings), "rejected": result.rejected})
    return result


def persist_recommendation(session: Session, ctx: ToolContext, execution: AgentExecutionRecord,
                           proposal: ResolutionProposal) -> PersistedOutput:
    result = PersistedOutput()
    _persist_evidence(session, ctx, execution, proposal.evidence, result)
    policy_ids = [str(result.evidence[label]) for label in proposal.policy_labels if label in result.evidence]
    warnings = []
    if not policy_ids:
        warnings.append("no verified policy passage supports this recommendation")
    missing = [label for label in proposal.policy_labels if label not in result.evidence]
    if missing:
        result.rejected.append(f"policy labels not backed by accepted evidence: {missing}")

    # Earlier open recommendations are replaced, never edited: history stays readable.
    session.execute(update(ResolutionRecommendation)
                    .where(ResolutionRecommendation.case_id == ctx.case.id,
                           ResolutionRecommendation.status.in_([RecommendationStatus.DRAFT,
                                                                RecommendationStatus.PENDING_APPROVAL]))
                    .values(status=RecommendationStatus.SUPERSEDED))
    version = (session.scalar(select(func.max(ResolutionRecommendation.version))
                              .where(ResolutionRecommendation.case_id == ctx.case.id)) or 0) + 1

    recommendation = ResolutionRecommendation(
        tenant_id=ctx.tenant_id, case_id=ctx.case.id, version=version, resolution_type=proposal.resolution_type,
        status=RecommendationStatus.DRAFT, summary=proposal.summary, rationale=proposal.rationale,
        confidence=CONFIDENCE_SCORE[proposal.confidence], agent_execution_id=execution.id,
        proposed_action={
            "action_steps": proposal.action_steps,
            "contact": proposal.contact,
            # Figures come from the reconciliation engine, not from the model (SRS §3, FR-03).
            "amount": str(ctx.case.discrepancy_amount) if ctx.case.discrepancy_amount is not None else None,
            "currency": ctx.case.currency,
            "category": str(ctx.exception.category),
            "evidence_ids": [str(e) for e in result.evidence.values()],
            "policy_evidence_ids": policy_ids,
            "warnings": warnings,
        },
    )
    session.add(recommendation)
    session.flush()
    result.recommendation_id = recommendation.id

    _audit(session, ctx, execution, "agent.recommendation_recorded", {
        "recommendation_id": recommendation.id, "version": version, "resolution_type": proposal.resolution_type,
        "evidence": len(result.evidence), "rejected": result.rejected, "warnings": warnings})
    return result


def _persist_evidence(session: Session, ctx: ToolContext, execution: AgentExecutionRecord,
                      items: list[EvidenceItem], result: PersistedOutput) -> None:
    for item in items:
        if item.label in result.evidence:
            result.rejected.append(f"{item.label}: duplicate label")
            continue
        canonical = ctx.resolve(item.kind, item.id)
        if canonical is None:
            result.rejected.append(f"{item.label}: {item.kind} {item.id!r} was not returned by any tool in this run")
            continue

        row = InvestigationEvidence(tenant_id=ctx.tenant_id, case_id=ctx.case.id, agent_execution_id=execution.id,
                                    evidence_type=EVIDENCE_TYPES[item.kind], excerpt=item.why_relevant,
                                    source_reference={"cited_as": item.id, "label": item.label})
        if item.kind == "transaction":
            row.transaction_id = uuid.UUID(canonical)
        elif item.kind == "historical_case":
            row.related_case_id = uuid.UUID(canonical)
        else:
            details: dict[str, Any] = ctx.details.get("policy_passage", {}).get(canonical, {})
            document_id = details.get("document_id")
            if not document_id or session.get(PolicyDocument, uuid.UUID(str(document_id))) is None:
                result.rejected.append(f"{item.label}: policy passage {canonical} has no current document")
                continue
            row.policy_document_id = uuid.UUID(str(document_id))
            row.source_reference |= {"chunk_id": canonical, **{k: v for k, v in details.items()
                                                               if k != "document_id"}}
        session.add(row)
        session.flush()
        result.evidence[item.label] = row.id


def _audit(session: Session, ctx: ToolContext, execution: AgentExecutionRecord, event_type: str,
           payload: dict[str, Any]) -> None:
    if payload.get("rejected"):
        log.warning("agent.output_partially_rejected", rejected=len(payload["rejected"]), audit_event=event_type)
    record_event(session, tenant_id=ctx.tenant_id, event_type=event_type, entity_type="investigation_case",
                 entity_id=ctx.case.id, actor_type=ActorType.AGENT, agent_execution_id=execution.id,
                 payload=payload)
