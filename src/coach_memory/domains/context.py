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
_METRICS_DAYS = 7
_CONTEXT_RACE_PRIORITIES = ["A", "B"]

# Week-plan digest stays small: everything except the big free-text fields.
_WEEK_PLAN_KEYS = ("id", "date", "sport", "type", "title", "status",
                   "target_load", "session_id", "block_label")


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def get_athlete_context() -> dict:
        """Return a compact digest of the athlete's current coaching state.

        One call yields: profile, hierarchical active goals (parent_id/horizon),
        upcoming A/B races, the current training week (planned workouts +
        status, without long descriptions), the last 7 days of daily metrics
        (hrv, sleep, readiness, tsb), the last 10 sessions, active coaching
        decisions, and the last 3 analyses. Call this first in a coaching
        conversation. Full workout descriptions live in get_week; full metrics
        in get_daily_metrics.
        """
        today = dt.date.today()
        week_start = today - dt.timedelta(days=today.weekday())
        profile = await repos.profile.get()
        active_goals = await repos.goals.list(status="active")
        upcoming_races = await repos.races.upcoming(
            on_or_after=today, priorities=_CONTEXT_RACE_PRIORITIES
        )
        week_plan = await repos.plan.week(week_start, week_start + dt.timedelta(days=6))
        metrics_7d = await repos.metrics.recent_compact(
            on_or_after=today - dt.timedelta(days=_METRICS_DAYS - 1)
        )
        recent_sessions = await repos.sessions.recent(_RECENT_SESSIONS)
        active_decisions = await repos.decisions.active()
        recent_analyses = await repos.analyses.recent(_RECENT_ANALYSES)

        return {
            "generated_at": today.isoformat(),
            "profile": profile.model_dump(mode="json") if profile else None,
            "active_goals": [g.model_dump(mode="json") for g in active_goals],
            "upcoming_races": [r.model_dump(mode="json") for r in upcoming_races],
            "current_week_plan": [
                {k: w.model_dump(mode="json")[k] for k in _WEEK_PLAN_KEYS}
                for w in week_plan
            ],
            "daily_metrics_7d": metrics_7d,
            "recent_sessions": [s.model_dump(mode="json") for s in recent_sessions],
            "active_decisions": [d.model_dump(mode="json") for d in active_decisions],
            "recent_analyses": [a.model_dump(mode="json") for a in recent_analyses],
        }
