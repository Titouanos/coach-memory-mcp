"""Static Bearer-token authentication as pure ASGI middleware.

Custom connectors reach this server from Anthropic's cloud, so a static shared
secret (``MCP_AUTH_TOKEN``) is checked on every request. This is defence-in-depth
*inside* the network-level IP allowlist (see README → Security), not a substitute
for it.

Implemented as plain ASGI middleware rather than via FastMCP's auth providers so
it is independent of that evolving API and can cheaply exempt ``/healthz`` (which
the load balancer / Docker healthcheck must reach unauthenticated).
"""

from __future__ import annotations

import hmac
import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("coach_memory.auth")


class BearerAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        exempt_paths: tuple[str, ...] = ("/healthz",),
    ) -> None:
        self.app = app
        # Comparaison en BYTES de bout en bout, jamais en str.
        #
        # La version precedente faisait `.decode("latin-1")` sur l'en-tete puis
        # comparait deux str. Or `hmac.compare_digest` REFUSE les str non-ASCII
        # et leve TypeError : un seul octet non-ASCII dans l'en-tete
        # (`Authorization: \xff`) suffisait a produire un HTTP 500 avec traceback
        # au lieu d'un 401 — et surtout le `logger.warning` ci-dessous n'etait
        # jamais emis, donc la detection de bruteforce etait aveugle sur ce
        # chemin. Reproduit puis corrige le 28/07/2026.
        #
        # `compare_digest` sur bytes accepte n'importe quel octet et reste a
        # temps constant : on ne perd rien contre les attaques temporelles.
        self._expected = b"Bearer " + token.encode("utf-8")
        # Normalize trailing slashes so both "/healthz" and "/healthz/" are exempt
        # (routing-layer slash redirects happen AFTER this middleware).
        self._exempt = {p.rstrip("/") for p in exempt_paths}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Lifespan / websocket events pass straight through.
            await self.app(scope, receive, send)
            return

        if scope.get("path", "").rstrip("/") in self._exempt:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        # Reste en bytes : c'est ce que l'ASGI fournit, et ce que compare_digest
        # accepte sans condition d'encodage.
        provided = headers.get(b"authorization", b"")

        # constant-time compare avoids leaking token length/prefix via timing
        if not provided or not hmac.compare_digest(provided, self._expected):
            logger.warning(
                "rejected unauthenticated request",
                extra={"path": scope.get("path"), "has_header": bool(provided)},
            )
            response = JSONResponse(
                {"error": "unauthorized", "detail": "missing or invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
