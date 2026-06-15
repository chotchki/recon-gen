"""DD.2 — JwtCookieMiddleware unit tests.

Covers the 5 dispatch branches:
- Public path (``/auth/*``, ``/static/*``, ``/docs/*``, ``/health``) ⇒
  pass through; no cookie required; ``request.state.user`` NOT set.
- Authenticated (valid JWT cookie) ⇒ pass through; ``request.state.user``
  set to the decoded claims dict.
- Missing cookie + HTML browser ⇒ 302 to ``/auth/login``.
- Missing cookie + HTMX/XHR ⇒ 401 JSON.
- Tampered / expired cookie ⇒ same unauthenticated path as missing.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from recon_gen.common.auth import JwtCodec
from recon_gen.common.html.auth import (
    SESSION_COOKIE_NAME,
    JwtCookieMiddleware,
    is_public_path,
    public_path_prefixes,
)


_TEST_SECRET = "test-secret-must-be-at-least-32-bytes-long!"


def _make_app(jwt_codec: JwtCodec) -> Starlette:
    """Tiny Starlette app: a protected ``/api`` route, a public
    ``/auth/login`` placeholder, a public ``/static/x.css``. Routes
    return a JSON envelope so tests can read ``state.user``."""

    async def protected(request: Request) -> JSONResponse:
        user = getattr(request.state, "user", None)
        return JSONResponse({"user": user, "path": str(request.url.path)})

    async def public_login(request: Request) -> JSONResponse:
        # MUST NOT have user set — middleware shouldn't decode for
        # public paths even when a cookie is present.
        user = getattr(request.state, "user", None)
        return JSONResponse({"user": user, "path": "/auth/login"})

    async def public_static(_request: Request) -> JSONResponse:
        return JSONResponse({"path": "/static/style.css"})

    return Starlette(
        routes=[
            Route("/api", protected, methods=["GET"]),
            Route("/auth/login", public_login, methods=["GET"]),
            Route("/static/style.css", public_static, methods=["GET"]),
        ],
        middleware=[Middleware(JwtCookieMiddleware, jwt_codec=jwt_codec)],
    )


# ---------------------------------------------------------------------------
# Public-path bypass
# ---------------------------------------------------------------------------


def test_public_path_helpers_match_prefixes() -> None:
    """is_public_path() ⇔ path starts with one of the locked prefixes."""
    for prefix in public_path_prefixes():
        assert is_public_path(prefix), prefix
        assert is_public_path(prefix + "anything"), prefix


def test_public_path_login_bypasses_without_cookie() -> None:
    codec = JwtCodec(secret=_TEST_SECRET)
    client = TestClient(_make_app(codec))
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert response.json() == {"user": None, "path": "/auth/login"}


def test_public_path_static_bypasses_without_cookie() -> None:
    codec = JwtCodec(secret=_TEST_SECRET)
    client = TestClient(_make_app(codec))
    response = client.get("/static/style.css")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Authenticated branch
# ---------------------------------------------------------------------------


def test_valid_jwt_attaches_user_to_request_state() -> None:
    codec = JwtCodec(secret=_TEST_SECRET)
    token = codec.encode({"sub": "user-42", "email": "u@example.com"})
    client = TestClient(_make_app(codec))
    client.cookies.set(SESSION_COOKIE_NAME, token)
    response = client.get("/api")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    user = body["user"]
    assert user is not None
    assert user["sub"] == "user-42"
    assert user["email"] == "u@example.com"
    # JwtCodec stamps iat + exp.
    assert "iat" in user
    assert "exp" in user


# ---------------------------------------------------------------------------
# Unauthenticated branches
# ---------------------------------------------------------------------------


def test_missing_cookie_html_client_gets_302_to_login() -> None:
    codec = JwtCodec(secret=_TEST_SECRET)
    client = TestClient(_make_app(codec))
    response = client.get(
        "/api",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_missing_cookie_htmx_client_gets_401_json() -> None:
    codec = JwtCodec(secret=_TEST_SECRET)
    client = TestClient(_make_app(codec))
    response = client.get("/api", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": "unauthenticated"}


def test_tampered_cookie_treated_as_missing() -> None:
    codec = JwtCodec(secret=_TEST_SECRET)
    valid = codec.encode({"sub": "user-1"})
    # Flip one character in the signature segment.
    tampered = valid[:-1] + ("a" if valid[-1] != "a" else "b")
    client = TestClient(_make_app(codec))
    client.cookies.set(SESSION_COOKIE_NAME, tampered)
    response = client.get(
        "/api",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_expired_cookie_treated_as_missing() -> None:
    # Mint a JWT with an exp 1h in the past.
    import jwt
    codec = JwtCodec(secret=_TEST_SECRET, ttl_seconds=3600)
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "user-1", "iat": now - 7200, "exp": now - 3600},
        _TEST_SECRET,
        algorithm="HS256",
    )
    client = TestClient(_make_app(codec))
    client.cookies.set(SESSION_COOKIE_NAME, expired)
    response = client.get("/api", headers={"HX-Request": "true"})
    assert response.status_code == 401


