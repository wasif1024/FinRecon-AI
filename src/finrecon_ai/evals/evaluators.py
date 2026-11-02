"""Evaluators for the agent workflow (SRS §13: accuracy, evidence relevance, policy citation, tool selection,
unsupported claims, recommendation quality). One metric per evaluator, as LangSmith expects.

Each takes (run, example) and handles both shapes LangSmith passes: RunTree/Example objects locally, dicts when
uploaded.
"""

import json
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

FRAUD_CLAIM = re.compile(r"\b(fraudulent|committed fraud|is fraud|was fraud|confirmed fraud)\b", re.IGNORECASE)
NEGATED = re.compile(r"\b(not|no|without|cannot|never)\b[^.]{0,40}\bfraud", re.IGNORECASE)


def _outputs(obj: Any) -> dict[str, Any]:
    outputs = obj.outputs if hasattr(obj, "outputs") else (obj or {}).get("outputs", {})
    return outputs or {}


def _not_graded(run) -> dict | None:
    """Quality metrics only grade runs that produced output; `completed` already counts the failures."""
    if not _outputs(run).get("completed"):
        return {"score": None, "comment": "no output to grade (run did not complete)"}
    return None


def completed(run, example) -> dict:
    out = _outputs(run)
    return {"score": 1 if out.get("completed") else 0, "comment": out.get("error") or "paused for approval"}


def resolution_acceptable(run, example) -> dict:
    if skipped := _not_graded(run):
        return skipped
    out, ref = _outputs(run), _outputs(example)
    chosen = out.get("resolution_type")
    ok = chosen in ref.get("acceptable_resolutions", [])
    return {"score": 1 if ok else 0, "comment": f"chose {chosen}; acceptable {ref.get('acceptable_resolutions')}"}


def grounded(run, example) -> dict:
    """Unsupported claims: anything the verification step had to drop or downgrade."""
    if skipped := _not_graded(run):
        return skipped
    rejected = _outputs(run).get("rejected_output") or []
    return {"score": 1 if not rejected else 0, "comment": "; ".join(rejected)[:500] or "all cited evidence verified"}


def cites_expected_policy(run, example) -> dict:
    if skipped := _not_graded(run):
        return skipped
    expected = _outputs(example).get("expected_policy")
    cited = _outputs(run).get("policies_cited") or []
    if not expected:
        return {"score": 1, "comment": "no policy governs this scenario"}
    return {"score": 1 if expected in cited else 0, "comment": f"expected {expected}; cited {cited}"}


def checked_engine_evidence(run, example) -> dict:
    """Tool selection: the investigation must look at the case's own records before explaining them."""
    if skipped := _not_graded(run):
        return skipped
    tools = set(_outputs(run).get("tools_used") or [])
    needed = {"get_case_transactions"}
    return {"score": 1 if needed <= tools else 0, "comment": f"used {sorted(tools)}"}


def read_playbook(run, example) -> dict:
    """Skills: the investigation read the playbook for this kind of case before concluding."""
    if skipped := _not_graded(run):
        return skipped
    expected = _outputs(example).get("expected_playbook")
    path = f"/skills/{expected}/SKILL.md"
    read = [t for t in _outputs(run).get("trajectory") or [] if t.get("tool") == "read_file" and t.get("ok")
            and t.get("agent") == "investigation" and t.get("path") == path]
    return {"score": 1 if read else 0, "comment": f"{'read' if read else 'did not read'} {path}"}


def no_fraud_determination(run, example) -> dict:
    if skipped := _not_graded(run):
        return skipped
    out = _outputs(run)
    texts = [f.get("statement", "") for f in out.get("findings", [])] + [out.get("summary") or "",
                                                                         out.get("rationale") or ""]
    offending = [t for t in texts if FRAUD_CLAIM.search(t) and not NEGATED.search(t)]
    return {"score": 0 if offending else 1, "comment": offending[0][:300] if offending else "no fraud determination"}


JUDGE_SYSTEM = (
    "You grade financial reconciliation investigations against a known root cause. The root cause was planted in "
    "synthetic data, so it is certain. Judge only whether the investigation identifies it: the explanation may be "
    "phrased differently, be appropriately hedged, or add plausible secondary possibilities, but its main "
    "explanation must match the root cause and must not contradict it."
)
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"reasoning": {"type": "string"}, "matches_root_cause": {"type": "boolean"}},
    "required": ["reasoning", "matches_root_cause"],
    "additionalProperties": False,
}


