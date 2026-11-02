from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import finrecon_ai.db.models  # noqa: F401  (registers all tables on Base.metadata)
from finrecon_ai.config import get_settings
from finrecon_ai.db.base import Base

config = context.config
if not config.get_main_option("sqlalchemy.url"):  # tests set their own URL
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    # Keep the application's loggers working when migrations run in-process (tests, app startup).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # LangGraph's PostgresSaver owns its checkpoint tables; don't let autogenerate drop them.
    if type_ == "table" and reflected and compare_to is None and name.startswith("checkpoint"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Migrations work across tenants: bypass row-level security for this connection only.
        connection.exec_driver_sql("SELECT set_config('app.rls_bypass', 'on', false)")
        connection.commit()  # session-level setting stays; Alembic must start its own transaction to commit
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
