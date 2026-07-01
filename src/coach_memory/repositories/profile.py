"""Athlete profile repository (singleton row)."""

from __future__ import annotations

from psycopg import sql
from psycopg.types.json import Jsonb

from ..models import AthleteProfile
from .base import BaseRepository

# Output columns (never expose the internal boolean singleton `id`).
_COLS = sql.SQL(
    "weight_kg, height_cm, vo2max_run, vo2max_bike, ftp, hr_max, "
    "lthr, threshold_pace, hr_zones, notes, updated_at"
)
_JSONB_FIELDS = {"hr_zones"}


class ProfileRepository(BaseRepository):
    async def get(self) -> AthleteProfile | None:
        query = sql.SQL("SELECT {cols} FROM coach.athlete_profile WHERE id = true").format(
            cols=_COLS
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(query)
            row = await cur.fetchone()
        return AthleteProfile(**row) if row else None

    async def upsert(self, fields: dict) -> AthleteProfile:
        """Partial upsert of the singleton. `fields` keys are already whitelisted
        by AthleteProfileUpdate (extra='forbid'), so identifiers are trusted."""
        if not fields:
            raise ValueError("no fields provided to update")

        columns = list(fields.keys())
        values = [
            Jsonb(fields[c]) if c in _JSONB_FIELDS and fields[c] is not None else fields[c]
            for c in columns
        ]
        ident = [sql.Identifier(c) for c in columns]
        query = sql.SQL(
            """
            INSERT INTO coach.athlete_profile (id, {cols}, updated_at)
            VALUES (true, {ph}, now())
            ON CONFLICT (id) DO UPDATE SET {assignments}, updated_at = now()
            RETURNING {ret}
            """
        ).format(
            cols=sql.SQL(", ").join(ident),
            ph=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            assignments=sql.SQL(", ").join(
                sql.SQL("{c} = EXCLUDED.{c}").format(c=i) for i in ident
            ),
            ret=_COLS,
        )
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, values)
            row = await cur.fetchone()
        assert row is not None  # RETURNING on an upsert always yields a row
        return AthleteProfile(**row)
