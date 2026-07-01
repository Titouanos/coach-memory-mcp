"""context domain — the central digest tool.

`get_athlete_context` is the one call to make at the start of a coaching
conversation: it stitches together the whole active picture from every domain
in a single round-trip, so the model doesn't need to fire six separate reads.
"""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP

from ..repositories import Repositories

_RECENT_SESSIONS = 10
_RECENT_ANALYSES = 3
_CONTEXT_RACE_PRIORITIES = ["A", "B"]


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def get_athlete_context() -> dict:
        """Return a compact digest of the athlete's current coaching state.

        One call yields: profile, active goals, upcoming A/B races, the last 10
        sessions, active (non-superseded) coaching decisions, and the last 3
        analyses. Call this first in a coaching conversation to ground yourself
        before reasoning. Raw device data (HRV, CTL/ATL/TSB, activities) lives in
        the Garmin MCP — this is the coaching layer on top.
        """
        today = dt.date.today()
        profile = await repos.profile.get()
        active_goals = await repos.goals.list(status="active")
        upcoming_races = await repos.races.upcoming(
            on_or_after=today, priorities=_CONTEXT_RACE_PRIORITIES
        )
        recent_sessions = await repos.sessions.recent(_RECENT_SESSIONS)
        active_decisions = await repos.decisions.active()
        recent_analyses = await repos.analyses.recent(_RECENT_ANALYSES)

        return {
            "generated_at": today.isoformat(),
            "profile": profile.model_dump(mode="json") if profile else None,
            "active_goals": [g.model_dump(mode="json") for g in active_goals],
            "upcoming_races": [r.model_dump(mode="json") for r in upcoming_races],
            "recent_sessions": [s.model_dump(mode="json") for s in recent_sessions],
            "active_decisions": [d.model_dump(mode="json") for d in active_decisions],
            "recent_analyses": [a.model_dump(mode="json") for a in recent_analyses],
        }
