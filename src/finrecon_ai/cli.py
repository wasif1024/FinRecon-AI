"""Developer CLI: finrecon generate-data | seed-demo | ingest."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from finrecon_ai.db.enums import IngestionMethod, SourceType
from finrecon_ai.db.models import FinancialSource, Tenant
from finrecon_ai.db.session import new_session
from finrecon_ai.ingestion.mappings import DEFAULT_MAPPINGS
from finrecon_ai.ingestion.service import IngestionError, ingest_file
from finrecon_ai.synthetic.generator import BANK_FILE, GATEWAY_FILE, SETTLEMENT_FILE, generate, write_dataset

DEMO_TENANT_SLUG = "demo"
DEMO_SOURCES = {
    "bank": ("Demo Bank Statement", SourceType.BANK_STATEMENT, IngestionMethod.CSV, BANK_FILE),
    "gateway": ("PayGate Transactions", SourceType.PAYMENT_GATEWAY, IngestionMethod.JSON, GATEWAY_FILE),
    "settlement": ("PayGate Merchant Settlements", SourceType.MERCHANT_SETTLEMENT, IngestionMethod.CSV,
                   SETTLEMENT_FILE),
}


def seed_demo(session: Session) -> Tenant:
    """Idempotent: creates the demo tenant and its three sources if missing."""
    tenant = session.scalar(select(Tenant).where(Tenant.slug == DEMO_TENANT_SLUG))
    if tenant is None:
        tenant = Tenant(name="Demo Fintech Ltd", slug=DEMO_TENANT_SLUG)
        session.add(tenant)
        session.flush()
    for name, source_type, method, _ in DEMO_SOURCES.values():
        exists = session.scalar(
            select(FinancialSource).where(FinancialSource.tenant_id == tenant.id, FinancialSource.name == name)
        )
        if exists is None:
            session.add(FinancialSource(
                tenant_id=tenant.id,
                name=name,
                source_type=source_type,
                ingestion_method=method,
                config={"mapping": DEFAULT_MAPPINGS[source_type].model_dump(mode="json")},
            ))
    session.flush()
    return tenant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finrecon")
    commands = parser.add_subparsers(dest="command", required=True)

    gen = commands.add_parser("generate-data", help="write synthetic source files and a ground-truth manifest")
    gen.add_argument("--out", type=Path, default=Path("data/synthetic"))
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--payments", type=int, default=1000)

    commands.add_parser("seed-demo", help="create the demo tenant and its three financial sources")

    ing = commands.add_parser("ingest", help="import a file into a demo source")
    ing.add_argument("source", choices=[*DEMO_SOURCES, "all"])
    ing.add_argument("--dir", type=Path, default=Path("data/synthetic"), help="folder holding the synthetic files")
    ing.add_argument("--file", type=Path, help="explicit file (single source only)")

    args = parser.parse_args(argv)

    if args.command == "generate-data":
        dataset = generate(seed=args.seed, payments=args.payments)
        write_dataset(dataset, args.out)
        print(json.dumps(dataset.manifest["files"], indent=2))
        return 0

    with new_session() as session:
        tenant = seed_demo(session)
        if args.command == "seed-demo":
            session.commit()
            print(f"demo tenant ready: {tenant.id}")
            return 0

        keys = list(DEMO_SOURCES) if args.source == "all" else [args.source]
        if args.file and len(keys) > 1:
            parser.error("--file needs a single source")
        for key in keys:
            name, _, _, default_file = DEMO_SOURCES[key]
            path = args.file or args.dir / default_file
            source = session.scalar(
                select(FinancialSource).where(FinancialSource.tenant_id == tenant.id, FinancialSource.name == name)
            )
            assert source is not None
            try:
                result = ingest_file(session, tenant_id=tenant.id, source_id=source.id, file_name=path.name,
                                     content=path.read_bytes(), storage_uri=str(path.resolve()))
            except (IngestionError, OSError) as exc:
                session.rollback()
                print(f"{key}: {exc}", file=sys.stderr)
                return 1
            session.commit()
            b = result.batch
            note = "" if result.created else " (already imported, skipped)"
            print(f"{key}: {b.status} - {b.accepted_records} accepted, {b.rejected_records} rejected "
                  f"of {b.total_records}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
