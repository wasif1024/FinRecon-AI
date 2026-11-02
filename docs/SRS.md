# FinRecon AI
## Software Requirements Specification (SRS)

**Version:** 1.5  
**Date:** November 2026  
**Project:** FinRecon AI – Agentic Financial Reconciliation & Investigation Platform  
**Status:** Revised Requirements Baseline

---

## Revision History

| Version | Date | Changes |
|---|---|---|
| 1.0 | November 2026 | Initial requirements baseline. |
| 1.1 | November 2026 | Agent orchestration confirmed as LangGraph on Python. Redis removed from the stack. Claude API confirmed as the sole LLM provider. |
| 1.2 | November 2026 | Agent stack made explicit: LLM agents are built with LangChain Deep Agents on LangChain (ChatAnthropic, LangChain tools) inside the LangGraph workflow; LangSmith is required for tracing, monitoring and evaluation (§7, §8, §9, §13). |
| 1.3 | November 2026 | Deep Agents standards (§7): one LangSmith sandbox per case as the agents' file workspace (no code execution), an operating-manual memory and per-category playbook skills, smaller models for narrow subagents, coordinator agents on the LangGraph Agent Server (operations assistant with a background batch investigator), and recovery of interrupted runs (§8). |
| 1.4 | November 2026 | Communication made specific: the case work (identity, cases, investigation workflow, approvals) is a Connect RPC / gRPC contract in protobuf, managed with Buf, used by the dashboard's server; REST remains for external clients, file transfer and the remaining pages. |
| 1.5 | November 2026 | Deployment of agent runs decided (§7, §9): production runs every case workflow inside the API process, made durable by PostgreSQL checkpoints, startup recovery of interrupted runs and one run per case at a time. The LangGraph Agent Server (LangSmith Studio, coordinator agents, evaluation runs) is a development tool and is not deployed, so neither Redis nor an Agent Server licence is required. |

---

# 1. Executive Summary

FinRecon AI is an Agentic AI-powered financial operations platform designed to automate and assist financial reconciliation, transaction investigation, anomaly detection, and exception management.

The platform will ingest financial records from multiple sources, identify matching and unmatched transactions, investigate discrepancies, retrieve supporting evidence, and generate explainable resolution recommendations.

The platform will use specialized AI agents built with LangChain Deep Agents and coordinated through LangGraph (Python), with deterministic financial processing services enforcing transaction integrity and business rules. LangSmith provides tracing, monitoring and evaluation of every agent.

All financially significant operations will require explicit authorization and human approval.

The initial implementation will use synthetic financial datasets and simulated banking integrations. No real banking credentials or customer financial information will be required.

# 2. Business Problem

Financial institutions and fintech companies receive transaction records from multiple systems, including:

- Banking transaction systems.
- Payment processors.
- Merchant settlement platforms.
- Invoicing systems.
- Financial statements and supporting documents.

Differences between these systems create reconciliation exceptions.

Common problems include:

- Missing transactions.
- Duplicate transactions.
- Incorrect settlement amounts.
- Processing fee discrepancies.
- Delayed settlements.
- Incorrect transaction references.
- Missing supporting documentation.
- Unexplained financial adjustments.

Financial operations teams investigate these exceptions manually, consuming operational resources and increasing resolution time.

FinRecon AI aims to reduce manual investigation effort by providing intelligent exception detection, evidence collection, investigation assistance, and controlled resolution workflows.

# 3. Project Objectives

The primary objectives are:

1. Automate ingestion and normalization of financial records.
2. Implement deterministic transaction matching and reconciliation.
3. Identify financial discrepancies and anomalies.
4. Use AI agents to investigate complex exceptions.
5. Retrieve supporting financial policies and documentation.
6. Generate explainable investigation reports.
7. Introduce human-in-the-loop approval mechanisms.
8. Maintain complete and traceable audit records.
9. Evaluate agent accuracy, reliability, and operational performance.
10. Provide an enterprise-style financial operations dashboard.

# 4. Project Scope

## 4.1 In Scope – MVP

- CSV and JSON transaction ingestion.
- Multiple simulated financial data sources.
- Transaction normalization.
- Exact and tolerance-based transaction matching.
- Duplicate transaction detection.
- Missing transaction identification.
- Settlement discrepancy detection.
- Financial exception classification.
- AI-assisted exception investigation.
- Policy document ingestion and semantic retrieval.
- Human approval and rejection workflows.
- Investigation case management.
- Financial reconciliation reports.
- Agent execution tracing.
- Audit logging.
- Automated evaluation and testing.

