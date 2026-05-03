"""
Soniq Database Migration System

A proper, versioned migration system that preserves data and allows
for safe schema evolution without data loss.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

import asyncpg

from ...core.leadership import advisory_key

if TYPE_CHECKING:
    from ...plugin import MigrationSource
    from . import PostgresBackend

# Reused across concurrent migration runs. Parallel deploys (two CI nodes
# calling `soniq setup` at once) used to race on non-idempotent DDL or
# double-insert into `soniq_migrations`; this lock serializes them.
_MIGRATION_LOCK_KEY = advisory_key("soniq.migrations")

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """Raised when migration operations fail"""

    pass


class MigrationRunner:
    """Manages database schema migrations for Soniq.

    The runner is connection-agnostic: callers always pass a connection
    or a ``PostgresBackend`` explicitly. There is no global-pool
    fallback - migrations run against the connection that the caller
    already holds (Soniq's backend, in practice).

    Plugin migrations. Plugins ship their own ``NNNN_*.sql`` files
    inside their package and register the directory via
    ``app.migrations.register_source(path, prefix=...)``. Soniq passes
    those sources through ``plugin_sources`` here so the runner discovers
    them alongside core migrations and applies them under the same
    advisory-lock guard.
    """

    def __init__(
        self,
        migrations_dir: Optional[Path] = None,
        backend: Optional["PostgresBackend"] = None,
        plugin_sources: Optional[List["MigrationSource"]] = None,
    ):
        if migrations_dir is None:
            migrations_dir = Path(__file__).parent / "migrations"
        self.migrations_dir = migrations_dir
        self._backend = backend
        self._plugin_sources: List["MigrationSource"] = list(plugin_sources or [])

    async def ensure_migration_table(self, conn: asyncpg.Connection) -> None:
        """Create the migration tracking table if it doesn't exist"""
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS soniq_migrations (
                id SERIAL PRIMARY KEY,
                version VARCHAR(255) NOT NULL UNIQUE,
                name TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT NOW(),
                checksum TEXT
            )
        """
        )

        # Create index for faster lookups
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_soniq_migrations_version
            ON soniq_migrations (version)
        """
        )

    def discover_migrations(
        self, version_filter: Optional[str] = None
    ) -> List[Tuple[str, str, Path]]:
        """
        Discover all migration files in the core directory plus any
        registered plugin sources.

        Args:
            version_filter: Optional prefix string. When set, only
                migrations whose version starts with this prefix are
                returned. ``"000"`` selects core migrations
                ``0001``-``0009``; ``"0010"`` selects only the
                ``0010_*`` slice (scheduler).

        Returns:
            List of (version, name, file_path) tuples sorted by version

        Core migrations live in ``soniq/backends/postgres/migrations/``
        and use ``NNNN_*.sql`` filenames (versions ``0001``-``0099``).
        Plugin migrations live inside the plugin package and use a
        4-digit version derived from ``prefix + last-three-of-filename``
        so the per-plugin range stays contiguous in the sort.
        ``soniq-stripe`` with prefix ``"0100"`` and a file
        ``002_events.sql`` registers as version ``0100002``.
        """
        migrations: List[Tuple[str, str, Path]] = []

        if not self.migrations_dir.exists():
            logger.warning(
                f"Migrations directory does not exist: {self.migrations_dir}"
            )
        else:
            for file_path in self.migrations_dir.glob("*.sql"):
                entry = self._parse_migration_filename(file_path)
                if entry is not None:
                    migrations.append(entry)

        for source in self._plugin_sources:
            if not source.path.exists():
                logger.warning(
                    "Plugin migrations directory does not exist: %s", source.path
                )
                continue
            for file_path in source.path.glob("*.sql"):
                entry = self._parse_migration_filename(
                    file_path, version_prefix=source.prefix
                )
                if entry is not None:
                    migrations.append(entry)

        if version_filter is not None:
            migrations = [m for m in migrations if m[0].startswith(version_filter)]

        # Sort by version. Core ``0001`` and plugin ``0100002`` both sort
        # numerically; lexicographic also works because every version is
        # a left-padded digit string.
        migrations.sort(key=lambda x: x[0])
        return migrations

    @staticmethod
    def _parse_migration_filename(
        file_path: Path, version_prefix: Optional[str] = None
    ) -> Optional[Tuple[str, str, Path]]:
        """Parse ``NNN_name.sql`` into a ``(version, name, path)`` tuple.

        With ``version_prefix`` set (plugin source), the file's leading
        number is concatenated onto the prefix so plugin migrations
        keep a deterministic order distinct from core's.
        """
        filename = file_path.stem
        parts = filename.split("_", 1)
        if len(parts) < 2:
            logger.warning(
                "Skipping migration file with invalid name format: %s", filename
            )
            return None
        local_version, name = parts[0], parts[1]
        version = (
            f"{version_prefix}{local_version}" if version_prefix else local_version
        )
        return version, name, file_path

    async def get_applied_migrations(self, conn: asyncpg.Connection) -> List[str]:
        """Get list of migration versions that have already been applied"""
        await self.ensure_migration_table(conn)

        rows = await conn.fetch(
            """
            SELECT version FROM soniq_migrations ORDER BY version
        """
        )

        return [row["version"] for row in rows]

    async def apply_migration(
        self, conn: asyncpg.Connection, version: str, name: str, file_path: Path
    ) -> None:
        """Apply a single migration within a transaction"""
        logger.info(f"Applying migration {version}: {name}")

        try:
            # Read migration SQL
            with open(file_path, "r", encoding="utf-8") as f:
                migration_sql = f.read()

            # Apply migration in a transaction
            async with conn.transaction():
                # Execute the migration SQL
                await conn.execute(migration_sql)

                # Record the migration as applied
                await conn.execute(
                    """
                    INSERT INTO soniq_migrations (version, name, applied_at)
                    VALUES ($1, $2, NOW())
                """,
                    version,
                    name,
                )

            logger.info(f"Successfully applied migration {version}: {name}")

        except Exception as e:
            logger.error(f"Failed to apply migration {version}: {name} - {e}")
            raise MigrationError(f"Migration {version} failed: {e}") from e

    async def run_migrations(
        self,
        conn: asyncpg.Connection = None,
        version_filter: Optional[str] = None,
    ) -> int:
        """
        Run all pending migrations.

        Args:
            conn: Optional connection. Falls back to the runner's
                configured backend pool.
            version_filter: Optional prefix-match on the 4-digit version.
                ``version_filter="0010"`` applies only ``0010_*``;
                ``version_filter=None`` applies everything. Used by
                feature-scoped setup() calls so a deployment that does
                not use a feature does not get its tables.

        Returns:
            Number of migrations applied
        """
        if conn is None:
            if self._backend is None:
                raise MigrationError(
                    "MigrationRunner has no backend; pass conn= or construct "
                    "MigrationRunner(backend=...) explicitly."
                )
            async with self._backend.acquire() as conn:
                return await self._run_migrations_with_connection(
                    conn, version_filter=version_filter
                )
        else:
            return await self._run_migrations_with_connection(
                conn, version_filter=version_filter
            )

    async def _run_migrations_with_connection(
        self,
        conn: asyncpg.Connection,
        version_filter: Optional[str] = None,
    ) -> int:
        """Internal method to run migrations with a provided connection.

        Holds a session-scoped `pg_advisory_lock` for the whole run so two
        deploy nodes calling `soniq setup` at the same time do not race
        on non-idempotent DDL or on inserts into `soniq_migrations`. The
        losing node waits, then re-reads the applied set and typically
        becomes a no-op.
        """
        logger.info("Starting database migration process")

        # Acquire the serializing advisory lock before any migration-table
        # interaction. pg_advisory_lock blocks until the other holder
        # releases, so both callers eventually make progress.
        await conn.fetchval("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_KEY)
        try:
            # Ensure migration tracking table exists
            await self.ensure_migration_table(conn)

            # Discover available migrations (optionally narrowed to a slice)
            available_migrations = self.discover_migrations(
                version_filter=version_filter
            )
            if not available_migrations:
                logger.info("No migration files found")
                return 0

            # Re-read applied inside the lock so a concurrent winner's state
            # is visible.
            applied_migrations = await self.get_applied_migrations(conn)
            applied_set = set(applied_migrations)

            # Determine which migrations need to be applied
            pending_migrations = [
                (version, name, file_path)
                for version, name, file_path in available_migrations
                if version not in applied_set
            ]

            if not pending_migrations:
                logger.info("All migrations are up to date")
                return 0

            logger.info(f"Found {len(pending_migrations)} pending migrations")

            # Apply each pending migration
            applied_count = 0
            for version, name, file_path in pending_migrations:
                await self.apply_migration(conn, version, name, file_path)
                applied_count += 1

            logger.info(f"Successfully applied {applied_count} migrations")
            return applied_count
        finally:
            await conn.fetchval("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_KEY)

    async def get_migration_status(
        self,
        conn: asyncpg.Connection = None,
        version_filter: Optional[str] = None,
    ) -> dict:
        """
        Get current migration status.

        Returns:
            Dictionary with migration status information
        """
        if conn is None:
            if self._backend is None:
                raise MigrationError(
                    "MigrationRunner has no backend; pass conn= or construct "
                    "MigrationRunner(backend=...) explicitly."
                )
            async with self._backend.acquire() as conn:
                return await self._get_migration_status_with_connection(
                    conn, version_filter=version_filter
                )
        else:
            return await self._get_migration_status_with_connection(
                conn, version_filter=version_filter
            )

    async def _get_migration_status_with_connection(
        self,
        conn: asyncpg.Connection,
        version_filter: Optional[str] = None,
    ) -> dict:
        """Internal method to get migration status with a provided connection"""
        await self.ensure_migration_table(conn)

        available_migrations = self.discover_migrations(version_filter=version_filter)
        applied_migrations = await self.get_applied_migrations(conn)
        applied_set = set(applied_migrations)

        pending_migrations = [
            f"{version}_{name}"
            for version, name, _ in available_migrations
            if version not in applied_set
        ]

        return {
            "total_migrations": len(available_migrations),
            "applied_migrations": applied_migrations,
            "pending_migrations": pending_migrations,
            "is_up_to_date": len(pending_migrations) == 0,
        }


# Global migration runner instance
_migration_runner = MigrationRunner()


async def run_migrations(
    conn: asyncpg.Connection = None,
    version_filter: Optional[str] = None,
) -> int:
    """
    Run all pending database migrations.

    Args:
        conn: Optional database connection. If not provided, creates one.
        version_filter: Optional prefix-match on the 4-digit version.

    Returns:
        Number of migrations applied

    Raises:
        MigrationError: If any migration fails
    """
    return await _migration_runner.run_migrations(conn, version_filter=version_filter)


async def get_migration_status(
    conn: asyncpg.Connection = None,
    version_filter: Optional[str] = None,
) -> dict:
    """
    Get current database migration status.

    Args:
        conn: Optional database connection. If not provided, creates one.
        version_filter: Optional prefix-match on the 4-digit version.

    Returns:
        Dictionary with migration status information
    """
    return await _migration_runner.get_migration_status(
        conn, version_filter=version_filter
    )
