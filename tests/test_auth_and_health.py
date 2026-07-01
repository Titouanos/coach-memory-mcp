"""ASGI-level tests: bearer auth on /mcp and the unauthenticated /healthz."""

from __future__ import annotations

import httpx

from coach_memory.server import create_asgi_app

_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}


async def test_missing_token_rejected(settings):
    app = create_asgi_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        resp = await ac.post("/mcp", json=_INIT)
    assert resp.status_code == 401


async def test_bad_token_rejected(settings):
    app = create_asgi_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://t", headers={"Authorization": "Bearer nope"}
    ) as ac:
        resp = await ac.post("/mcp", json=_INIT)
    assert resp.status_code == 401


async def test_healthz_unauthenticated_ok(settings):
    app = create_asgi_app(settings)
    # /healthz is exempt from auth but needs the pool open → drive the lifespan.
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True


async def test_healthz_trailing_slash_is_exempt(settings):
    # A health check probing "/healthz/" must not be rejected by auth (it may be
    # 307-redirected by routing, but never 401).
    app = create_asgi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.get("/healthz/")
    assert resp.status_code != 401
