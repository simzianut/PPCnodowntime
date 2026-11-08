"""Access key for hosted runs (decision 0087): login cookie, checked on /api and /ws."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from http.cookies import SimpleCookie

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger("uvicorn.error")

COOKIE = "ppc_access"
MAX_AGE_S = 30 * 24 * 3600
OPEN_PATHS = ("/api/health", "/api/auth/")


@dataclass(frozen=True)
class AccessKey:
    key: str | None  # None = auth off

    @property
    def required(self) -> bool:
        return self.key is not None

    @property
    def token(self) -> str:
        # The cookie carries an HMAC of the key, so the key itself never sits in the browser.
        return hmac.new(self.key.encode(), b"ppc-access-cookie-v1", hashlib.sha256).hexdigest()

    def key_ok(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate.encode(), self.key.encode())

    def token_ok(self, candidate: str | None) -> bool:
        return bool(candidate) and secrets.compare_digest(candidate.encode(), self.token.encode())


def access_key_from_env() -> AccessKey:
    key = (os.environ.get("PPC_ACCESS_KEY") or "").strip()
    if key:
        log.info("access key: PPC_ACCESS_KEY from the environment")
        return AccessKey(key)
    if (os.environ.get("PPC_AUTH") or "off").strip().lower() not in ("on", "1", "true"):
        return AccessKey(None)
    key = secrets.token_urlsafe(32)
    log.warning("access key (generated, valid until the API restarts): %s", key)
    return AccessKey(key)


def _cookie_from_scope(scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            jar = SimpleCookie()
            jar.load(value.decode("latin-1"))
            if COOKIE in jar:
                return jar[COOKIE].value
    return None


class AccessKeyMiddleware:
    """Pure ASGI, so it covers HTTP and WebSocket handshakes alike."""

    def __init__(self, app, access: AccessKey):
        self.app = app
        self.access = access

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        guarded = (scope["type"] == "http" and path.startswith("/api/")) or (
            scope["type"] == "websocket" and path.startswith("/ws/")
        )
        if (
            not self.access.required
            or not guarded
            or path == OPEN_PATHS[0]
            or path.startswith(OPEN_PATHS[1])
            or self.access.token_ok(_cookie_from_scope(scope))
        ):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await JSONResponse({"detail": "access key required"}, status_code=401)(scope, receive, send)


class LoginBody(BaseModel):
    key: str


def build_auth_router(access: AccessKey) -> APIRouter:
    r = APIRouter()

    @r.get("/api/auth/status")
    async def status(request: Request):
        ok = not access.required or access.token_ok(request.cookies.get(COOKIE))
        return {"required": access.required, "authenticated": ok}

    @r.post("/api/auth/login")
    async def login(body: LoginBody, request: Request, response: Response):
        if not access.required:
            return {"authenticated": True}
        if not access.key_ok(body.key.strip()):
            return JSONResponse({"detail": "invalid access key"}, status_code=401)
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(
            COOKIE,
            access.token,
            max_age=MAX_AGE_S,
            httponly=True,
            samesite="strict",
            secure=secure,
            path="/",
        )
        return {"authenticated": True}

    @r.post("/api/auth/logout")
    async def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"authenticated": False}

    return r
