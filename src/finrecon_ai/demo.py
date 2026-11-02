"""Demo snapshot: one tenant's data, exported and loaded exactly, so a demo database can be rebuilt at will.

    finrecon demo export --tenant demo --out demo/snapshot.tar.gz   # from a database that has the investigations
    finrecon demo load demo/snapshot.tar.gz                         # into an empty database (scripts/demo.sh)

The snapshot holds the tenant row, every tenant table's rows and the link tables (role assignments by role name),
LangGraph checkpoints of runs paused for approval (so approving in the demo resumes them), and the original
policy files. Rows travel as PostgreSQL COPY text, so every type (JSON, arrays, timestamps, checkpoint blobs)
round-trips exactly: ids, timestamps and the audit hash chain are unchanged. Password hashes are never exported;
set passwords after loading. Runs as the table owner (MIGRATION_DATABASE_URL), which is allowed across tenants.

Loading also re-indexes the policy files into Qdrant under the same document ids, so the chunk ids the agents
cited are found again, and refuses a snapshot taken at another schema revision.
"""

import hashlib
import io
import json
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath

import psycopg
from psycopg import sql

from finrecon_ai.log import get_logger

log = get_logger(__name__)

FORMAT = 1
# Load order (foreign keys). Rows within a table are ordered so a parent precedes its children.
TENANT_TABLES = [
    "users", "financial_sources", "import_batches", "transactions", "reconciliation_runs", "transaction_matches",
    "reconciliation_exceptions", "investigation_cases", "agent_execution_records", "policy_documents",
    "investigation_evidence", "investigation_findings", "resolution_recommendations", "approval_requests",
    "approval_decisions", "audit_events",
]
ROW_ORDER = {"agent_execution_records": "started_at, id", "audit_events": "id"}
LINK_TABLES = {  # table -> (parent table, column pointing at it)
    "transaction_match_items": ("transaction_matches", "match_id"),
    "exception_transactions": ("reconciliation_exceptions", "exception_id"),
}
CHECKPOINT_TABLES = ["checkpoints", "checkpoint_blobs", "checkpoint_writes"]
SECRET_COLUMNS = {"users": {"password_hash"}}


class SnapshotError(Exception):
    pass


