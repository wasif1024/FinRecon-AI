"""Agent performance per agent and prompt version (SRS §13): reliability, latency, cost, grounding, outcomes.

Built from data FinRecon already records: agent_execution_records (status, timings, tokens, tool calls), the audit
events written when agent output is verified (what was rejected), and what reviewers decided on recommendations.
"""

import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.authz import Actor, Permission
from finrecon_ai.db.enums import AgentExecutionStatus
from finrecon_ai.db.models import AgentExecutionRecord, AuditEvent, ResolutionRecommendation
from finrecon_ai.db.tenancy import bind_tenant

# USD per million tokens (list price). Cached input is billed lower, but the records count it as input,
# so the estimate is an upper bound.
PRICE_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}
VERIFICATION_EVENTS = ("agent.findings_recorded", "agent.recommendation_recorded")


@dataclass
class VersionMetrics:
    agent: str
    prompt: str  # agent@version#hash, or the deterministic pipeline version
    model: str | None
    runs: int = 0
    completed: int = 0
    failed: int = 0
    latency_p50_s: float | None = None
    latency_p95_s: float | None = None
    avg_input_tokens: float | None = None
    avg_output_tokens: float | None = None
    est_cost_usd: str = "0.00"
    est_cost_per_run_usd: str = "0.00"
    tool_calls: int = 0
    tool_errors: int = 0
    runs_with_rejected_output: int = 0
    rejected_items: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)  # recommendation status -> count

    @property
    def success_rate(self) -> float | None:
        return self.completed / self.runs if self.runs else None


def agent_metrics(session: Session, actor: Actor, days: int = 30) -> list[VersionMetrics]:
    actor.require(Permission.AGENT_METRICS_READ)
    bind_tenant(session, actor.tenant_id)
    since = datetime.now(UTC) - timedelta(days=days)
    runs = session.scalars(select(AgentExecutionRecord).where(AgentExecutionRecord.tenant_id == actor.tenant_id,
                                                              AgentExecutionRecord.started_at >= since)).all()
    rejected = _rejections(session, actor, [r.id for r in runs])
    outcomes = _outcomes(session, actor, [r.id for r in runs])

    groups: dict[tuple[str, str], list[AgentExecutionRecord]] = defaultdict(list)
    for run in runs:
        groups[(run.agent_name.value, run.workflow_version)].append(run)

    result = []
    for (agent, prompt), members in sorted(groups.items()):
        m = VersionMetrics(agent=agent, prompt=prompt, model=members[0].model, runs=len(members))
        m.completed = sum(r.status is AgentExecutionStatus.COMPLETED for r in members)
        m.failed = sum(r.status is AgentExecutionStatus.FAILED for r in members)
        latencies = sorted((r.completed_at - r.started_at).total_seconds() for r in members if r.completed_at)
        if latencies:
            m.latency_p50_s = round(statistics.median(latencies), 1)
            m.latency_p95_s = round(_percentile(latencies, 0.95), 1)
        tokens = [(r.input_tokens or 0, r.output_tokens or 0) for r in members if r.model]
        if tokens:
            m.avg_input_tokens = round(sum(t[0] for t in tokens) / len(tokens))
            m.avg_output_tokens = round(sum(t[1] for t in tokens) / len(tokens))
            cost = sum(_run_cost(r) for r in members if r.model)
            m.est_cost_usd = f"{cost:.2f}"
            m.est_cost_per_run_usd = f"{cost / len(tokens):.3f}"
        for r in members:
            m.tool_calls += len(r.tool_calls or [])
            m.tool_errors += sum(1 for call in (r.tool_calls or []) if not call.get("ok", True))
            if rejected.get(r.id):
                m.runs_with_rejected_output += 1
                m.rejected_items += rejected[r.id]
            for status, count in outcomes.get(r.id, {}).items():
                m.outcomes[status] = m.outcomes.get(status, 0) + count
        result.append(m)
    return result


def _rejections(session: Session, actor: Actor, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not run_ids:
        return {}
    events = session.execute(select(AuditEvent.agent_execution_id, AuditEvent.payload).where(
        AuditEvent.tenant_id == actor.tenant_id, AuditEvent.event_type.in_(VERIFICATION_EVENTS),
        AuditEvent.agent_execution_id.in_(run_ids))).all()
    counts: Counter[uuid.UUID] = Counter()
    for run_id, payload in events:
        counts[run_id] += len(payload.get("rejected") or [])
    return counts


def _outcomes(session: Session, actor: Actor, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    if not run_ids:
        return {}
    rows = session.execute(select(ResolutionRecommendation.agent_execution_id, ResolutionRecommendation.status).where(
        ResolutionRecommendation.tenant_id == actor.tenant_id,
        ResolutionRecommendation.agent_execution_id.in_(run_ids))).all()
    result: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for run_id, status in rows:
        result[run_id][status.value] = result[run_id].get(status.value, 0) + 1
    return result


def _run_cost(run: AgentExecutionRecord) -> Decimal:
    """Priced per model when the run recorded it (subagents on a smaller model), else all at the run's model."""
    if run.usage_by_model:
        return sum((_cost(model, u.get("input_tokens", 0), u.get("output_tokens", 0))
                    for model, u in run.usage_by_model.items()), Decimal(0))
    return _cost(run.model, run.input_tokens or 0, run.output_tokens or 0)


def _cost(model: str | None, input_tokens: int, output_tokens: int) -> Decimal:
    # The API may report a dated ID (claude-opus-5-5-2026…) for an alias: price by the alias it starts with.
    key = next((k for k in PRICE_PER_MTOK if (model or "").startswith(k)), "")
    price_in, price_out = PRICE_PER_MTOK.get(key, (Decimal(0), Decimal(0)))
    return (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)


def _percentile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)
