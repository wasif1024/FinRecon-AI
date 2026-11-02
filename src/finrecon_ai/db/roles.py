"""Database logins (SRS §11): the application connects as a role that owns nothing.

Two logins:
- the owner (MIGRATION_DATABASE_URL): owns the tables, runs migrations and LangGraph's checkpoint setup; the only
  login the row-level security bypass works for (migration 0007).
- the application (DATABASE_URL): reads and writes rows, nothing else. It cannot alter or drop tables, switch row
  level security off, read across tenants, or update and delete the append-only audit trail and decisions.

The application role is created once by a database administrator (`CREATE ROLE finrecon_app LOGIN PASSWORD ...`);
`grant_app_role` then gives it exactly these rights, including on tables created later.
"""

from dataclasses import dataclass

from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

APPEND_ONLY_TABLES = ("audit_events", "approval_decisions")


class RoleError(Exception):
    pass


@dataclass(frozen=True)
class LoginReport:
    user: str
    superuser: bool
    bypass_rls: bool
    owned_tables: int

    @property
    def safe_for_application(self) -> bool:
        return not (self.superuser or self.bypass_rls or self.owned_tables)


def login_of(url: str) -> str | None:
    return make_url(url).username


def report(connection: Connection) -> LoginReport:
    """What the connected login could do beyond reading and writing rows."""
    row = connection.execute(text("""
        SELECT current_user AS user, r.rolsuper, r.rolbypassrls,
               (SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user) AS owned
        FROM pg_roles r WHERE r.rolname = current_user""")).one()
    return LoginReport(row.user, row.rolsuper, row.rolbypassrls, row.owned)


def grant_app_role(connection: Connection, role: str) -> None:
    """Run as the owner. Idempotent: call after every migration (`finrecon db migrate` does)."""
    found = connection.execute(text("""
        SELECT rolsuper, rolbypassrls, pg_has_role(rolname, current_user, 'USAGE') AS owner_rights,
               rolname = current_user AS is_owner
        FROM pg_roles WHERE rolname = :role"""), {"role": role}).first()
    if found is None:
        raise RoleError(f"role {role!r} does not exist; a database administrator creates it once: "
                        f"CREATE ROLE {role} LOGIN PASSWORD '...'")
    if found.is_owner or found.owner_rights:
        raise RoleError(f"{role!r} is (or has the rights of) the table owner; the application needs its own role")
    if found.rolsuper or found.rolbypassrls:
        raise RoleError(f"{role!r} is a superuser or has BYPASSRLS, which ignores row-level security")

    quoted = connection.dialect.identifier_preparer.quote(role)
    database = connection.dialect.identifier_preparer.quote(connection.engine.url.database or "")
    statements = [
        f"GRANT CONNECT ON DATABASE {database} TO {quoted}",
        f"GRANT USAGE ON SCHEMA public TO {quoted}",
        f"REVOKE CREATE ON SCHEMA public FROM {quoted}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted}",
        f"REVOKE UPDATE, DELETE, TRUNCATE ON {', '.join(APPEND_ONLY_TABLES)} FROM {quoted}",
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM {quoted}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}",
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {quoted}",
        # Tables the owner creates later (migrations, LangGraph checkpoint migrations) get the same rights.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {quoted}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {quoted}",
    ]
    for statement in statements:
        connection.exec_driver_sql(statement)
