"""Training-plan repository (the "prévu" side of prévu/réalisé)."""

from __future__ import annotations

import datetime as dt

from psycopg import sql

from ..models import PlannedWorkout
from .base import BaseRepository

_COLS = (
    "id, date, sport, type, title, description, target_load, status, "
    "session_id, block_label, goal_id, created_at"
)
_INSERT = f"""
    INSERT INTO coach.training_plan
        (date, sport, type, title, description, target_load, block_label, goal_id)
    VALUES (%(date)s, %(sport)s, %(type)s, %(title)s, %(description)s,
            %(target_load)s, %(block_label)s, %(goal_id)s)
    RETURNING {_COLS}
"""


def _insert_params(w: dict) -> dict:
    return {
        "date": w["date"],
        "sport": w["sport"],
        "type": w.get("type"),
        "title": w["title"],
        "description": w.get("description"),
        "target_load": w.get("target_load"),
        "block_label": w.get("block_label"),
        "goal_id": w.get("goal_id"),
    }


class TrainingPlanRepository(BaseRepository):
    async def _ensure_goal_exists(self, conn, goal_id: int) -> None:
        cur = await conn.execute("SELECT 1 FROM coach.goals WHERE id = %s", (goal_id,))
        if await cur.fetchone() is None:
            raise LookupError(f"goal {goal_id} not found")

    async def create(self, workout: dict) -> PlannedWorkout:
        async with self.pool.connection() as conn:
            if workout.get("goal_id") is not None:
                await self._ensure_goal_exists(conn, workout["goal_id"])
            cur = await conn.execute(_INSERT, _insert_params(workout))
            row = await cur.fetchone()
        assert row is not None
        return PlannedWorkout(**row)

    async def create_many(self, workouts: list[dict]) -> list[PlannedWorkout]:
        """Insert a batch atomically — either the whole week lands, or nothing."""
        created: list[PlannedWorkout] = []
        async with self.pool.connection() as conn:
            async with conn.transaction():
                for goal_id in {w["goal_id"] for w in workouts if w.get("goal_id")}:
                    await self._ensure_goal_exists(conn, goal_id)
                for w in workouts:
                    cur = await conn.execute(_INSERT, _insert_params(w))
                    row = await cur.fetchone()
                    assert row is not None
                    created.append(PlannedWorkout(**row))
        return created

    async def week(self, start: dt.date, end: dt.date) -> list[PlannedWorkout]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM coach.training_plan "
                "WHERE date BETWEEN %s AND %s ORDER BY date, id",
                (start, end),
            )
            rows = await cur.fetchall()
        return [PlannedWorkout(**r) for r in rows]

    async def mark(
        self, workout_id: int, status: str, session_id: int | None = None
    ) -> PlannedWorkout | None:
        async with self.pool.connection() as conn:
            if session_id is not None:
                cur = await conn.execute(
                    "SELECT 1 FROM coach.sessions WHERE id = %s", (session_id,)
                )
                if await cur.fetchone() is None:
                    raise LookupError(f"session {session_id} not found")
            cur = await conn.execute(
                f"""
                UPDATE coach.training_plan
                SET status = %s, session_id = COALESCE(%s, session_id)
                WHERE id = %s
                RETURNING {_COLS}
                """,
                (status, session_id, workout_id),
            )
            row = await cur.fetchone()
        return PlannedWorkout(**row) if row else None

    async def update(self, workout_id: int, fields: dict) -> PlannedWorkout | None:
        """Partial edit. Keys whitelisted upstream by PlannedWorkoutUpdate."""
        if not fields:
            raise ValueError("no fields provided to update")
        async with self.pool.connection() as conn:
            if fields.get("goal_id") is not None:
                await self._ensure_goal_exists(conn, fields["goal_id"])
            query = sql.SQL(
                "UPDATE coach.training_plan SET {assignments} "
                "WHERE id = {wid} RETURNING {ret}"
            ).format(
                assignments=sql.SQL(", ").join(
                    sql.SQL("{c} = {p}").format(c=sql.Identifier(c), p=sql.Placeholder(c))
                    for c in fields
                ),
                wid=sql.Placeholder("workout_id"),
                ret=sql.SQL(_COLS),
            )
            cur = await conn.execute(query, {**fields, "workout_id": workout_id})
            row = await cur.fetchone()
        return PlannedWorkout(**row) if row else None

    async def find_planned_match(
        self, *, date: dt.date, sport: str
    ) -> PlannedWorkout | None:
        """Workout 'planned' du même sport le plus proche, fenêtre [J-1, J]
        (date exacte gagnante, puis id le plus petit). Sert à apparier
        prévu ↔ réalisé lors de l'ingestion.

        FENÊTRE RESSERRÉE le 29/07/2026. Elle allait jusqu'à J+1, ce qui laissait
        une activité marquer 'done' un entraînement planifié APRÈS elle — donc
        pas encore couru. Cas réel encouru : un run le 29/07 clôturait le tempo
        prescrit le 30/07.

        L'asymétrie des coûts tranche le compromis. `mark` écrit
        `session_id = COALESCE(%s, session_id)` : un lien erroné ne peut donc
        JAMAIS être effacé, un faux positif est un dégât permanent. À l'inverse
        un faux négatif se rattrape d'un `mark_workout` à la main.

        Ce qui reste couvert : prévu J, réalisé J+1 — le retard, le cas fréquent.
        Ce qui est sacrifié : prévu J+1, réalisé J — l'avance, précisément le
        vecteur de clôture prématurée.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT {_COLS} FROM coach.training_plan
                WHERE status = 'planned'
                  AND lower(sport) = lower(%(sport)s)
                  AND date BETWEEN %(d)s::date - 1 AND %(d)s::date
                ORDER BY abs(date - %(d)s::date), id
                LIMIT 1
                """,
                {"sport": sport, "d": date},
            )
            row = await cur.fetchone()
        return PlannedWorkout(**row) if row else None
