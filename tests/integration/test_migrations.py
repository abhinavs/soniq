import asyncpg
import pytest

from soniq.backends.postgres.migration_runner import MigrationRunner
from tests.db_utils import make_test_db_url, run_createdb, run_dropdb

MIGRATIONS_DB = "soniq_migrations_test"


@pytest.mark.asyncio
async def test_migrations_apply_and_idempotent():
    run_dropdb(MIGRATIONS_DB)
    run_createdb(MIGRATIONS_DB, check=True)

    db_url = make_test_db_url(MIGRATIONS_DB)

    try:
        pool = await asyncpg.create_pool(db_url)
        async with pool.acquire() as conn:
            runner = MigrationRunner()
            applied = await runner.run_migrations(conn)
            assert applied > 0

            applied_again = await runner.run_migrations(conn)
            assert applied_again == 0

            tables = await conn.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            table_names = {row["table_name"] for row in tables}
            assert "soniq_jobs" in table_names
            assert "soniq_migrations" in table_names
        await pool.close()
    finally:
        run_dropdb(MIGRATIONS_DB)
