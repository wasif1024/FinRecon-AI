# Security and governance

How FinRecon AI meets SRS §11. Each control names where it lives and which tests prove it.

## Identity and access

| Control | Implementation | Tests |
|---|---|---|
| Authentication | Tenant + email + password; Argon2 hashes (min. 12 chars); HS256 tokens, 60 min, identity only | `test_api.py` |
| Roles from the database, every request | Token holds user and tenant; roles reloaded per request, so deactivation and role changes apply to issued tokens | `test_api.py::test_inactive_user…`, `…role_change…` |
| No account enumeration | Same 401 for unknown tenant/user, inactive user, wrong password; a hash is always verified to equalise timing | `test_api.py::test_bad_logins_look_identical` |
| Account lockout | 5 failures in 15 min since the last success, counted from the append-only audit log: shared by all API processes and kept across restarts | `test_api.py::test_lockout_is_shared…` |
| Fail-closed authorization | Permissions granted per role; anything not granted is denied; the system actor cannot work or decide cases | `test_authz.py`, `test_cases.py` |
| User administration | Admins only (`user:manage`), own tenant only; changes apply to issued tokens; no self-deactivation, no removing your own admin role, never the last admin; audited without passwords | `test_user_admin.py` |
| Separation of duties | Whoever started an investigation cannot decide its recommendation; one immutable decision per request | `test_approvals.py` |
| Same rules over RPC | The Connect/gRPC services (`api/rpc.py`) authenticate the same bearer token and call the same functions as the REST endpoints, so roles, tenancy, validation, lockout and audit are shared, not re-implemented | `test_rpc.py` |

## Tenant isolation (three layers)

1. **Application:** every query and service is scoped by the actor's tenant; another tenant's resource returns the
   same 404 as a missing one.
2. **Database:** forced row-level security on every tenant table (migration 0005). A row is visible or writable only
   when its `tenant_id` equals the transaction's `app.tenant_id`, which the application sets when it loads the
   actor (`db/tenancy.py`) and re-applies after every commit. With no tenant set, queries return nothing and
   inserts are rejected; link tables follow their parent row. API tests run strictly scoped, and `test_tenancy.py`
   checks every tenant table.

3. **Database login:** the API connects as a role that owns nothing (`db/roles.py`, README "Database logins"). It
   can read and write rows, and cannot alter or drop tables, switch row-level security off, replace the security
   functions, or update or delete the append-only tables (privileges revoked as well as the trigger). The bypass
   setting counts only for the table owner (migration 0007), so even SQL injected into the API could not widen
   its view: setting `app.rls_bypass` does nothing for it. The application's two cross-tenant lookups (a case's
   tenant for resuming an approved workflow, interrupted runs for the recovery sweep) are `SECURITY DEFINER`
   functions that answer only those questions. The grant refuses superusers, `BYPASSRLS` roles and roles with the
   owner's rights. The API and RPC suites run every request as this role, and `tests/security/test_db_roles.py`
   attacks it directly.

**Remaining limit:** within its own tenant the application role can read and write what the application can; the
tenant setting is still written by the application, so injected SQL could choose another tenant's id. That is
prevented by parameterised queries (SQLAlchemy throughout) and by never building SQL from input.

## Auditability

- `audit_events` is append-only (trigger blocks UPDATE, DELETE, TRUNCATE) and hash-chained per tenant;
  `GET /audit-events/verify` recomputes the chain.
- Approval decisions are append-only too. Every request's `X-Request-ID` is stored on its audit events and in the
  application logs.

## AI agents

- Tools are read-only, bound to one tenant and one case, and allow-listed per prompt version.
- Agent output is verified before it is stored: cited IDs must come from that run's tool results; unbacked facts
  become marked hypotheses; amounts come from the matching engine. Nothing an agent produces executes without a
  human decision, and execution follows fixed plans that never move money.
- Prompts tell agents that tool output and documents are data, never instructions. Case data reaches the model only
  inside tool results; titles and reviewer comments are framed as data with their angle brackets neutralised.

## Data handling

- Secrets come from the environment (`.env`, never committed). The API refuses to start without a 32+ character
  `JWT_SECRET`.
- Application logs carry IDs and counts only; secret-looking values are masked. Unexpected API errors return a
  generic body with the request ID, never internals; over RPC they are `INTERNAL` with the message "internal
  error" (connect-python would otherwise send the exception text), and another tenant's case is `NOT_FOUND`, like
  a missing one (`test_rpc.py`).
- OpenTelemetry spans carry ids, routes, statuses, durations and counts: SQL statements with placeholders, never
  their values, and no request bodies or prompt text (`telemetry.py`, `test_telemetry.py`). It is off unless an
  OTLP endpoint is configured.
- Uploaded files are size- and type-checked before parsing; corrupt, encrypted and zip-bomb documents are refused.
- CSV report exports neutralise cells a spreadsheet would execute (leading `=`, `+`, `-`, `@`, tab, carriage
  return) with a leading quote, while genuine negative amounts stay numbers (`reports/service.py::safe_cell`,
  `test_reports.py`).
- The dashboard keeps the API token in an httpOnly cookie on the Next.js server; browser JavaScript never sees it,
  and downloads and live-progress polling go through server-side route handlers. Its Connect calls are made from
  that server too; the browser never calls the RPC services, so they need no CORS.

## Security test suite (`tests/security/`)

Run with `uv run pytest tests/security`. The scripted tests use a fake model that obeys every injection, to prove
the safeguards hold even when an agent is fully fooled.

| File | Attacks |
|---|---|
| `test_prompt_injection.py` | Instructions planted in a payment description, a policy document, a case title and a reviewer comment; fully fooled agents inventing approvals and policy |
| `test_approval_bypass.py` | Direct status changes by every role, wrong-channel transitions, forged and replayed resumes, decisions borrowed from another recommendation, superseded requests, self-approval with every role |
| `test_tool_abuse.py` | Tools aimed at another case or tenant, SQL payloads in tool inputs, write attempts, oversized results |
| `test_api_attacks.py` | SQL payloads in query parameters, login and notes; every read and write endpoint with another tenant's IDs |
| `test_db_roles.py` | As the application's database role: altering tables, disabling RLS, dropping policies and triggers, replacing the bypass function, creating tables, editing `alembic_version`, rewriting the audit trail, and setting the RLS bypass to read other tenants |
| `test_hostile_uploads.py` | Deep-nested JSON, oversized CSV fields, NUL bytes, bad encodings, encrypted PDFs, DOCX zip bombs, path traversal in file names, oversized uploads |

`test_real_model_flags_injection` runs the real agents on a poisoned case and checks that the model reports the
planted instruction and is not steered by it. It calls Claude (about $0.50), so it runs only with
`FINRECON_LIVE_TESTS=1 uv run pytest -m live`.

### Found and fixed by this suite

- A deeply nested JSON file, a CSV field above the CSV module's size limit, and NUL characters (which PostgreSQL
  cannot store) crashed an import instead of failing it cleanly. They now fail the batch, or for an escaped NUL in
  one JSON record, reject only that record.
- A reviewer comment containing `</reviewer_comment>`, or a source ID inside a case title, could break out of the
  framing that marks it as data in an agent's message.

### Open items

- None from earlier phases: CSV formula escaping (5d) and the separate database role (6) are done.
