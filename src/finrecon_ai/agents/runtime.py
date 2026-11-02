"""Run one agent against one case: Claude tool loop, allow-listed read-only tools, structured output.

Every run is recorded in agent_execution_records (prompt ref, model, tool calls, tokens, output or error) and
audited; every tool call is audited too (FR-12). The caller commits.
"""

import json
import time

import langsmith as ls
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from finrecon_ai.agents.llm import LLMClient
from finrecon_ai.agents.prompt_registry import PromptSpec
from finrecon_ai.agents.schemas import OUTPUT_SCHEMAS, strict_json_schema
from finrecon_ai.agents.tools import TOOLS, ToolContext, ToolError, run_tool
from finrecon_ai.audit import record_event
from finrecon_ai.db.enums import ActorType, AgentExecutionStatus
from finrecon_ai.db.models import AgentExecutionRecord
from finrecon_ai.log import get_logger, log_context
from finrecon_ai.tracing import tracing_enabled

log = get_logger(__name__)

MAX_TOOL_RESULT_CHARS = 20_000


class AgentFailed(Exception):
    def __init__(self, message: str, execution: AgentExecutionRecord):
        super().__init__(message)
        self.execution = execution


@dataclass(frozen=True)
class AgentResult:
    output: BaseModel
    execution: AgentExecutionRecord
    seen: dict[str, set[str]]  # IDs the tools returned during this run, by kind


def run_agent(
    session: Session,
    *,
    spec: PromptSpec,
    ctx: ToolContext,
    user_content: str,
    client: LLMClient,
    parent_execution_id: uuid.UUID | None = None,
) -> AgentResult:
    manifest = spec.manifest
    unknown = [name for name in manifest.tools if name not in TOOLS]
    if unknown:
        raise ValueError(f"{spec.ref} allows unknown tools: {unknown}")
    tools = {name: TOOLS[name] for name in manifest.tools}
    output_model = OUTPUT_SCHEMAS[manifest.output_schema]

    execution = AgentExecutionRecord(
        tenant_id=ctx.tenant_id, agent_name=manifest.id, status=AgentExecutionStatus.RUNNING,
        case_id=ctx.case.id, run_id=ctx.exception.run_id, parent_execution_id=parent_execution_id,
        workflow_version=spec.ref, model=manifest.model, input={"user_content": user_content},
        tool_calls=[], input_tokens=0, output_tokens=0,
    )
    session.add(execution)
    session.flush()

    params: dict[str, Any] = {
        "model": manifest.model,
        "max_tokens": manifest.max_tokens,
        # Static system prompt first so it caches across cases.
        "system": [{"type": "text", "text": spec.system_text, "cache_control": {"type": "ephemeral"}}],
        "tools": [t.definition() for t in tools.values()],
        "output_config": {"effort": manifest.effort,
                          "format": {"type": "json_schema", "schema": strict_json_schema(output_model)}},
    }
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_calls: list[dict[str, Any]] = []
    with log_context(execution_id=execution.id, case_id=ctx.case.id, agent=manifest.id.value, prompt=spec.ref), \
            ls.trace(name=f"agent:{manifest.id.value}", run_type="chain", inputs={"user_content": user_content},
                     tags=["agent", manifest.id.value, spec.ref],
                     metadata={"prompt": spec.ref, "case_id": str(ctx.case.id), "tenant_id": str(ctx.tenant_id),
                               "execution_id": str(execution.id), "model": manifest.model}) as span:
        if tracing_enabled():
            execution.langsmith_run_id = str(span.id)  # links the execution record to its trace
        log.info("agent.started", model=manifest.model, effort=manifest.effort, tools=len(tools))
        result = _loop(session, spec, ctx, client, execution, tools, output_model, params, messages, tool_calls)
        span.end(outputs={"output": result.output.model_dump(mode="json"), "tool_calls": len(tool_calls),
                          "input_tokens": execution.input_tokens, "output_tokens": execution.output_tokens})
        return result


