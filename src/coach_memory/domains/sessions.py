"""sessions domain — planned vs executed training with subjective load."""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP

from ..models import Rpe
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
        return session.model_dump(mode="json")

    @mcp.tool
    async def get_recent_sessions(limit: int = 10) -> list[dict]:
        """Return the most recent sessions (newest first). Default 10."""
        limit = max(1, min(limit, 200))
        sessions = await repos.sessions.recent(limit)
        return [s.model_dump(mode="json") for s in sessions]
