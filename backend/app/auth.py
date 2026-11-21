"""Optional shared-password protection for a deployed DocuSight.

Set DOCUSIGHT_ACCESS_PASSWORD to require it; leave it unset for local use (everything stays open).
POST /api/auth/login exchanges the password for a signed, expiring token. Every other /api route then
needs that token, as an `Authorization: Bearer <token>` header or, for plain links the browser opens
itself (page images, file downloads, exports), a `token` query parameter. /api/health stays open for the
hosting platform's health check. Tokens are signed with DOCUSIGHT_AUTH_SECRET, or with a key derived from
the password when that is unset, so changing the password logs everyone out.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import config as _config  # noqa: F401  (loads .env before the settings below are read)

ACCESS_PASSWORD = os.getenv("DOCUSIGHT_ACCESS_PASSWORD", "")
AUTH_SECRET = os.getenv("DOCUSIGHT_AUTH_SECRET", "")
TOKEN_TTL = int(os.getenv("DOCUSIGHT_TOKEN_TTL_HOURS", "168")) * 3600   # 7 days
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/status"}
MAX_FAILURES, FAILURE_WINDOW = 5, 600   # per client address: 5 wrong passwords per 10 minutes

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
router = APIRouter(prefix="/api/auth")


def enabled() -> bool:
    return bool(ACCESS_PASSWORD)


def _key() -> bytes:
    return (AUTH_SECRET or "docusight:" + hashlib.sha256(ACCESS_PASSWORD.encode()).hexdigest()).encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def issue_token(now: float | None = None) -> tuple[str, float]:
    expires = (time.time() if now is None else now) + TOKEN_TTL
    payload = _b64(json.dumps({"exp": expires}).encode())
    signature = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}", expires


def valid_token(token: str | None, now: float | None = None) -> bool:
    if not token or token.count(".") != 1:
        return False
    payload, signature = token.split(".")
    expected = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(data["exp"]) > (time.time() if now is None else now)
    except (ValueError, KeyError, TypeError):
        return False


def request_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.query_params.get("token")


class AuthMiddleware(BaseHTTPMiddleware):
    """Rejects /api requests without a valid token when a password is configured.

    Added before CORSMiddleware so CORS wraps it: a 401 still carries CORS headers and the browser
    can read it (and show the login screen) instead of reporting a network error."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (enabled() and path.startswith("/api/") and path not in OPEN_PATHS
                and request.method != "OPTIONS" and not valid_token(request_token(request))):
            return JSONResponse({"detail": "Sign in to DocuSight."}, status_code=401)
        return await call_next(request)


class LoginIn(BaseModel):
    password: str


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    return {"required": enabled(), "signed_in": (not enabled()) or valid_token(request_token(request))}


@router.post("/login")
def login(body: LoginIn, request: Request) -> dict[str, Any]:
    if not enabled():
        return {"token": "", "expires_at": None}
    client = request.client.host if request.client else "unknown"
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(client, []) if now - t < FAILURE_WINDOW]
        _failures[client] = recent
        if len(recent) >= MAX_FAILURES:
            raise HTTPException(429, "Too many wrong passwords. Try again in a few minutes.")
    if not hmac.compare_digest(body.password.encode(), ACCESS_PASSWORD.encode()):
        with _failures_lock:
            _failures.setdefault(client, []).append(now)
        raise HTTPException(401, "Wrong password.")
    with _failures_lock:
        _failures.pop(client, None)
    token, expires = issue_token(now)
    return {"token": token, "expires_at": expires}
