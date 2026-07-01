"""search domain — transversal full-text search over the coaching memory.

Postgres full-text search (generated tsvector columns + GIN indexes) across
sessions, coaching decisions, and analyses. No vector DB, no embeddings, no LLM.
"""

from __future__ import annotations

from fastmcp import FastMCP

from ..repositories import Repositories


def register(mcp: FastMCP, repos: Repositories) -> None:
    @mcp.tool
    async def search_memory(query: str, limit: int = 20) -> list[dict]:
        """Full-text search the coaching memory.

        Searches sessions (executed + felt), coaching decisions (decision +
        rationale), and analyses (topic + summary). Returns ranked hits, each
        with its source table, id, date and a text snippet — use the id with the
        matching get_* tool to pull the full record.
        """
        limit = max(1, min(limit, 100))
        hits = await repos.search_memory(query, limit)
        return [h.model_dump(mode="json") for h in hits]
