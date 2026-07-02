"""Training-session repository."""

from __future__ import annotations

import datetime as dt

from psycopg.errors import UniqueViolation

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
        try:
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
        except UniqueViolation as exc:
            raise ValueError(
                f"a session for garmin_activity_id {garmin_activity_id!r} already "
                "exists (sessions are unique per Garmin activity)"
            ) from exc
        assert row is not None
        return Session(**row)

    async def find_by_garmin_id(self, garmin_activity_id: str) -> Session | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.sessions WHERE garmin_activity_id = %s",
                (garmin_activity_id,),
            )
            row = await cur.fetchone()
        return Session(**row) if row else None

    async def recent(self, limit: int = 10) -> list[Session]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.sessions ORDER BY date DESC, id DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [Session(**r) for r in rows]
