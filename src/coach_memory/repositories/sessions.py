"""Training-session repository."""

from __future__ import annotations

import datetime as dt

from ..models import Session
from .base import BaseRepository

_COLS = (
    "id, date, sport, type, planned, executed, rpe, felt, garmin_activity_id, created_at"
)


class SessionRepository(BaseRepository):
    async def create(
        self,
        *,
        date: dt.date,
        sport: str,
        type: str | None = None,
        planned: str | None = None,
        executed: str | None = None,
        rpe: int | None = None,
        felt: str | None = None,
        garmin_activity_id: str | None = None,
    ) -> Session:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"""
                INSERT INTO coach.sessions
                    (date, sport, type, planned, executed, rpe, felt, garmin_activity_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_COLS}
                """,
                (date, sport, type, planned, executed, rpe, felt, garmin_activity_id),
            )
            row = await cur.fetchone()
        assert row is not None
        return Session(**row)

    async def recent(self, limit: int = 10) -> list[Session]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.sessions ORDER BY date DESC, id DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [Session(**r) for r in rows]
