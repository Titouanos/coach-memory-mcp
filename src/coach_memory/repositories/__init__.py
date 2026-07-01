"""Repository container.

`Repositories` bundles one repository per domain plus the shared pool. Domain
modules receive this object in ``register(mcp, repos)`` and may use any repo —
which is how the transversal ``context`` and ``search`` tools compose data from
several tables.

The cross-table full-text search lives here (rather than in any single-table
repo) because it is transversal by nature; it queries the pool directly.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from ..models import SearchHit
from .analyses import AnalysisRepository
from .decisions import DecisionRepository
from .goals import GoalRepository
from .profile import ProfileRepository
from .races import RaceRepository
from .sessions import SessionRepository

# Full-text search across the three free-text tables. Uses one shared tsquery
# (built once in the CTE) and ranks all matches together. The generated
# search_vector columns + GIN indexes make this cheap. No vector DB, no LLM.
_SEARCH_SQL = """
WITH q AS (SELECT plainto_tsquery('english', %(query)s) AS tsq)
SELECT 'session' AS source, s.id AS id, s.date::text AS date,
       left(coalesce(s.executed, '') || ' ' || coalesce(s.felt, ''), 300) AS snippet,
       ts_rank(s.search_vector, q.tsq) AS rank
FROM coach.sessions s, q
WHERE s.search_vector @@ q.tsq
UNION ALL
SELECT 'decision', d.id, d.date::text,
       left(d.decision || ' — ' || coalesce(d.rationale, ''), 300),
       ts_rank(d.search_vector, q.tsq)
FROM coach.coaching_decisions d, q
WHERE d.search_vector @@ q.tsq
UNION ALL
SELECT 'analysis', a.id, a.date::text,
       left(a.topic || ': ' || a.summary, 300),
       ts_rank(a.search_vector, q.tsq)
FROM coach.analyses a, q
WHERE a.search_vector @@ q.tsq
ORDER BY rank DESC
LIMIT %(limit)s
"""


class Repositories:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool
        self.profile = ProfileRepository(pool)
        self.sessions = SessionRepository(pool)
        self.goals = GoalRepository(pool)
        self.races = RaceRepository(pool)
        self.decisions = DecisionRepository(pool)
        self.analyses = AnalysisRepository(pool)

    async def search_memory(self, query: str, limit: int = 20) -> list[SearchHit]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(_SEARCH_SQL, {"query": query, "limit": limit})
            rows = await cur.fetchall()
        return [SearchHit(**r) for r in rows]


__all__ = ["Repositories"]
