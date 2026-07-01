"""Server assembly.

Wires the layers together and stays domain-agnostic: it opens the pool, runs
migrations, loops over ``DOMAIN_MODULES`` calling ``register(mcp, repos)``,
exposes an unauthenticated ``/healthz``, and serves streamable-HTTP under a
static-bearer ASGI middleware. Adding a domain never touches this file.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .auth import BearerAuthMiddleware
from .config import Settings, get_settings
from .db import create_pool, ping, run_migrations
from .domains import DOMAIN_MODULES
from .logging_setup import setup_logging
from .repositories import Repositories

logger = logging.getLogger("coach_memory.server")

_INSTRUCTIONS = (
    "Persistent long-term coaching memory for an endurance athlete. Stores the "
    "coaching layer — profile, goals, races, sessions (planned vs executed + "
    "felt/RPE), coaching decisions, and analyses — NOT raw device data (that "
    "lives in the Garmin MCP). Call get_athlete_context first to ground yourself."
)


def build_server(settings: Settings) -> tuple[FastMCP, object]:
    """Construct the FastMCP server and its (still-closed) connection pool.

    Returns ``(mcp, pool)``. The pool is opened and migrated by the server
    lifespan on startup and closed on shutdown.
    """
    pool = create_pool(settings)
    repos = Repositories(pool)

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        await pool.open()
        await pool.wait()  # fail fast if the DB is unreachable at startup
        applied = await run_migrations(pool, settings.migrations_dir)
        logger.info(
            "server ready",
            extra={"version": __version__, "migrations_applied": applied},
        )
        try:
            yield
        finally:
            await pool.close()
            logger.info("server stopped")

    mcp = FastMCP(
        name=settings.server_name,
        instructions=_INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
    )

    for module in DOMAIN_MODULES:
        module.register(mcp, repos)
    logger.info(
        "registered domains",
        extra={"domains": [m.__name__.rsplit(".", 1)[-1] for m in DOMAIN_MODULES]},
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> JSONResponse:
        # custom_route is intentionally unauthenticated and the bearer middleware
        # exempts /healthz, so a load balancer / Docker healthcheck can reach it.
        try:
            ok = await ping(pool)
        except Exception as exc:  # noqa: BLE001 — report any DB failure as unhealthy
            logger.warning("healthz db ping failed", extra={"error": str(exc)})
            ok = False
        return JSONResponse(
            {"status": "ok" if ok else "error", "db": ok},
            status_code=200 if ok else 503,
        )

    return mcp, pool


def create_asgi_app(settings: Settings | None = None):
    """Build the Starlette ASGI app (FastMCP streamable-HTTP + bearer auth).

    Usable directly by an ASGI server, e.g. ``uvicorn --factory
    coach_memory.server:create_asgi_app``. Tests pass an explicit ``settings``.
    """
    settings = settings or get_settings()
    mcp, _pool = build_server(settings)
    middleware = [Middleware(BearerAuthMiddleware, token=settings.mcp_auth_token)]
    return mcp.http_app(
        path=settings.mcp_path,
        transport="streamable-http",
        middleware=middleware,
    )


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    app = create_asgi_app()
    logger.info(
        "starting coach-memory",
        extra={"host": settings.host, "port": settings.port, "path": settings.mcp_path},
    )
    # log_config=None keeps our JSON formatter instead of uvicorn's defaults.
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
