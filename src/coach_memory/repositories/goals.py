"""Goals repository.

Goals form a tree via ``parent_id`` (e.g. long_term root → season → block).
Self-reference is blocked by a CHECK constraint; deeper cycles are blocked here
with a recursive ancestor walk, because a CHECK cannot traverse the tree.
"""

from __future__ import annotations

import datetime as dt

from psycopg import sql

from ..models import Goal
from .base import BaseRepository

_COLS = (
    "id, type, description, target_value, target_date, status, notes, "
    "parent_id, horizon, created_at"
)

# All ancestors of :candidate — if :goal_id appears among them, making
# :candidate the parent of :goal_id would close a cycle. UNION (not UNION ALL)
# so the walk terminates even if the table somehow already held a cycle.
_ANCESTOR_CYCLE_SQL = """
WITH RECURSIVE ancestors AS (
    SELECT id, parent_id FROM coach.goals WHERE id = %(candidate)s
    UNION
    SELECT g.id, g.parent_id
    FROM coach.goals g
    JOIN ancestors a ON g.id = a.parent_id
)
SELECT 1 FROM ancestors WHERE id = %(goal_id)s LIMIT 1
"""


class GoalRepository(BaseRepository):
    async def create(
        self,
        *,
        type: str,
        description: str,
        target_value: str | None = None,
        target_date: dt.date | None = None,
        notes: str | None = None,
        parent_id: int | None = None,
        horizon: str | None = None,
    ) -> Goal:
        async with self.pool.connection() as conn:
            if parent_id is not None:
                cur = await conn.execute(
                    "SELECT 1 FROM coach.goals WHERE id = %s", (parent_id,)
                )
                if await cur.fetchone() is None:
                    raise LookupError(f"parent goal {parent_id} not found")
            cur = await conn.execute(
                f"""
                INSERT INTO coach.goals
                    (type, description, target_value, target_date, notes, parent_id, horizon)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_COLS}
                """,
                (type, description, target_value, target_date, notes, parent_id, horizon),
            )
            row = await cur.fetchone()
        assert row is not None
        return Goal(**row)

    async def update(self, goal_id: int, fields: dict) -> Goal | None:
        """Partial update. `fields` keys are whitelisted upstream by GoalUpdate
        (extra='forbid'), so identifiers are trusted. Returns None if the goal
        does not exist. Raises ValueError on self-parenting or a cycle."""
        if not fields:
            raise ValueError("no fields provided to update")

        async with self.pool.connection() as conn:
            async with conn.transaction():
                new_parent = fields.get("parent_id")
                if new_parent is not None:
                    if new_parent == goal_id:
                        raise ValueError("a goal cannot be its own parent")
                    # Serialize tree mutations: under READ COMMITTED, two
                    # concurrent reciprocal re-parents could each pass the
                    # cycle check on their own snapshot and jointly commit a
                    # cycle (write-skew). The xact-scoped advisory lock makes
                    # check+update atomic across connections; the second
                    # transaction then sees the first one's committed parent
                    # and its cycle check correctly rejects.
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext('coach.goals.tree'))"
                    )
                    cur = await conn.execute(
                        "SELECT 1 FROM coach.goals WHERE id = %s", (new_parent,)
                    )
                    if await cur.fetchone() is None:
                        raise LookupError(f"parent goal {new_parent} not found")
                    cur = await conn.execute(
                        _ANCESTOR_CYCLE_SQL,
                        {"candidate": new_parent, "goal_id": goal_id},
                    )
                    if await cur.fetchone() is not None:
                        raise ValueError(
                            f"linking to parent {new_parent} would create a cycle"
                        )

                columns = list(fields.keys())
                query = sql.SQL(
                    "UPDATE coach.goals SET {assignments} WHERE id = {gid} RETURNING {ret}"
                ).format(
                    assignments=sql.SQL(", ").join(
                        sql.SQL("{c} = {p}").format(
                            c=sql.Identifier(c), p=sql.Placeholder(c)
                        )
                        for c in columns
                    ),
                    gid=sql.Placeholder("goal_id"),
                    ret=sql.SQL(_COLS),
                )
                cur = await conn.execute(query, {**fields, "goal_id": goal_id})
                row = await cur.fetchone()
        return Goal(**row) if row else None

    async def update_status(self, goal_id: int, status: str) -> Goal | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE coach.goals SET status = %s WHERE id = %s RETURNING {_COLS}",
                (status, goal_id),
            )
            row = await cur.fetchone()
        return Goal(**row) if row else None

    async def list(self, status: str | None = None) -> list[Goal]:
        clause = "WHERE status = %s" if status else ""
        params = (status,) if status else ()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.goals {clause} "
                "ORDER BY target_date ASC NULLS LAST, id DESC",
                params,
            )
            rows = await cur.fetchall()
        return [Goal(**r) for r in rows]
