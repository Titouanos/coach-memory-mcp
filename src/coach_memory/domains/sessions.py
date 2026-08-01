"""sessions domain — planned vs executed training with subjective load."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from ..models import Rpe, SessionUpdate
from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def log_session(
        sport: str,
        date: dt.date | None = None,
        type: str | None = None,
        planned: str | None = None,
        executed: str | None = None,
        rpe: Rpe | None = None,
        felt: str | None = None,
        garmin_activity_id: str | None = None,
    ) -> dict:
        """Record a training session.

        sport: run | bike | swim | strength | ... (free-form).
        date: ISO date; defaults to today.
        type: easy | tempo | VO2 | long | brick | ... (free-form).
        planned/executed: what was prescribed vs what actually happened.
        rpe: rate of perceived exertion, 1-10. felt: subjective note.
        garmin_activity_id: optional link to the raw activity in the Garmin MCP.
        """
        try:
            session = await repos.sessions.create(
                date=date or dt.date.today(),
                sport=sport,
                type=type,
                planned=planned,
                executed=executed,
                rpe=rpe,
                felt=felt,
                garmin_activity_id=garmin_activity_id,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return session.model_dump(mode="json")

    @mcp.tool
    async def get_recent_sessions(
        limit: int = 10, include_raw: bool = False
    ) -> list[dict]:
        """Return the most recent sessions (newest first). Default 10.

        `include_raw` attache le payload Garmin brut de chaque seance : splits,
        cadence et FC par lap. A n'activer QUE sur un petit nombre de seances
        precises, jamais pour balayer un historique.

        Le blob pese environ 5 Ko par seance : a la limite de 200, l'activer
        represente ~1 Mo, bien au-dela de ce qu'un appel d'outil peut rendre.
        Il est donc exclu par defaut depuis le 2026-08-01, apres l'import de 52
        semaines qui a rendu ce champ non nul sur la totalite des seances — et
        qui, du meme coup, faisait depasser la limite de jetons au premier appel.
        """
        limit = max(1, min(limit, 200))
        sessions = await repos.sessions.recent(limit)
        exclure = set() if include_raw else {"raw"}
        return [s.model_dump(mode="json", exclude=exclure) for s in sessions]

    @mcp.tool
    async def update_session(session_id: int, fields: dict) -> dict:
        """Edit a session after the fact. Pass ONLY the fields to change —
        unspecified (and null) fields are untouched.

        Allowed fields: type, planned, executed, rpe (1-10), felt.
        Cas d'usage principal : ajouter le RPE et le ressenti une fois la
        seance deja ingeree depuis Garmin.

        date, sport, garmin_activity_id et raw ne sont volontairement PAS
        modifiables : les deux premiers identifient la seance, le troisieme
        est la cle de deduplication des imports, le dernier est le payload
        capteur brut.
        """
        try:
            update = SessionUpdate.model_validate(fields)
        except ValidationError as exc:
            raise ToolError(f"invalid session fields: {exc}") from exc
        data = update.model_dump(exclude_unset=True, exclude_none=True)
        if not data:
            raise ToolError("no updatable fields provided; pass at least one non-null field")
        session = await repos.sessions.update(session_id, data)
        if session is None:
            raise ToolError(f"session {session_id} not found")
        return session.model_dump(mode="json", exclude={"raw"})

    @mcp.tool
    async def delete_session(session_id: int, unlink_plan: bool = False) -> dict:
        """Delete a session (e.g. a duplicate import or a bad manual entry).

        Si la seance est LIEE a un ou plusieurs entrainements du plan, l'appel
        REFUSE par defaut et liste les workouts concernes — une seance liee est
        la preuve qu'un entrainement a ete fait. Passer unlink_plan=true pour
        detacher ces workouts (leur statut n'est pas touche) puis supprimer,
        dans la meme transaction.

        La suppression est definitive : le payload Garmin brut (raw) part avec
        la ligne. L'activite reste recuperable par un backfill ulterieur tant
        qu'elle existe cote Garmin.
        """
        try:
            result = await repos.sessions.delete(session_id, unlink_plan=unlink_plan)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if result is None:
            raise ToolError(f"session {session_id} not found")
        return result
