from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base
from tests.integration.conftest import alembic_config


def _diff(connection: Connection) -> list[object]:
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


async def test_migrations_match_models(migrated_postgres_url: str) -> None:
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    await engine.dispose()
    assert diff == []


def test_downgrade_and_upgrade_round_trip(migrated_postgres_url: str) -> None:
    config = alembic_config(migrated_postgres_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