## 4.2 Out of Scope – MVP

- Direct integration with real banking systems.
- Actual money transfers.
- Autonomous ledger adjustments.
- Automatic account freezing.
- Real customer onboarding.
- Regulatory reporting submissions.
- Production payment processing.
- Autonomous fraud determinations.
- Investment recommendations.

These capabilities may be considered in future releases, subject to appropriate security, legal and compliance reviews.

# 5. User Roles

## 5.1 Financial Operations Analyst

Responsibilities:
- Upload financial datasets.
- Initiate reconciliation jobs.
- Review unmatched transactions.
- Investigate exceptions.
- Request additional evidence.
- Prepare investigation cases.

## 5.2 Financial Operations Manager

Responsibilities:
- Review investigation reports.
- Approve or reject proposed resolutions.
- Assign cases.
- Monitor reconciliation performance.
- Review audit history.

## 5.3 System Administrator

Responsibilities:
- Manage user permissions.
- Configure data sources.
- Manage policy documents.
- Monitor system health.
- Configure agent execution policies.

## 5.4 Auditor

Responsibilities:
- Access approved audit records.
- Review investigation evidence.
- Examine approval history.
- Verify reconciliation decisions.

Auditors will have read-only access.

# 6. Functional Requirements

## FR-01: Financial Data Ingestion

The platform shall support importing financial records from:

- CSV files.
- JSON files.
- Simulated banking APIs.

The initial implementation shall support three independent data sources:

1. Bank statements.
2. Payment gateway transactions.
3. Merchant settlement records.

The system shall validate uploaded files and reject malformed records.

Every imported record shall maintain its original source reference.

## FR-02: Transaction Normalization

The platform shall normalize incoming transaction records into a common financial transaction model.

Normalized fields shall include:

- Transaction ID.
- Source system.
- External reference.
- Transaction date.
- Settlement date.
- Amount.
- Currency.
- Debit or credit indicator.
- Counterparty.
- Merchant.
- Transaction description.
- Transaction status.

The system shall preserve the original imported data for traceability.

## FR-03: Transaction Reconciliation

The platform shall implement deterministic matching between transactions from different financial sources.

Matching strategies shall include:

- Exact transaction reference matching.
- Exact amount and currency matching.
- Date-window matching.
- Counterparty matching.
- Configurable tolerance matching.

The reconciliation engine shall classify transactions as:

- Matched.
- Unmatched.
- Partially matched.
- Duplicate.
- Amount mismatch.
- Date mismatch.
- Currency mismatch.
- Pending investigation.

All matching decisions shall be reproducible and explainable.

An LLM shall not independently determine financial balances or create authoritative transaction matches.

## FR-04: Duplicate Detection

The system shall identify potential duplicate transactions using:

- Transaction references.
- Amount and currency.
- Counterparty information.
- Transaction timestamps.
- Source-system identifiers.

The system shall distinguish between confirmed duplicates and potential duplicates.

## FR-05: Exception Management

The platform shall create an investigation case for each qualifying reconciliation exception.

Each case shall contain:

- Case ID.
- Exception category.
- Related transaction IDs.
- Financial discrepancy amount.
- Source systems.
- Investigation status.
- Assigned analyst.
- Evidence references.
- Agent-generated findings.
- Recommended resolution.
- Approval history.

Supported case statuses:

- New.
- Investigating.
- Awaiting Evidence.
- Awaiting Approval.
- Approved.
- Rejected.
- Resolved.
- Closed.

## FR-06: AI Investigation

The platform shall use specialized agents to investigate reconciliation exceptions.

Agents shall retrieve relevant transaction records, historical cases, supporting documents, and applicable policies.

The investigation output shall contain:

- Summary of the discrepancy.
- Relevant transaction references.
- Identified evidence.
- Possible explanation.
- Confidence assessment.
- Recommended next action.
- Outstanding information requirements.

The system shall distinguish verified facts from AI-generated hypotheses.

## FR-07: Financial Policy Retrieval

The platform shall allow administrators to upload financial policies and investigation procedures.

Supported document formats:

- PDF.
- DOCX.
- TXT.
- Markdown.

Documents shall be parsed, chunked, embedded, and indexed.

Qdrant shall support semantic retrieval of relevant policy passages and investigation procedures.

Every policy-based recommendation shall include references to the retrieved source document and relevant section.

## FR-08: Anomaly Detection

The platform shall identify potential anomalies, including:

