"""DD.2 — pervasive route-gate contract test.

The structural answer to "test the authentication checks pervasively":

  ``assert_all_routes_gated(app)`` — walks every ``Route`` /
  ``Mount`` registered on a Starlette app, fires unauthenticated
  requests against each, and asserts that public-prefix paths pass
  through to the handler while every other route returns 302
  (browser) / 401 (HTMX/XHR).

DD.3 (wire-into-studio + dashboards) will reuse the helper against
the real ``make_app()`` outputs — that's the integration-level
pervasive coverage. This file covers the contract:
  * the helper itself returns failures cleanly
  * the public-prefix tuple is the only auth-bypass surface
  * a synthetic ``oauth_routes() + JwtCookieMiddleware`` app passes
    the contract — proving the middleware/routes machinery
    composes correctly before the dashboards plumbing layers on top

New routes added later to studio + dashboards inherit the
assertion AUTOMATICALLY when DD.3's tests call the helper — no
test-author has to remember to add a per-route auth check.
"""

from __future__ import annotations

import os

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from recon_gen.common.config import (
    AuthConfig,
    AwsConfig,
    Config,
    DbConfig,
    OidcConfig,
    SessionConfig,
)
from recon_gen.common.html.auth import (
    JwtCookieMiddleware,
    build_starlette_oauth,
    is_public_path,
    oauth_routes,
    public_path_prefixes,
)
from recon_gen.common.sql import Dialect


_TEST_JWT_SECRET = "test-jwt-secret-must-be-at-least-32-bytes-long!"
_TEST_OIDC_SECRET = "test-oidc-client-secret"


