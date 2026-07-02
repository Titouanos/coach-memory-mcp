"""Domain module registry.

Each domain module exposes ``register(mcp, repos)`` and appears once in
``DOMAIN_MODULES``. The server registers them in a loop — adding a new domain is
one import + one list entry here, with zero changes to server.py.

Order is cosmetic (it only affects tool-registration order in logs). `context`
is listed first because it is the tool a coaching conversation starts with.
"""

from __future__ import annotations

from types import ModuleType

from . import (
    analyses,
    context,
    decisions,
    goals,
    ingest,
    metrics,
    plan,
    profile,
    races,
    search,
    sessions,
)

DOMAIN_MODULES: list[ModuleType] = [
    context,
    profile,
    sessions,
    goals,
    races,
    decisions,
    analyses,
    metrics,
    plan,
    ingest,
    search,
]

__all__ = ["DOMAIN_MODULES"]