def judge_chat_model(model: str = "claude-opus-5-5") -> BaseChatModel:
    """Claude (the project's model) via LangChain, with the grade as structured output."""
    from langchain_anthropic import ChatAnthropic

    from finrecon_ai.agents.llm import FALLBACK_BETA
    from finrecon_ai.config import get_settings

    api_key = get_settings().anthropic_api_key
    return ChatAnthropic(model=model, max_tokens=4000, betas=[FALLBACK_BETA], model_kwargs={"fallbacks": "default"},
                         output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
                         **({"api_key": api_key} if api_key else {}))


def make_root_cause_judge(model: BaseChatModel):
    """LLM-as-judge: does the agents' explanation identify the planted root cause?"""

    def root_cause_identified(run, example) -> dict:
        if skipped := _not_graded(run):
            return skipped
        out, ref = _outputs(run), _outputs(example)
        investigation = {
            "findings": out.get("findings", []), "recommendation": out.get("resolution_type"),
            "summary": out.get("summary"), "rationale": out.get("rationale"),
        }
        response = model.invoke([
            SystemMessage(content=JUDGE_SYSTEM),
            HumanMessage(content=f"<root_cause>{ref.get('root_cause')}</root_cause>\n"
                                 f"<investigation>{json.dumps(investigation, ensure_ascii=False)}</investigation>"),
        ])
        if response.response_metadata.get("stop_reason") == "refusal":
            return {"score": None, "comment": "judge declined"}
        grade = json.loads(response.text)
        return {"score": 1 if grade["matches_root_cause"] else 0, "comment": grade["reasoning"]}

    return root_cause_identified


PAIRWISE_SYSTEM = (
    "You compare two recommended resolutions for the same financial reconciliation case, whose root cause is known. "
    "Prefer the one a financial operations manager could approve and act on with less follow-up: it identifies the "
    "root cause, proposes a resolution the evidence supports, cites the governing policy, gives concrete action "
    "steps, and is honest about what is still unknown. Length is not a merit."
)
PAIRWISE_SCHEMA = {
    "type": "object",
    "properties": {"reasoning": {"type": "string"}, "preferred": {"type": "string", "enum": ["A", "B", "tie"]}},
    "required": ["reasoning", "preferred"],
    "additionalProperties": False,
}


def pairwise_judge_model(model: str = "claude-opus-5-5") -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    from finrecon_ai.agents.llm import FALLBACK_BETA
    from finrecon_ai.config import get_settings

    api_key = get_settings().anthropic_api_key
    return ChatAnthropic(model=model, max_tokens=4000, betas=[FALLBACK_BETA], model_kwargs={"fallbacks": "default"},
                         output_config={"effort": "medium", "format": {"type": "json_schema", "schema": PAIRWISE_SCHEMA}},
                         **({"api_key": api_key} if api_key else {}))


def make_pairwise_judge(model: BaseChatModel):
    """Pairwise LLM-as-judge over two experiments' runs of one example (langsmith.evaluate with a tuple of
    experiments, randomize_order=True so position cannot be favoured). One score per run."""

    def recommendation_preference(runs: list, example) -> dict:
        outs = [_outputs(r) for r in runs]
        if not all(o.get("completed") for o in outs):
            return {"key": "recommendation_preference",
                    "scores": {r.id: (1 if o.get("completed") else 0) for r, o in zip(runs, outs, strict=True)}}
        shown = [{k: o.get(k) for k in ("resolution_type", "summary", "rationale", "action_steps",
                                        "policies_cited")} for o in outs]
        response = model.invoke([
            SystemMessage(content=PAIRWISE_SYSTEM),
            HumanMessage(content=f"<root_cause>{_outputs(example).get('root_cause')}</root_cause>\n"
                                 f"<A>{json.dumps(shown[0], ensure_ascii=False)}</A>\n"
                                 f"<B>{json.dumps(shown[1], ensure_ascii=False)}</B>"),
        ])
        verdict = json.loads(response.text)["preferred"]
        scores = {"A": (1, 0), "B": (0, 1), "tie": (0.5, 0.5)}[verdict]
        return {"key": "recommendation_preference",
                "scores": {runs[0].id: scores[0], runs[1].id: scores[1]}}

    return recommendation_preference


CODE_EVALUATORS = [completed, resolution_acceptable, grounded, cites_expected_policy, checked_engine_evidence,
                   read_playbook, no_fraud_determination]
