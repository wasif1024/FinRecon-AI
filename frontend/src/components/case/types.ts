import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type CaseDetail = S["CaseDetailOut"];
export type Finding = S["FindingOut"];
export type Evidence = S["EvidenceOut"];
export type Transaction = S["TransactionOut"];
export type Match = S["MatchOut"];
export type Recommendation = S["RecommendationOut"];
export type AuditEvent = S["AuditEventOut"];
export type Workflow = S["WorkflowOut"];
export type User = S["UserOut"];

/** Display names for user IDs, built once per page from GET /users. */
export type Names = Map<string, string>;
