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


async def test_non_ascii_authorization_header_yields_401_not_500(settings):
    """Non-regression : un octet non-ASCII dans Authorization renvoyait un 500.

    L'ancienne implementation decodait l'en-tete en latin-1 puis appelait
    hmac.compare_digest sur des str. compare_digest REFUSE les str non-ASCII et
    leve TypeError : `Authorization: \xff` produisait donc un HTTP 500 avec
    traceback au lieu d'un 401, et le logger.warning de detection de bruteforce
    n'etait jamais emis — le signal de detection etait aveugle sur ce chemin.

    On passe les en-tetes en octets bruts via l'ASGI : un client HTTP refuserait
    d'encoder un tel en-tete et le bug ne se reproduirait pas.
    """
    app = create_asgi_app(settings)

    envois: list[dict] = []

    async def send(message):
        envois.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    for brut in (b"\xff", "Bearer café".encode("utf-8"), b"Bearer \xc3"):
        envois.clear()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "server": ("t", 80),
            "client": ("1.2.3.4", 1234),
            "headers": [(b"host", b"t"), (b"authorization", brut)],
        }
        await app(scope, receive, send)
        debut = next(m for m in envois if m["type"] == "http.response.start")
        assert debut["status"] == 401, f"en-tete {brut!r} -> {debut['status']}, attendu 401"


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


async def test_auth_disabled_allows_unauthenticated_initialize(settings):
    # Network-only mode: no bearer needed; must NOT 401 and must NOT advertise
    # any auth challenge (which would make claude.ai attempt an OAuth flow).
    s = settings.model_copy(update={"auth_enabled": False})
    app = create_asgi_app(s)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.post(
                "/mcp", json=init, headers={"Accept": "application/json, text/event-stream"}
            )
    assert resp.status_code == 200
    assert "www-authenticate" not in resp.headers


async def test_healthz_trailing_slash_is_exempt(settings):
    # A health check probing "/healthz/" must not be rejected by auth (it may be
    # 307-redirected by routing, but never 401).
    app = create_asgi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            resp = await ac.get("/healthz/")
    assert resp.status_code != 401