@dataclass
class LoadResult:
    tenant_slug: str
    rows: dict[str, int]
    policies_indexed: int
    chain_intact: bool


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _columns(cur, table: str) -> list[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = %s ORDER BY ordinal_position", (table,))
    return [r[0] for r in cur.fetchall()]


def _copy_out(cur, query: sql.Composable) -> bytes:
    buffer = io.BytesIO()
    with cur.copy(sql.SQL("COPY ({}) TO STDOUT").format(query)) as copy:
        for data in copy:
            buffer.write(data)
    return buffer.getvalue()


def _select(table: str, columns: list[str], where: sql.Composable, order: str | None = None) -> sql.Composable:
    blanked = SECRET_COLUMNS.get(table, set())
    fields = sql.SQL(", ").join(sql.SQL("NULL") if c in blanked else sql.Identifier(c) for c in columns)
    query = sql.SQL("SELECT {} FROM {} WHERE {}").format(fields, sql.Identifier(table), where)
    return query + sql.SQL(f" ORDER BY {order}") if order else query


def export_tenant(owner_url: str, slug: str, out: Path, policy_dir: Path | None = None) -> dict[str, int]:
    """Write the tenant's snapshot to `out` (.tar.gz). Returns rows per table. A policy original that is no longer
    in storage is taken from `policy_dir` (the sample library) if a file there has the recorded SHA-256."""
    with psycopg.connect(_plain(owner_url)) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.rls_bypass', 'on', false)")
        cur.execute("SELECT id FROM tenants WHERE slug = %s", (slug,))
        found = cur.fetchone()
        if found is None:
            raise SnapshotError(f"no tenant {slug!r}")
        tenant_id = found[0]
        cur.execute("SELECT version_num FROM alembic_version")
        revision = cur.fetchone()[0]
        tenant = sql.SQL("tenant_id = {}").format(sql.Literal(tenant_id))

        files: dict[str, bytes] = {}
        manifest: dict = {"format": FORMAT, "revision": revision, "tenant_id": str(tenant_id), "slug": slug,
                          "tables": {}, "policy_files": {}}

        def add(name: str, columns: list[str], data: bytes) -> None:
            files[f"tables/{name}.copy"] = data
            manifest["tables"][name] = {"columns": columns, "rows": data.count(b"\n")}

        columns = _columns(cur, "tenants")
        add("tenants", columns, _copy_out(cur, _select("tenants", columns,
                                                        sql.SQL("id = {}").format(sql.Literal(tenant_id)))))
        for table in TENANT_TABLES:
            columns = _columns(cur, table)
            add(table, columns, _copy_out(cur, _select(table, columns, tenant, ROW_ORDER.get(table))))
        for table, (parent, column) in LINK_TABLES.items():
            columns = _columns(cur, table)
            where = sql.SQL("{} IN (SELECT id FROM {} WHERE {})").format(sql.Identifier(column),
                                                                         sql.Identifier(parent), tenant)
            add(table, columns, _copy_out(cur, _select(table, columns, where)))
        add("user_roles", ["user_id", "role_name"], _copy_out(cur, sql.SQL(
            "SELECT ur.user_id, r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "JOIN users u ON u.id = ur.user_id WHERE u.{}").format(tenant)))

        # Checkpoints of runs paused for approval only: an interrupted run is left out, so nothing is recovered
        # (and Claude called) when the demo API starts; that case can simply be investigated again.
        threads = sql.SQL("thread_id IN (SELECT 'case:' || id::text FROM investigation_cases "
                          "WHERE {} AND status = 'awaiting_approval')").format(tenant)
        for table in CHECKPOINT_TABLES:
            columns = _columns(cur, table)
            add(table, columns, _copy_out(cur, _select(table, columns, threads)))

        cur.execute("SELECT id, file_name, storage_uri, file_sha256 FROM policy_documents WHERE tenant_id = %s "
                    "AND status = 'indexed'", (tenant_id,))
        for document_id, file_name, storage_uri, sha256 in cur.fetchall():
            content = _policy_original(Path(storage_uri), policy_dir, file_name, sha256)
            member = f"policies/{document_id}{PurePath(file_name).suffix.lower()}"
            files[member] = content
            manifest["policy_files"][str(document_id)] = member

    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as archive:
        for name, data in {"manifest.json": json.dumps(manifest, indent=2).encode(), **files}.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 0  # reproducible archive
            archive.addfile(info, io.BytesIO(data))
    return {name: t["rows"] for name, t in manifest["tables"].items()}


def _policy_original(stored: Path, policy_dir: Path | None, file_name: str, sha256: str) -> bytes:
    for path in (stored, *([policy_dir / file_name] if policy_dir else [])):
        if path.is_file() and hashlib.sha256(content := path.read_bytes()).hexdigest() == sha256:
            return content
    raise SnapshotError(f"policy original not found: {file_name} (not in storage, nor with the same content in "
                        f"{policy_dir})")


def load_snapshot(owner_url: str, path: Path, *, storage_dir: Path, store) -> LoadResult:
    """Load into a database that has no tenant with the snapshot's id or slug. All or nothing."""
    with tarfile.open(path, "r:gz") as archive:
        def read(name: str) -> bytes:
            member = archive.extractfile(name)
            if member is None:
                raise SnapshotError(f"snapshot is missing {name}")
            return member.read()

        manifest = json.loads(read("manifest.json"))
        if manifest.get("format") != FORMAT:
            raise SnapshotError(f"unsupported snapshot format {manifest.get('format')!r}")
        tables = {name: read(f"tables/{name}.copy") for name in manifest["tables"]}
        policy_files = {doc: read(member) for doc, member in manifest["policy_files"].items()}

    tenant_id = uuid.UUID(manifest["tenant_id"])
    with psycopg.connect(_plain(owner_url)) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.rls_bypass', 'on', false)")
        cur.execute("SELECT version_num FROM alembic_version")
        revision = cur.fetchone()[0]
        if revision != manifest["revision"]:
            raise SnapshotError(f"snapshot is at schema revision {manifest['revision']}, the database at "
                                f"{revision}: run `finrecon db migrate` (or re-export the snapshot)")
        cur.execute("SELECT 1 FROM tenants WHERE id = %s OR slug = %s", (tenant_id, manifest["slug"]))
        if cur.fetchone():
            raise SnapshotError(f"tenant {manifest['slug']!r} already exists here; load into an empty database "
                                "(scripts/demo.sh recreates the Docker one)")

        for name in ["tenants", *TENANT_TABLES, *LINK_TABLES, *CHECKPOINT_TABLES]:
            # COPY cannot write to a table under row-level security: copy into a temporary table of the same
            # shape, then insert from it (the policies check every row, and the owner's bypass admits them).
            columns = sql.SQL(", ").join(map(sql.Identifier, manifest["tables"][name]["columns"]))
            staging = sql.Identifier(f"load_{name}")
            cur.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE {}) ON COMMIT DROP").format(staging,
                                                                                    sql.Identifier(name)))
            with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(staging, columns)) as copy:
                copy.write(tables[name])
            cur.execute(sql.SQL("INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM {}").format(
                sql.Identifier(name), columns, columns, staging))
        for line in tables["user_roles"].decode().splitlines():
            user_id, role = line.split("\t")
            cur.execute("INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE name = %s",
                        (user_id, role))
        cur.execute("SELECT setval(pg_get_serial_sequence('audit_events', 'id'), "
                    "(SELECT coalesce(max(id), 1) FROM audit_events))")

        indexed = 0
        for document_id, content in policy_files.items():
            cur.execute("SELECT file_name, file_sha256 FROM policy_documents WHERE id = %s", (document_id,))
            file_name, sha256 = cur.fetchone()
            stored = storage_dir / str(tenant_id) / f"{sha256}{PurePath(file_name).suffix.lower()}"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(content)
            cur.execute("UPDATE policy_documents SET storage_uri = %s WHERE id = %s", (str(stored), document_id))
            indexed += _reindex(cur, store, tenant_id, uuid.UUID(document_id), file_name, content)
        conn.commit()

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from finrecon_ai.audit import verify_chain

    engine = create_engine(owner_url)
    with Session(engine) as session:
        session.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
        intact = verify_chain(session, tenant_id) is None
    engine.dispose()
    rows = {name: t["rows"] for name, t in manifest["tables"].items()}
    log.info("demo.loaded", tenant=manifest["slug"], rows=sum(rows.values()), policies=indexed, chain_intact=intact)
    return LoadResult(manifest["slug"], rows, indexed, intact)


def _reindex(cur, store, tenant_id: uuid.UUID, document_id: uuid.UUID, file_name: str, content: bytes) -> int:
    """The same parsing and chunking as an upload, under the same document id: the same chunk ids."""
    from finrecon_ai.policies.chunking import chunk_sections
    from finrecon_ai.policies.parsing import parse_document

    cur.execute("SELECT title, version, chunk_count FROM policy_documents WHERE id = %s", (document_id,))
    title, version, chunk_count = cur.fetchone()
    chunks = chunk_sections(parse_document(file_name, content).sections)
    if len(chunks) != chunk_count:
        raise SnapshotError(f"{file_name}: {len(chunks)} chunks now, {chunk_count} when it was indexed; the cited "
                            "passages would not match")
    store.index(tenant_id=tenant_id, document_id=document_id, title=title, version=version, chunks=chunks)
    return 1
