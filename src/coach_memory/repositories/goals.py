"""Goals repository."""

from __future__ import annotations

import datetime as dt

from ..models import Goal
from .base import BaseRepository

_COLS = "id, type, description, target_value, target_date, status, notes, created_at"


class GoalRepository(BaseRepository):
    async def create(
        self,
        *,
        type: str,
        description: str,
        target_value: str | None = None,
        target_date: dt.date | None = None,
        notes: str | None = None,
    ) -> Goal:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"""
                INSERT INTO coach.goals (type, description, target_value, target_date, notes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING {_COLS}
                """,
                (type, description, target_value, target_date, notes),
            )
            row = await cur.fetchone()
        assert row is not None
        return Goal(**row)

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