- Unusual transaction amounts.
- Unexpected settlement differences.
- Repeated transactions.
- Unusual transaction frequency.
- Unexpected counterparty activity.
- Historical pattern deviations.

The initial release shall use deterministic rules and statistical techniques.

AI agents may investigate flagged anomalies but shall not independently classify customers or transactions as fraudulent.

## FR-09: Human-in-the-Loop Approval

The system shall implement explicit approval checkpoints using LangGraph interrupts and persistent workflow state.

Supported decisions:

- Approve.
- Reject.
- Request additional evidence.
- Return for investigation.

The system shall record:

- Reviewer identity.
- Decision.
- Decision timestamp.
- Comments.
- Evidence considered.
- Original recommendation.
- Final action.

No financially significant resolution shall be executed without authorization.

## FR-10: Resolution Recommendations

The system shall generate proposed resolutions for investigation cases.

Examples:

- Confirm processing fee discrepancy.
- Request missing settlement documentation.
- Investigate a possible duplicate payment.
- Escalate an unresolved transaction mismatch.

Approved resolutions shall be recorded through deterministic application services.

The MVP shall not modify real financial accounts or execute actual financial transfers.

## FR-11: Reporting

The platform shall provide:

- Daily reconciliation summaries.
- Monthly reconciliation reports.
- Matched transaction statistics.
- Unmatched transaction reports.
- Outstanding exception reports.
- Duplicate transaction reports.
- Investigation resolution statistics.
- Financial discrepancy summaries.

Reports shall support CSV and PDF exports.

## FR-12: Audit Logging

The platform shall maintain audit records for:

- Data ingestion.
- Transaction matching.
- Exception creation.
- Agent execution.
- Tool invocations.
- Evidence retrieval.
- Approval decisions.
- Resolution execution.
- User access to sensitive operations.

Audit events shall be append-only at the application level, with restricted modification and deletion permissions.

# 7. Agentic AI Architecture Requirements

The platform shall use LangGraph for Python as the primary workflow orchestration framework.

The LLM-based agents (Investigation, Risk Analysis, Resolution) shall be implemented as LangChain Deep Agents (`deepagents`), each run as a step of the LangGraph workflow:

- **Model:** Claude through LangChain's `ChatAnthropic` (`langchain-anthropic`). Claude remains the sole LLM provider.
- **Tools:** case tools are LangChain tools, read-only, bound to one tenant and one case, and allow-listed per prompt version.
- **Planning and workspace:** the Deep Agents planning tool and file tools. Each case has one LangSmith sandbox as its agents' workspace, used only as a filesystem: working papers carry from one agent to the next and across investigation rounds. Agents shall not run commands or code in it; financial calculations stay in deterministic services. Sandboxes stop when idle and are deleted after a retention period.
- **Memory and skills:** a versioned, read-only operating manual (memory) loaded into every model call, and one versioned playbook (skill) per exception category, read only when relevant.
- **Models:** narrow research subagents may use a smaller Claude model than their parent; cost is recorded per model.
- **Subagents:** each agent may delegate focused research to subagents declared in its prompt version, each with its own tool allow-list. Shell execution and any general-purpose subagent that inherits every tool shall be disabled.
- **Structured output:** each agent returns a schema-validated report; cited identifiers are verified against what the tools returned before anything is stored.
- **Auditing:** every tool call, including subagents' and the harness's own tools, is recorded and audited (FR-12).

Coordinator agents on the LangGraph Agent Server (development only; see §9 Deployment): an operations assistant that reports case status and starts a background batch investigator (an asynchronous subagent), which starts one audited case workflow per case, capped per batch. Coordinators cannot investigate or decide cases themselves.

The Transaction Ingestion and Reconciliation responsibilities (7.1, 7.2) are performed by deterministic LangGraph workflow nodes; no LLM is involved in moving or matching financial records.

## 7.1 Transaction Ingestion Agent

Responsibilities:
- Identify uploaded data sources.
- Invoke ingestion tools.
- Validate imported records.
- Trigger normalization.
- Report ingestion errors.

Financial calculations shall remain in deterministic services.

## 7.2 Reconciliation Agent

Responsibilities:
- Invoke matching services.
- Examine unmatched records.
- Classify reconciliation exceptions.
- Identify relevant transaction groups.
- Initiate investigation workflows.

The agent shall not independently modify authoritative transaction records.

## 7.3 Investigation Agent

Responsibilities:
- Retrieve transaction history.
- Collect related financial evidence.
- Search historical investigation cases.
- Retrieve relevant financial policies.
- Develop possible explanations.
- Produce investigation reports.

