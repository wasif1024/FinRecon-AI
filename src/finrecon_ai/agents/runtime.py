"""Run one agent against one case as a Deep Agent (LangChain + LangGraph), with structured output.

Each run builds a Deep Agent from its prompt version: Claude via ChatAnthropic, the manifest's allow-listed
read-only tools as LangChain tools bound to this case, the manifest's subagents (delegated through Deep Agents'
`task` tool), and Deep Agents' planning (`write_todos`) and scratch-file tools, whose files live only in this run's
state. Shell execution and the catch-all general-purpose subagent are removed (`HARNESS_PROFILE`).

Every run is recorded in agent_execution_records (prompt ref, model, tool calls, tokens, output or error) and
audited; every tool call, including subagents' and Deep Agents' own, is audited too (FR-12). LangSmith traces come
from LangChain's tracer; the record keeps the trace's root run id. The caller commits.
"""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from deepagents import (
    FilesystemPermission,
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.utils import create_file_data
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, TodoListMiddleware, ToolCallRequest
from langchain.agents.structured_output import ProviderStrategy, StructuredOutputError
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.outputs import LLMResult
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from finrecon_ai.agents.llm import ChatModelFactory
from finrecon_ai.agents.prompt_registry import PromptSpec
from finrecon_ai.agents.schemas import OUTPUT_SCHEMAS, strict_json_schema
from finrecon_ai.agents.tools import TOOLS, Tool, ToolContext, ToolError, run_tool
from finrecon_ai.agents.workspace import seed
from finrecon_ai.audit import record_event
from finrecon_ai.db.enums import ActorType, AgentExecutionStatus
from finrecon_ai.db.models import AgentExecutionRecord
from finrecon_ai.log import get_logger, log_context
from finrecon_ai.tracing import tracing_enabled

log = get_logger(__name__)

MAX_TOOL_RESULT_CHARS = 20_000

# Agents investigate; they never run commands, and every subagent is declared in a prompt manifest with its own
# tool allow-list (no general-purpose subagent inheriting all tools). Registered for Anthropic models, which is
# what Deep Agents resolves ChatAnthropic to.
HARNESS_PROFILE = HarnessProfile(excluded_tools=frozenset({"execute"}),
                                 general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))