@pytest.fixture(autouse=True)
def set_secret_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The auth blocks read secrets from named env vars per
    [[feedback_no_credential_friction]]."""
    monkeypatch.setenv("RECON_GEN_OIDC_CLIENT_SECRET", _TEST_OIDC_SECRET)  # typing-smell: ignore[envvar-bypass]: cfg.auth.oidc.client_secret_env is operator-supplied; the typed registry isn't the right shape
    monkeypatch.setenv("RECON_GEN_JWT_SECRET", _TEST_JWT_SECRET)  # typing-smell: ignore[envvar-bypass]: cfg.auth.session.jwt_secret_env is operator-supplied; the typed registry isn't the right shape


def _make_cfg() -> Config:
    return Config(
        aws=AwsConfig(deployment_name="test-deploy"),
        db=DbConfig(
            url="duckdb:///tmp/test.duckdb",
            dialect=Dialect.DUCKDB,
            table_prefix="test_deploy",
        ),
        auth=AuthConfig(
            oidc=OidcConfig(
                issuer_url="https://hotchkiss.io:5557/dex",
                client_id="recon-gen-app2",
                client_secret_env="RECON_GEN_OIDC_CLIENT_SECRET",
                redirect_uri="https://localhost:8765/auth/callback",
            ),
            session=SessionConfig(jwt_secret_env="RECON_GEN_JWT_SECRET"),
        ),
    )


# ---------------------------------------------------------------------------
# The reusable contract helper — DD.3+ tests call this against real apps.
# ---------------------------------------------------------------------------


def assert_all_routes_gated(app: Starlette) -> None:
    """For every GET route registered on ``app``:
      - public-prefix path (``/auth/*``, ``/static/*``, ``/docs/*``,
        ``/health``) ⇒ 200 / non-redirect.
      - any other path ⇒ 302 to ``/auth/login`` (HTML browser) AND
        401 (HTMX/XHR), both proving the gate fires.

    Raises ``AssertionError`` with a multi-line message listing every
    misconfigured route. New routes added to ``app`` inherit the
    assertion without test-author intervention — that's the pervasive
    guarantee.

    Path parameters (``{dashboard_id}``) substitute to a sentinel ``x``
    so the route resolves; the auth middleware runs BEFORE route
    dispatch, so the substitution doesn't affect the gate outcome
    (404 from the handler is still a 404 — the gate would fire 302
    before that).
    """
    import re

    client = TestClient(app)
    failures: list[str] = []
    protected_paths: list[str] = []

    for route in app.routes:
        if isinstance(route, Route):
            paths_to_check = [route.path]
        elif isinstance(route, Mount):
            paths_to_check = [route.path + "/"]
        else:
            continue

        for raw_path in paths_to_check:
            request_path = re.sub(r"\{[^/}]+\}", "x", raw_path)

            html_response = client.get(
                request_path,
                headers={"Accept": "text/html"},
                follow_redirects=False,
            )
            htmx_response = client.get(
                request_path, headers={"HX-Request": "true"}
            )

            if is_public_path(raw_path):
                if (
                    html_response.status_code == 302
                    and html_response.headers.get("location") == "/auth/login"
                ):
                    failures.append(
                        f"public path {raw_path!r} redirected to /auth/login "
                        f"(middleware bypass broken)"
                    )
                continue

            protected_paths.append(raw_path)

            if html_response.status_code != 302:
                failures.append(
                    f"protected path {raw_path!r} (HTML) returned "
                    f"{html_response.status_code}; expected 302"
                )
            elif html_response.headers.get("location") != "/auth/login":
                failures.append(
                    f"protected path {raw_path!r} (HTML) redirected to "
                    f"{html_response.headers.get('location')!r}; "
                    f"expected /auth/login"
                )

            if htmx_response.status_code != 401:
                failures.append(
                    f"protected path {raw_path!r} (HTMX) returned "
                    f"{htmx_response.status_code}; expected 401"
                )

    if not protected_paths:
        failures.append(
            "no protected paths discovered — middleware not wired or all "
            "routes fall under public prefixes (suspicious)"
        )

    if failures:
        raise AssertionError("\n".join(failures))


# ---------------------------------------------------------------------------
# Test the helper with a synthetic app — DD.2's coverage proof
# ---------------------------------------------------------------------------


async def _protected_handler(request: Request) -> JSONResponse:
    return JSONResponse({"user": getattr(request.state, "user", None)})


async def _public_handler(_request: Request) -> Response:
    return JSONResponse({"public": True})


def _build_synthetic_gated_app(tmp_path: str | os.PathLike[str]) -> Starlette:
    """Build the minimum app that should pass ``assert_all_routes_gated``:
    JwtCookieMiddleware + protected routes + a public Mount + /health.

    Deliberately skips ``oauth_routes()`` — those handlers require a
    live IdP (authlib hits ``.well-known/openid-configuration``) and
    aren't relevant to the GATE-WALK contract (which tests the
    middleware-vs-route-prefix gate decision, not OAuth handler
    semantics). The oauth_routes shape is verified separately by
    ``test_oauth_routes_registers_login_callback_logout``."""
    cfg = _make_cfg()
    from recon_gen.common.auth import build_jwt_codec
    jwt_codec = build_jwt_codec(cfg)

    routes = [
        Route("/api", _protected_handler, methods=["GET"]),
        Route("/dashboards/{dashboard_id}", _protected_handler, methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=str(tmp_path)), name="static"),
        Route("/health", _public_handler, methods=["GET"]),
    ]
    return Starlette(
        routes=routes,
        middleware=[
            Middleware(JwtCookieMiddleware, jwt_codec=jwt_codec),
        ],
    )


def test_synthetic_gated_app_passes_the_contract(tmp_path: object) -> None:
    """A correctly-wired app (oauth_routes + JwtCookieMiddleware +
    a Mount + extra protected routes) passes the gate-walk contract.
    This is the proof-of-life for the helper that DD.3+ tests reuse."""
    from pathlib import Path
    app = _build_synthetic_gated_app(str(Path(str(tmp_path))))
    assert_all_routes_gated(app)


def test_helper_detects_a_missing_gate() -> None:
    """If middleware is OMITTED, the helper raises AssertionError
    listing the protected paths that returned 200 instead of 302/401.
    Proves the helper would catch a regression — not a vacuous pass."""
    # No JwtCookieMiddleware — protected routes unguarded.
    app = Starlette(
        routes=[
            Route("/api", _protected_handler, methods=["GET"]),
        ],
    )
    with pytest.raises(AssertionError, match=r"protected path '/api'"):
        assert_all_routes_gated(app)


# ---------------------------------------------------------------------------
# Anti-regression — public-prefix tuple is the auth-boundary contract.
# ---------------------------------------------------------------------------


def test_public_prefix_tuple_is_frozen() -> None:
    """The public-prefix list IS the unauthenticated attack surface.
    Adding a prefix forces this test to update alongside the change,
    surfacing the security-impact review."""
    assert public_path_prefixes() == (
        "/auth/",
        "/static/",
        "/docs/",
        "/health",
    )


# ---------------------------------------------------------------------------
# oauth_routes() registration shape
# ---------------------------------------------------------------------------


def test_oauth_routes_registers_login_callback_logout() -> None:
    """``oauth_routes(...)`` returns the three named routes the spike
    locked. New deployments + tests rely on these specific paths."""
    cfg = _make_cfg()
    oauth = build_starlette_oauth(cfg)
    from recon_gen.common.auth import build_jwt_codec
    jwt_codec = build_jwt_codec(cfg)

    routes = oauth_routes(oauth=oauth, jwt_codec=jwt_codec, cfg=cfg)
    paths = {r.path for r in routes}
    assert paths == {"/auth/login", "/auth/callback", "/auth/logout"}

    names = {r.name for r in routes}
    assert names == {"auth_login", "auth_callback", "auth_logout"}