def _loop(session: Session, spec: PromptSpec, ctx: ToolContext, client: LLMClient,
          execution: AgentExecutionRecord, tools: dict, output_model: type[BaseModel], params: dict[str, Any],
          messages: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> AgentResult:
    manifest = spec.manifest
    started = time.perf_counter()
    try:
        for turn in range(1, manifest.max_turns + 1):
            call_started = time.perf_counter()
            response = client.create(messages=messages, **params)
            _count_tokens(execution, response)
            stop = response.stop_reason
            usage = getattr(response, "usage", None)
            log.info("claude.call", turn=turn, model=getattr(response, "model", manifest.model), stop_reason=stop,
                     input_tokens=getattr(usage, "input_tokens", None),
                     output_tokens=getattr(usage, "output_tokens", None),
                     cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
                     duration_ms=round((time.perf_counter() - call_started) * 1000, 1),
                     anthropic_request_id=getattr(response, "_request_id", None))

            if stop == "refusal":
                details = getattr(response, "stop_details", None)
                raise _Stop(f"model declined (category: {getattr(details, 'category', None)})")
            if stop == "max_tokens":
                raise _Stop("response hit max_tokens before finishing")

            # Append the assistant turn unchanged: thinking blocks must be passed back as-is.
            messages.append({"role": "assistant", "content": response.content})

            if stop == "pause_turn":
                continue
            if stop == "tool_use":
                results = [_call_tool(session, ctx, execution, tools, block, tool_calls)
                           for block in response.content if block.type == "tool_use"]
                messages.append({"role": "user", "content": results})
                continue

            text = "".join(b.text for b in response.content if b.type == "text")
            try:
                output = output_model.model_validate_json(text)
            except ValidationError as exc:
                raise _Stop(f"output did not match {manifest.output_schema}: {exc.error_count()} errors") from None
            _finish(session, ctx, execution, tool_calls, AgentExecutionStatus.COMPLETED,
                    output=output.model_dump(mode="json"))
            log.info("agent.completed", turns=turn, tool_calls=len(tool_calls),
                     input_tokens=execution.input_tokens, output_tokens=execution.output_tokens,
                     duration_ms=round((time.perf_counter() - started) * 1000, 1))
            return AgentResult(output=output, execution=execution, seen=dict(ctx.seen))

        raise _Stop(f"no final answer after {manifest.max_turns} turns")
    except _Stop as exc:
        _finish(session, ctx, execution, tool_calls, AgentExecutionStatus.FAILED, error=str(exc))
        log.warning("agent.failed", reason=str(exc), tool_calls=len(tool_calls))
        raise AgentFailed(str(exc), execution) from None
    except Exception as exc:  # API errors etc.: record, then re-raise for the caller's retry policy
        _finish(session, ctx, execution, tool_calls, AgentExecutionStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}")
        log.exception("agent.error", tool_calls=len(tool_calls))
        raise


class _Stop(Exception):
    pass


def _call_tool(session: Session, ctx: ToolContext, execution: AgentExecutionRecord, tools: dict, block: Any,
               log_entries: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    tool = tools.get(block.name)
    error = None
    with ls.trace(name=f"tool:{block.name}", run_type="tool", inputs={"input": block.input}) as span:
        try:
            if tool is None:
                raise ToolError(f"tool {block.name!r} is not available to this agent")
            content = json.dumps(run_tool(ctx, tool, block.input), default=str)
            if len(content) > MAX_TOOL_RESULT_CHARS:
                raise ToolError("result too large; narrow the query")
        except ToolError as exc:
            error, content = str(exc), str(exc)
        span.end(outputs={"content": content}, error=error)

    entry = {"tool": block.name, "input": block.input, "ok": error is None, "error": error,
             "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
    log_entries.append(entry)
    # Tool inputs can carry case data, so the log line keeps only name, outcome and timing.
    (log.info if error is None else log.warning)("agent.tool_call", tool=block.name, ok=error is None,
                                                  error=error, duration_ms=entry["duration_ms"])
    record_event(session, tenant_id=ctx.tenant_id, event_type="agent.tool_invoked", entity_type="agent_execution",
                 entity_id=execution.id, actor_type=ActorType.AGENT, agent_execution_id=execution.id, payload=entry)
    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": content}
    if error is not None:
        result["is_error"] = True
    return result


def _count_tokens(execution: AgentExecutionRecord, response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    cached = (getattr(usage, "cache_read_input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0)
                                                                    or 0)
    execution.input_tokens = (execution.input_tokens or 0) + (usage.input_tokens or 0) + cached
    execution.output_tokens = (execution.output_tokens or 0) + (usage.output_tokens or 0)


def _finish(session: Session, ctx: ToolContext, execution: AgentExecutionRecord, tool_calls: list,
            status: AgentExecutionStatus, *, output: dict | None = None, error: str | None = None) -> None:
    execution.status = status
    execution.tool_calls = tool_calls
    execution.output = output
    execution.error = error
    execution.completed_at = datetime.now(UTC)
    session.flush()
    record_event(session, tenant_id=ctx.tenant_id, event_type=f"agent.execution_{status.value}",
                 entity_type="agent_execution", entity_id=execution.id, actor_type=ActorType.AGENT,
                 agent_execution_id=execution.id,
                 payload={"agent": execution.agent_name, "prompt": execution.workflow_version,
                          "case_id": ctx.case.id, "tool_calls": len(tool_calls), "error": error,
                          "input_tokens": execution.input_tokens, "output_tokens": execution.output_tokens})