register_harness_profile("anthropic", HARNESS_PROFILE)


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
    client: ChatModelFactory,
    parent_execution_id: uuid.UUID | None = None,
    workspace: BackendProtocol | None = None,
) -> AgentResult:
    manifest = spec.manifest
    output_model = OUTPUT_SCHEMAS[manifest.output_schema]
    execution = AgentExecutionRecord(
        tenant_id=ctx.tenant_id, agent_name=manifest.id, status=AgentExecutionStatus.RUNNING,
        case_id=ctx.case.id, run_id=ctx.exception.run_id, parent_execution_id=parent_execution_id,
        workflow_version=spec.ref, model=manifest.model, input={"user_content": user_content},
        tool_calls=[], input_tokens=0, output_tokens=0,
    )
    session.add(execution)
    session.flush()

    tool_calls: list[dict[str, Any]] = []
    agent = build_deep_agent(spec, client, ctx, workspace=workspace, audit_for=lambda subagent: [
        ToolAuditMiddleware(session, ctx, execution, tool_calls, subagent=subagent)])
    agent_input: dict[str, Any] = {"messages": [{"role": "user", "content": user_content}]}
    if workspace is None:  # the run's own state: the manual and playbooks go in with the first message
        agent_input["files"] = {path: create_file_data(text) for path, text in spec.files.items()}
    elif spec.files:
        seed(workspace, spec.files)  # a case's sandbox: replace any earlier (or edited) copy with this version's

    observer = RunObserver(manifest.model)
    trace_id = uuid.uuid4()
    if tracing_enabled():
        execution.langsmith_run_id = str(trace_id)  # links the execution record to its LangSmith trace
    config = {
        "run_id": trace_id, "run_name": f"agent:{manifest.id.value}", "callbacks": [observer],
        "recursion_limit": manifest.max_turns * 6 + 20,  # graph steps; model turns are capped by middleware
        "tags": ["agent", manifest.id.value, spec.ref],
        "metadata": {"prompt": spec.ref, "case_id": str(ctx.case.id), "tenant_id": str(ctx.tenant_id),
                     "execution_id": str(execution.id), "model": manifest.model},
    }
    started = time.perf_counter()
    with log_context(execution_id=execution.id, case_id=ctx.case.id, agent=manifest.id.value, prompt=spec.ref):
        log.info("agent.started", model=manifest.model, effort=manifest.effort, tools=len(manifest.tools),
                 subagents=len(manifest.subagents))
        try:
            state = agent.invoke(agent_input, config=config)
            _tokens(execution, observer)
            output = _structured_output(state, output_model, manifest.output_schema, observer)
        except _Stop as exc:
            _tokens(execution, observer)
            _finish(session, ctx, execution, tool_calls, AgentExecutionStatus.FAILED, error=str(exc))
            log.warning("agent.failed", reason=str(exc), tool_calls=len(tool_calls))
            raise AgentFailed(str(exc), execution) from None
        except Exception as exc:
            _tokens(execution, observer)
            reason = _known_failure(exc, observer)
            if reason:
                _finish(session, ctx, execution, tool_calls, AgentExecutionStatus.FAILED, error=reason)
                log.warning("agent.failed", reason=reason, tool_calls=len(tool_calls))
                raise AgentFailed(reason, execution) from None
            # API errors etc.: record, then re-raise for the workflow's retry policy
            _finish(session, ctx, execution, tool_calls, AgentExecutionStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}")
            log.exception("agent.error", tool_calls=len(tool_calls))
            raise
        _finish(session, ctx, execution, tool_calls, AgentExecutionStatus.COMPLETED,
                output=output.model_dump(mode="json"))
        log.info("agent.completed", model_calls=observer.calls, tool_calls=len(tool_calls),
                 input_tokens=execution.input_tokens, output_tokens=execution.output_tokens,
                 duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return AgentResult(output=output, execution=execution, seen=dict(ctx.seen))


class _Stop(Exception):
    pass


def build_deep_agent(spec: PromptSpec, client: ChatModelFactory, ctx: ToolContext | None, *,
                     workspace: BackendProtocol | None = None,
                     audit_for: Callable[[str | None], list[AgentMiddleware]] = lambda subagent: []):
    """The Deep Agent for one prompt version: case tools bound to `ctx`, declared subagents (each on its own model
    if the manifest says so), planning, the operating-manual memory and playbook skills, a turn limit per agent and
    subagent, and structured output. Files live in `workspace` (a case's sandbox) or, when None, the run's state.
    `audit_for(None)` / `audit_for(name)` supply the audit middleware for the agent and each subagent. The Agent
    Server builds it with no case to draw its graph."""
    manifest = spec.manifest
    wanted = set(manifest.tools) | {t for s in manifest.subagents for t in s.tools}
    unknown = sorted(wanted - TOOLS.keys())
    if unknown:
        raise ValueError(f"{spec.ref} allows unknown tools: {unknown}")
    tools = {name: langchain_tool(TOOLS[name], ctx) for name in wanted}
    memory = [path for path in spec.files if not path.startswith("/skills/")]
    return create_deep_agent(
        model=client(manifest),
        tools=[tools[name] for name in manifest.tools],
        memory=memory or None,
        skills=["/skills/"] if manifest.skills else None,
        backend=workspace,
        permissions=[FilesystemPermission(operations=["write"], paths=[*memory, "/skills/**"], mode="deny")]
        if spec.files else None,
        # Static system prompt first, marked so it caches across cases.
        system_prompt=SystemMessage(content=[{"type": "text", "text": spec.system_text,
                                              "cache_control": {"type": "ephemeral"}}]),
        subagents=[{"name": s.name, "description": s.description, "system_prompt": spec.subagent_texts[s.name],
                    "tools": [tools[name] for name in s.tools],
                    "model": client(manifest.model_copy(update={
                        "model": s.model or manifest.model, "effort": s.effort or manifest.effort})),
                    "middleware": [*audit_for(s.name),
                                   ModelCallLimitMiddleware(run_limit=s.max_turns, exit_behavior="error")]}
                   for s in manifest.subagents],
        middleware=[TodoListMiddleware(), *audit_for(None),
                    ModelCallLimitMiddleware(run_limit=manifest.max_turns, exit_behavior="error")],
        response_format=ProviderStrategy(strict_json_schema(OUTPUT_SCHEMAS[manifest.output_schema]), strict=True),
        name=f"agent:{manifest.id.value}",
    )


def _structured_output(state: dict, output_model: type[BaseModel], schema_name: str,
                       observer: "RunObserver") -> BaseModel:
    raw = state.get("structured_response")
    if raw is None:
        raise _Stop(_stop_reason_message(observer) or f"no {schema_name} in the final answer")
    try:
        return output_model.model_validate(raw)
    except ValidationError as exc:
        raise _Stop(f"output did not match {schema_name}: {exc.error_count()} errors") from None


def _known_failure(exc: Exception, observer: "RunObserver") -> str | None:
    """Failures that end the run without a retry: the model declined, ran out of turns or of output budget."""
    from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError

    if isinstance(exc, ModelCallLimitExceededError):
        return f"no final answer within {observer.calls} model turns"
    if isinstance(exc, StructuredOutputError):
        return _stop_reason_message(observer) or f"final answer was not valid structured output: {exc}"
    return None


def _stop_reason_message(observer: "RunObserver") -> str | None:
    if observer.last_stop_reason == "refusal":
        return f"model declined (category: {observer.last_refusal_category})"
    if observer.last_stop_reason == "max_tokens":
        return "response hit max_tokens before finishing"
    return None


# --- tools ---


def langchain_tool(tool: Tool, ctx: ToolContext) -> StructuredTool:
    """A FinRecon tool as a LangChain tool, bound to one case. Claude receives the exact strict schema."""

    def call(**kwargs: Any) -> str:
        try:
            with ctx.db_lock:
                content = json.dumps(run_tool(ctx, tool, kwargs), default=str)
        except ToolError as exc:
            raise ToolException(str(exc)) from None
        if len(content) > MAX_TOOL_RESULT_CHARS:
            raise ToolException("result too large; narrow the query")
        return content

    return StructuredTool.from_function(
        func=call, name=tool.name, description=tool.description, args_schema=tool.input_model,
        handle_tool_error=True, handle_validation_error=lambda exc: f"invalid input: {exc}",
        extras={"provider_tool_definition": tool.definition()},
    )


class ToolAuditMiddleware(AgentMiddleware):
    """Records and audits every tool call the agent (or one of its subagents) makes, whatever the tool."""

    def __init__(self, session: Session, ctx: ToolContext, execution: AgentExecutionRecord,
                 entries: list[dict[str, Any]], subagent: str | None = None):
        super().__init__()
        self.session, self.ctx, self.execution, self.entries, self.subagent = session, ctx, execution, entries, subagent

    @property
    def name(self) -> str:
        return f"ToolAudit[{self.subagent or 'main'}]"

    def wrap_tool_call(self, request: ToolCallRequest,
                       handler: Callable[[ToolCallRequest], ToolMessage | Any]) -> ToolMessage | Any:
        started = time.perf_counter()
        call = request.tool_call
        result = handler(request)
        failed = isinstance(result, ToolMessage) and result.status == "error"
        error = str(result.content)[:500] if failed else None
        entry = {"tool": call["name"], "input": call.get("args", {}), "ok": not failed, "error": error,
                 "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
        if self.subagent:
            entry["subagent"] = self.subagent
        # Tool inputs can carry case data, so the log line keeps only name, outcome and timing.
        (log.warning if failed else log.info)("agent.tool_call", tool=call["name"], subagent=self.subagent,
                                              ok=not failed, error=error, duration_ms=entry["duration_ms"])
        # Not held while the tool runs: `task` runs a whole subagent, whose own tools take the lock.
        with self.ctx.db_lock:
            self.entries.append(entry)
            record_event(self.session, tenant_id=self.ctx.tenant_id, event_type="agent.tool_invoked",
                         entity_type="agent_execution", entity_id=self.execution.id, actor_type=ActorType.AGENT,
                         agent_execution_id=self.execution.id, payload=entry)
        return result


# --- model calls ---


class RunObserver(BaseCallbackHandler):
    """Counts Claude calls and tokens across the agent and its subagents, and logs each call."""

    def __init__(self, model: str):
        self.model = model
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.last_stop_reason: str | None = None
        self.last_refusal_category: str | None = None
        self.by_model: dict[str, dict[str, int]] = {}
        self._started: dict[uuid.UUID, float] = {}

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self._started[run_id] = time.perf_counter()

    def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self.calls += 1
        message = getattr(response.generations[0][0], "message", None) if response.generations else None
        usage = getattr(message, "usage_metadata", None) or {}
        meta = getattr(message, "response_metadata", None) or {}
        # LangChain's input_tokens already include cache reads and writes.
        self.input_tokens += usage.get("input_tokens", 0) or 0
        self.output_tokens += usage.get("output_tokens", 0) or 0
        model = meta.get("model_name") or meta.get("model") or self.model
        per_model = self.by_model.setdefault(model, {"input_tokens": 0, "output_tokens": 0})
        per_model["input_tokens"] += usage.get("input_tokens", 0) or 0
        per_model["output_tokens"] += usage.get("output_tokens", 0) or 0
        self.last_stop_reason = meta.get("stop_reason")
        self.last_refusal_category = (meta.get("stop_details") or {}).get("category") \
            if isinstance(meta.get("stop_details"), dict) else None
        started = self._started.pop(run_id, None)
        log.info("claude.call", turn=self.calls, model=meta.get("model_name") or meta.get("model") or self.model,
                 stop_reason=self.last_stop_reason, input_tokens=usage.get("input_tokens"),
                 output_tokens=usage.get("output_tokens"),
                 cache_read_tokens=(usage.get("input_token_details") or {}).get("cache_read"),
                 duration_ms=round((time.perf_counter() - started) * 1000, 1) if started else None,
                 anthropic_request_id=meta.get("id"))


def _tokens(execution: AgentExecutionRecord, observer: RunObserver) -> None:
    execution.input_tokens = observer.input_tokens
    execution.output_tokens = observer.output_tokens
    execution.usage_by_model = {model: dict(u) for model, u in observer.by_model.items()}


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
