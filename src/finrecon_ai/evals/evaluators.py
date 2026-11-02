"""Evaluators for the agent workflow (SRS §13: accuracy, evidence relevance, policy citation, tool selection,
unsupported claims, recommendation quality). One metric per evaluator, as LangSmith expects.

Each takes (run, example) and handles both shapes LangSmith passes: RunTree/Example objects locally, dicts when
uploaded.
"""

import json
import re
from typing import Any

from finrecon_ai.agents.llm import LLMClient

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


def make_root_cause_judge(llm: LLMClient, model: str = "claude-opus-5-5"):
    """LLM-as-judge: does the agents' explanation identify the planted root cause? Uses Claude, the project's model."""

    def root_cause_identified(run, example) -> dict:
        out, ref = _outputs(run), _outputs(example)
        if skipped := _not_graded(run):
            return skipped
        investigation = {
            "findings": out.get("findings", []), "recommendation": out.get("resolution_type"),
            "summary": out.get("summary"), "rationale": out.get("rationale"),
        }
        response = llm.create(
            model=model, max_tokens=4000, system=JUDGE_SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            messages=[{"role": "user", "content": (
                f"<root_cause>{ref.get('root_cause')}</root_cause>\n"
                f"<investigation>{json.dumps(investigation, ensure_ascii=False)}</investigation>")}],
        )
        if response.stop_reason == "refusal":
            return {"score": None, "comment": "judge declined"}
        grade = json.loads(next(b.text for b in response.content if b.type == "text"))
        return {"score": 1 if grade["matches_root_cause"] else 0, "comment": grade["reasoning"]}

    return root_cause_identified


CODE_EVALUATORS = [completed, resolution_acceptable, grounded, cites_expected_policy, checked_engine_evidence,
                   no_fraud_determination]