## 7.4 Risk Analysis Agent

Responsibilities:
- Examine rule-based anomaly alerts.
- Retrieve historical patterns.
- Identify suspicious transaction relationships.
- Explain anomaly indicators.
- Recommend further investigation.

It shall not make autonomous fraud determinations.

## 7.5 Resolution Agent

Responsibilities:
- Consolidate investigation findings.
- Generate recommended resolutions.
- Prepare approval requests.
- Submit proposals to the human approval workflow.
- Record approved resolution outcomes.

It shall not bypass authorization controls.

# 8. Workflow Orchestration

LangGraph shall manage:

- Workflow state.
- Conditional routing.
- Agent delegation.
- Persistent checkpoints.
- Interrupt and resume operations.
- Retry policies.
- Failure handling.
- Background task execution, using LangGraph runs with PostgreSQL-backed checkpointing.
- Recovery: a run interrupted mid-way (process stopped, deployment) resumes from its last checkpoint without repeating completed steps.

A typical reconciliation workflow:

1. Receive ingestion request.
2. Import financial records.
3. Normalize transactions.
4. Execute deterministic reconciliation.
5. Identify exceptions.
6. Create investigation cases.
7. Invoke investigation and risk analysis agents.
8. Retrieve supporting evidence.
9. Generate recommended resolution.
10. Pause for human approval.
11. Execute approved application-level action.
12. Record the audit event.
13. Update case status.
14. Generate reconciliation report.

# 9. Technical Architecture

## Backend
- Python 3.x.
- FastAPI.
- LangChain (`langchain`, `langchain-anthropic`).
- LangChain Deep Agents (`deepagents`).
- LangGraph.
- Pydantic.
- SQLAlchemy.

## AI and Agent Infrastructure
- Claude API (Anthropic), through LangChain `ChatAnthropic`.
- LangChain Deep Agents for the LLM agents (planning, subagents, run-scoped scratch files).
- LangSmith for tracing, agent monitoring, datasets and evaluation experiments.
- LangGraph checkpointing.
- Structured tool interfaces.
- MCP integration where appropriate.

## Data Infrastructure
- PostgreSQL for authoritative financial records and LangGraph checkpoints.
- Qdrant for vector search.
- Local filesystem or object storage for supporting documents.

## Frontend
- React.
- TypeScript.
- Next.js.
- Financial operations dashboard.

## Communication
- REST APIs: the public API (OpenAPI), file uploads and downloads, and the read-only insight pages.
- Connect RPC / gRPC for internal service communication: the case work contract (`proto/finrecon/v1`, managed with
  Buf) between the dashboard's server and the API, served as Connect and gRPC on the API's port. Agents run out of
  process on the LangGraph Agent Server in development (Studio, evaluation), reached through its own API.

## Observability
- OpenTelemetry.
- LangSmith tracing.
- Structured application logs.
- Metrics and health checks.

## Deployment
- Docker.
- Docker Compose.
- GitHub Actions.
- Environment-based configuration.
- Agent runs in production execute inside the API process, not on a separately deployed Agent Server: each case
  workflow is checkpointed in PostgreSQL, interrupted runs are finished at startup, and an advisory lock allows one
  run per case. The LangGraph Agent Server is used in development only (Studio, coordinator agents, evaluation
  experiments); production does not require Redis or an Agent Server licence.

# 10. Data Model Requirements

The initial relational database shall include:

1. users
2. roles
3. financial_sources
4. import_batches
5. transactions
6. reconciliation_runs
7. transaction_matches
8. reconciliation_exceptions
9. investigation_cases
10. investigation_evidence
11. investigation_findings
12. resolution_recommendations
13. approval_requests
14. approval_decisions
15. audit_events
16. policy_documents
17. agent_execution_records

Financial amounts shall use fixed-precision decimal types, never floating-point arithmetic.

All monetary records shall retain their original currency.

The system shall not aggregate different currencies without an explicit and documented conversion process.

# 11. Security and Governance Requirements

The system shall implement:

- Authentication and role-based authorization.
- Tenant-level data isolation.
- Encryption in transit and at rest.
- Sensitive information redaction.
- Secure document processing.
- SQL injection protection.
- Prompt injection protection.
- Restricted agent tool permissions.
- Human approval for sensitive operations.
- Audit event integrity.
- Secret management.
- Fail-closed authorization.

Untrusted document contents shall never be treated as system instructions.

Agent-generated recommendations shall not override application-level security policies.

# 12. Non-Functional Requirements

