import type { Evidence } from "./types";

/**
 * Display labels for evidence that match what the agents wrote ("…required by policy (E4)"). Each agent run labels
 * its own evidence E1, E2…; when several runs reuse a label, later runs get a prefix (2·E1) to keep labels unique.
 */
export function evidenceLabels(evidence: Evidence[]): Map<string, string> {
  const runs = [...new Set(evidence.map((e) => e.agent_execution_id ?? "analyst"))];
  const counts = new Map<string, number>();
  for (const e of evidence) {
    const label = agentLabel(e);
    if (label) counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return new Map(evidence.map((e, i) => {
    const label = agentLabel(e);
    if (!label) return [e.id, `N${i + 1}`]; // analyst notes and unlabelled evidence
    const run = runs.indexOf(e.agent_execution_id ?? "analyst");
    return [e.id, (counts.get(label) ?? 0) > 1 && run > 0 ? `${run + 1}·${label}` : label];
  }));
}

function agentLabel(e: Evidence): string | undefined {
  const label = (e.source_reference as { label?: unknown }).label;
  return typeof label === "string" && label ? label : undefined;
}
