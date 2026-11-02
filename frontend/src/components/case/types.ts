// The case work views render the RPC messages (proto/finrecon/v1) directly.
export type {
  AuditEvent, CaseDetail, Evidence, Finding, Match, Recommendation, Transaction, TransitionOption, Workflow,
} from "@/gen/finrecon/v1/cases_pb";
export type { PendingApproval } from "@/gen/finrecon/v1/approvals_pb";
export type { User } from "@/gen/finrecon/v1/identity_pb";

/** Display names for user IDs, built once per page from ListUsers. */
export type Names = Map<string, string>;
