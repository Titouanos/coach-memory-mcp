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
# Le digest remontait UNIQUEMENT les courses A et B. Les courses de priorite C
# etaient donc invisibles pour qui se fiait a get_athlete_context — ce qui a
# reellement coute une erreur de planification le 29/07/2026 : le 5 km de Vannes
# du 12/09, enregistre en C le 20/07, a ete oublie dans la construction du bloc
# Londres, et il a fallu une decision de coaching (id=16) pour rattraper le
# coup et poser la regle « toujours appeler get_upcoming_races en plus ».
#
# Une regle qu'il faut se rappeler d'appliquer est une regle qui sera oubliee.
# On corrige donc la cause : le digest remonte TOUTES les courses a venir. Le
# cout est negligeable — 2 lignes de plus dans le cas reel — et chaque course
# porte deja sa `priority`, donc l'appelant garde toute latitude pour la
# ponderer. Une course inscrite au calendrier contraint la planification, quelle
# que soit sa priorite.
#
# `None` = aucun filtre de priorite (les annulees restent exclues par
# `upcoming`, ce qui est le comportement voulu).
_CONTEXT_RACE_PRIORITIES = None

# Week-plan digest stays small: everything except the big free-text fields.
_WEEK_PLAN_KEYS = ("id", "date", "sport", "type", "title", "status",
                   "target_load", "session_id", "block_label")


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def get_athlete_context() -> dict:
        """Return a compact digest of the athlete's current coaching state.

        One call yields: profile, hierarchical active goals (parent_id/horizon),
        ALL upcoming races whatever their priority — cancelled ones excluded —
        each carrying its own A/B/C priority, the current training week
        (planned workouts +
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
            # `raw` est EXCLU du digest, sans option pour l'inclure. Ce blob pese
            # environ 5 Ko par seance : le 2026-08-01, apres l'import des 52
            # semaines, il representait 56 % du digest (56 000 caracteres sur
            # 101 000) et faisait DEPASSER LA LIMITE DE JETONS de l'appel — donc
            # planter le tout premier outil qu'appelle une session de coaching.
            # Un digest doit rester un digest ; le detail se demande a la piece
            # via get_recent_sessions(include_raw=True).
            "recent_sessions": [
                s.model_dump(mode="json", exclude={"raw"}) for s in recent_sessions
            ],
            "active_decisions": [d.model_dump(mode="json") for d in active_decisions],
            "recent_analyses": [a.model_dump(mode="json") for a in recent_analyses],
        }
