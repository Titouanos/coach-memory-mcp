"""Database access layer: async connection pool + versioned migration runner.

Design notes
------------
* The pool is created *closed* (``open=False``) and opened during the server
  lifespan — psycopg deprecates opening the pool inside ``__init__``.
* Every pooled connection uses ``dict_row`` so repositories get ``dict`` rows.
* Migrations are plain ``.sql`` files named ``NNNN_description.sql``. The runner
  bootstraps the ``coach`` schema and the ``coach.schema_migrations`` ledger,
  then applies every file whose numeric version is not yet recorded, in order,
  each wrapped in its own transaction (atomic: the file and its ledger row
  commit together, or not at all).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import Settings

logger = logging.getLogger("coach_memory.db")

# repo layout: src/coach_memory/db.py -> parents[2] == repo root -> /migrations
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_MIGRATION_RE = re.compile(r"^(\d+)_.*\.sql$")


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Build the (still closed) async connection pool.

    Call ``await pool.open()`` before use — done for you in the server lifespan.
    """
    return AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        open=False,
        timeout=settings.db_connect_timeout,
        kwargs={"row_factory": dict_row, "autocommit": False},
    )


async def ping(pool: AsyncConnectionPool) -> bool:
    """Return True if the database answers a trivial query; used by /healthz."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1 AS ok")
        row = await cur.fetchone()
        return bool(row and row["ok"] == 1)


def _discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    """Return [(version, path), ...] sorted ascending by version.

    Raises on duplicate version numbers — an ambiguous history is a hard error,
    not something to silently pick a winner for.
    """
    found: dict[int, Path] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if not match:
            logger.warning("skipping non-migration file", extra={"file": path.name})
            continue
        version = int(match.group(1))
        if version in found:
            raise RuntimeError(
                f"duplicate migration version {version}: "
                f"{found[version].name} and {path.name}"
            )
        found[version] = path
    return sorted(found.items())


async def _bootstrap(pool: AsyncConnectionPool) -> None:
    """Idempotent infra bootstrap: the schema + the migration ledger itself.

    This is the ONLY place IF NOT EXISTS is acceptable — it manages bookkeeping,
    not domain tables (those come exclusively from ordered migration files).
    """
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("CREATE SCHEMA IF NOT EXISTS coach")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS coach.schema_migrations (
                    version    integer     PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )


async def _applied_versions(pool: AsyncConnectionPool) -> set[int]:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT version FROM coach.schema_migrations")
        rows = await cur.fetchall()
        return {row["version"] for row in rows}


async def run_migrations(
    pool: AsyncConnectionPool, migrations_dir: Path | str | None = None
) -> list[int]:
    """Apply all pending migrations in order. Returns the versions applied.

    Safe to call on every boot: already-recorded versions are skipped, so this
    is a no-op once the schema is current.
    """
    directory = Path(migrations_dir) if migrations_dir else DEFAULT_MIGRATIONS_DIR
    if not directory.is_dir():
        raise RuntimeError(f"migrations directory not found: {directory}")

    await _bootstrap(pool)
    applied = await _applied_versions(pool)
    pending = [(v, p) for v, p in _discover_migrations(directory) if v not in applied]

    if not pending:
        logger.info("schema up to date", extra={"current_version": max(applied, default=0)})
        return []

    done: list[int] = []
    for version, path in pending:
        sql = path.read_text(encoding="utf-8")
        logger.info("applying migration", extra={"version": version, "file": path.name})
        async with pool.connection() as conn:
            # One transaction per migration: the DDL and the ledger row are
            # committed together. psycopg sends multi-statement SQL fine as long
            # as no query parameters are used (true for every .sql file here).
            async with conn.transaction():
                await conn.execute(sql)  # type: ignore[arg-type]
                await conn.execute(
                    "INSERT INTO coach.schema_migrations (version) VALUES (%s)",
                    (version,),
                )
        done.append(version)

    logger.info("migrations complete", extra={"applied": done})
    return done
