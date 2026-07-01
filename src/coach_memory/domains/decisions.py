"""decisions domain — the anti-staleness core.

A new decision can supersede an older one; the old one is flipped to inactive so
it stays in history but stops surfacing in the active coaching context.
"""

from __future__ import annotations

import datetime as dt

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def log_coaching_decision(
        decision: str,
        rationale: str | None = None,
        context_snapshot: dict | None = None,
        supersedes_id: int | None = None,
        date: dt.date | None = None,
    ) -> dict:
        """Record a coaching decision (a training-plan adjustment, a rule, a call).

        rationale: why this decision was made.
        context_snapshot: the metrics that drove it, e.g.
            {"ctl": 62, "atl": 71, "tsb": -9, "hrv": 48}.
        supersedes_id: if this decision replaces an earlier one, pass its id — the
            earlier decision is deactivated (kept in history, hidden from context).
        date: ISO date; defaults to today.
        """
        try:
            decision_row = await repos.decisions.create(
                decision=decision,
                rationale=rationale,
                context_snapshot=context_snapshot,
                supersedes_id=supersedes_id,
                date=date or dt.date.today(),
            )
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        return decision_row.model_dump(mode="json")

    @mcp.tool
    async def get_active_decisions() -> list[dict]:
        """List currently-active coaching decisions (newest first).

        Superseded/retired decisions are excluded — this is the live guidance set.
        """
        decisions = await repos.decisions.active()
        return [d.model_dump(mode="json") for d in decisions]