## Reliability
- Failed workflows shall be recoverable.
- Retries shall not create duplicate financial operations.
- Workflow checkpoints shall support interruption and resumption.
- Financial operations shall be idempotent.

## Performance
Initial prototype targets:

- Import 1,000 synthetic transactions within 30 seconds under the defined development environment.
- Execute deterministic reconciliation within 10 seconds for a 1,000-record test dataset.
- Return standard dashboard queries within 2 seconds under normal development load.

LLM investigation latency shall be measured separately because it depends on external model providers.

## Scalability
- Support independent processing of multiple import batches.
- Support background investigation workflows.
- Separate agent orchestration from financial data services.
- Allow horizontal scaling of stateless application components.

## Maintainability
- Modular architecture.
- Type-safe interfaces.
- Automated testing.
- Clear separation of business logic and AI reasoning.
- Documented APIs.
- Versioned workflow definitions.

# 13. Testing and Evaluation

The project shall include:

## Unit Testing
- Transaction normalization.
- Matching algorithms.
- Financial calculations.
- Duplicate detection.
- Currency validation.
- Authorization checks.

## Integration Testing
- PostgreSQL integration.
- Qdrant retrieval.
- LangGraph workflow execution.
- Approval and resume operations.
- Document ingestion.

## Agent Evaluation
Agent evaluation shall use LangSmith datasets and experiments, with a dataset built from scenarios whose root cause is known, and shall cover:
- Investigation accuracy.
- Evidence retrieval relevance.
- Policy citation correctness.
- Tool selection accuracy.
- Unsupported claim detection.
- Resolution recommendation quality.

## Security Testing
- Unauthorized tool execution.
- Prompt injection.
- SQL injection.
- Cross-tenant data access.
- Approval bypass attempts.
- Malformed document uploads.

## Regression Testing
Every major agent or workflow change shall be evaluated against a controlled dataset of known financial scenarios.

# 14. MVP Acceptance Criteria

The MVP shall be considered complete when:

1. Three independent synthetic financial sources can be imported.
2. Records are normalized into a common transaction model.
3. Deterministic matching identifies exact matches.
4. Unmatched and mismatched transactions are detected.
5. Potential duplicate transactions are flagged.
6. Investigation cases are created automatically.
7. Investigation agents retrieve supporting transaction evidence.
8. Policy retrieval returns relevant source references.
9. Investigation reports distinguish facts from hypotheses.
10. Human reviewers can approve, reject, or request additional evidence.
11. Approval decisions are persisted.
12. Approved resolutions execute through deterministic services.
13. Audit history is available.
14. Dashboard reports reflect reconciliation outcomes.
15. Automated tests and agent evaluations pass defined acceptance thresholds.

# 15. Development Phases

## Phase 1 – Financial Data Foundation
- Project setup.
- Database schema.
- Synthetic dataset generation.
- CSV/JSON ingestion.
- Transaction normalization.
- Deterministic matching engine.

## Phase 2 – Exception Management
- Exception classification.
- Duplicate detection.
- Investigation case management.
- Financial operations dashboard.

## Phase 3 – Agentic AI
- LangGraph orchestration.
- Specialist agents built with LangChain Deep Agents.
- Tool integrations.
- Policy document ingestion.
- Qdrant semantic retrieval.
- Investigation report generation.

## Phase 4 – Governance
- Human-in-the-loop approval.
- Persistent checkpoints.
- Audit logging.
- Authorization controls.
- Security testing.

## Phase 5 – Evaluation and Deployment
- LangSmith tracing.
- Agent evaluation datasets.
- Regression tests.
- OpenTelemetry.
- Docker Compose deployment.
- GitHub Actions CI.
- Demonstration video and architecture documentation.

# 16. Final Deliverables

The GitHub repository shall contain:

- Complete source code.
- Architecture diagrams.
- Software requirements specification.
- Database ER diagram.
- Agent workflow diagrams.
- API documentation.
- Synthetic financial datasets.
- Docker Compose configuration.
- Automated tests.
- Agent evaluation reports.
- Security and governance documentation.
- Demo screenshots.
- A recorded end-to-end demonstration.

# 17. Project Success Definition

FinRecon AI will demonstrate the practical application of Agentic AI in financial operations.

The primary success criterion is not the number of agents or the complexity of the prompts.

It is the ability to reliably identify financial discrepancies, collect evidence, produce explainable findings, enforce deterministic financial controls, and support human-supervised resolution.

The platform must demonstrate that AI can improve financial investigation workflows without compromising financial integrity, authorization, or auditability.
