"""Training-session repository."""

from __future__ import annotations

import datetime as dt

from psycopg import sql
from psycopg.errors import ForeignKeyViolation, UniqueViolation
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

    async def update(self, session_id: int, fields: dict) -> Session | None:
        """Partial edit. Keys whitelisted upstream by SessionUpdate."""
        if not fields:
            raise ValueError("no fields provided to update")
        async with self.pool.connection() as conn:
            query = sql.SQL(
                "UPDATE coach.sessions SET {assignments} "
                "WHERE id = {sid} RETURNING {ret}"
            ).format(
                assignments=sql.SQL(", ").join(
                    sql.SQL("{c} = {p}").format(c=sql.Identifier(c), p=sql.Placeholder(c))
                    for c in fields
                ),
                sid=sql.Placeholder("session_id"),
                ret=sql.SQL(_COLS),
            )
            cur = await conn.execute(query, {**fields, "session_id": session_id})
            row = await cur.fetchone()
        return Session(**row) if row else None

    async def delete(
        self, session_id: int, *, unlink_plan: bool = False
    ) -> dict | None:
        """Supprime une seance, en gerant le lien du plan EXPLICITEMENT.

        training_plan.session_id la reference en NO ACTION : supprimer une
        seance liee echouerait sur la contrainte. Deux postures possibles, et
        le choix appartient a l'appelant :

        * unlink_plan=False (defaut) : refuser, en LISTANT les workouts lies.
          C'est le garde-fou — une seance liee est generalement la preuve
          qu'un entrainement a ete fait, la supprimer par accident detruirait
          cette trace.
        * unlink_plan=True : detacher les workouts (session_id -> NULL, leur
          statut n'est PAS touche) puis supprimer, dans la meme transaction.

        Retourne {"deleted": ..., "unlinked_workout_ids": [...]} ou None si la
        seance n'existe pas.
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                if unlink_plan:
                    # RETURNING plutot que SELECT prealable : la liste renvoyee
                    # est celle des lignes REELLEMENT detachees, meme si un lien
                    # a ete cree entre-temps par une ingestion concurrente.
                    cur = await conn.execute(
                        "UPDATE coach.training_plan SET session_id = NULL "
                        "WHERE session_id = %s RETURNING id",
                        (session_id,),
                    )
                    linked = sorted(r["id"] for r in await cur.fetchall())
                else:
                    cur = await conn.execute(
                        "SELECT id FROM coach.training_plan WHERE session_id = %s "
                        "ORDER BY id",
                        (session_id,),
                    )
                    linked = [r["id"] for r in await cur.fetchall()]
                    if linked:
                        raise ValueError(
                            f"session {session_id} is linked to workout(s) "
                            f"{linked}: pass unlink_plan=true to detach them, or "
                            "re-point them with mark_workout first"
                        )
                try:
                    cur = await conn.execute(
                        "DELETE FROM coach.sessions WHERE id = %s RETURNING id",
                        (session_id,),
                    )
                except ForeignKeyViolation as exc:
                    # Course perdue contre une ingestion : un workout s'est lie
                    # a la seance ENTRE notre controle et le DELETE. Sans ce
                    # rattrapage, mask_error_details transformerait la violation
                    # en erreur opaque ; ici l'appelant recoit le meme message
                    # actionnable que le refus normal, et un simple retry voit
                    # le lien devenu visible.
                    raise ValueError(
                        f"session {session_id} got linked to a workout while the "
                        "delete was running (concurrent ingestion): retry, or "
                        "pass unlink_plan=true to detach links atomically"
                    ) from exc
                row = await cur.fetchone()
        if row is None:
            return None
        return {"deleted": session_id, "unlinked_workout_ids": linked}
