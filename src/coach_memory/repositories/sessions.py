"""Training-session repository."""

from __future__ import annotations

import datetime as dt

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ..models import Session
from .base import BaseRepository

_COLS = (
    "id, date, sport, type, planned, executed, rpe, felt, garmin_activity_id, "
    "raw, created_at"
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
        raw: dict | None = None,
    ) -> Session:
        try:
            async with self.pool.connection() as conn:
                cur = await conn.execute(
                    f"""
                    INSERT INTO coach.sessions
                        (date, sport, type, planned, executed, rpe, felt,
                         garmin_activity_id, raw)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_COLS}
                    """,
                    (date, sport, type, planned, executed, rpe, felt,
                     garmin_activity_id, Jsonb(raw) if raw is not None else None),
                )
                row = await cur.fetchone()
        except UniqueViolation as exc:
            raise ValueError(
                f"a session for garmin_activity_id {garmin_activity_id!r} already "
                "exists (sessions are unique per Garmin activity)"
            ) from exc
        assert row is not None
        return Session(**row)

    async def existing_garmin_ids(self, ids: list[str]) -> set[str]:
        """Sous-ensemble de `ids` deja present en base.

        Une seule requete pour tout un lot, la ou find_by_garmin_id ferait un
        aller-retour par activite. Sur une reprise de 250 activites, c'est la
        difference entre 250 requetes et une.
        """
        if not ids:
            return set()
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT garmin_activity_id FROM coach.sessions "
                "WHERE garmin_activity_id = ANY(%s)",
                (ids,),
            )
            rows = await cur.fetchall()
        return {r["garmin_activity_id"] for r in rows}

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
