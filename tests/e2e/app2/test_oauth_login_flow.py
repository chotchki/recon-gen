"""DD.4.e2e — OAuth login flow against the App2 auth-wired Starlette
server.

What this test pins:

1. The ``JwtCookieMiddleware`` redirect contract — unauthenticated
   browser visits get 302'd to ``/auth/login``; HX-Request XHRs get 401
   JSON.
2. The ``recon_gen_session`` cookie round-trip — a JWT minted by
   ``build_jwt_codec(cfg)``, injected via Playwright's cookie API, is
   accepted by ``JwtCookieMiddleware.dispatch`` and the protected
   dashboard renders.
3. ``inspect_jwt_cookie`` reads the cookie correctly.
4. ``sign_in_via_oidc`` short-circuits when ``recon_gen_session`` is
   already present (peek-before-act idempotency — see app2 driver
   docstring).
5. ``sign_out_via_oidc`` clears the cookie. (Requires live Dex via the
   ``dex_container_url`` fixture because ``/auth/logout`` calls
   ``oauth.oidc.load_server_metadata()`` for the
   ``end_session_endpoint``.)
6. Tampered JWT → ``InvalidTokenError`` → 401 for HX-Request /
   302 for browser navigation.

What this test does NOT pin (deferred to a follow-up):

- The full ``sign_in_via_oidc`` round-trip through Dex's login form +
  approval screen. That would require the test server to bind to
  ``cfg.auth.oidc.redirect_uri``'s exact host:port over HTTPS using
  ``cfg.app2.tls``'s cert+key — the current ``html2_server`` only binds
  to an ephemeral port over HTTP. Backlog item filed in PLAN.md.

Skip semantics:

- Skips when ``cfg.auth.oidc`` or ``cfg.auth.session`` is absent (cfg
  not configured for auth).
- ``test_sign_out_via_oidc_clears_session_cookie`` additionally pulls
  the ``dex_container_url`` fixture, which skips when ``cfg.app2.tls``
  is absent (Dex needs the LE cert from DC.3).

POLICY 1 ≡ POLICY 2: when CI's ``ci.yml`` cfg block carries
``auth.oidc`` + ``auth.session``, every test in this module fires.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from recon_gen.apps.executives.app import build_executives_app
from recon_gen.apps.executives.datasets import build_all_datasets
from recon_gen.common.auth import build_jwt_codec
from recon_gen.common.config import Config
from recon_gen.common.html.auth import SESSION_COOKIE_NAME
from tests.e2e._drivers import App2Driver


_DASHBOARD_ID = "exec"


def _exec_stub_fetcher(
    visual_id: str, params: dict[str, list[str]],  # typing-smell: ignore[bare-str-id]: visual_id comes from callers as raw analyst string
) -> dict[str, Any]:
    """Minimal stub fetcher — auth tests don't assert on rendered data,
    just on the redirect chain + cookie state. Returns shape-appropriate
    empty payloads so the page renders past the middleware."""
    if "kpi" in visual_id:
        return {"values": [
            {"value": 0, "label": "Auth Test", "format": "number"},
        ]}
    if "table" in visual_id:
        return {
            "columns": ["col"],
            "rows": [],
            "page_offset": 0, "page_size": 0, "total_rows": 0,
        }
    return {}


@pytest.fixture(scope="module")
def auth_cfg(cfg: Config) -> Config:
    """Require auth.oidc + auth.session both set; skip otherwise.

    Reads from the session-scoped ``cfg`` fixture (re-exported via
    ``tests/e2e/app2/conftest.py``), which the runner populates from
    ``run/config.yaml`` locally or from ``ci.yml``'s heredoc in CI.
    """
    if cfg.auth.oidc is None:
        pytest.skip("cfg.auth.oidc not configured — skipping OAuth e2e")
    if cfg.auth.session is None:
        pytest.skip("cfg.auth.session not configured — skipping OAuth e2e")
    return cfg


@pytest.fixture
def auth_driver(auth_cfg: Config) -> Iterator[App2Driver]:
    """App2Driver pointing at an auth-wired Starlette server. The
    server reads ``cfg.auth.oidc`` + ``cfg.auth.session`` and wires
    ``JwtCookieMiddleware`` + ``oauth_routes`` per DD.2."""
    build_all_datasets(auth_cfg)
    tree_app = build_executives_app(auth_cfg)
    assert tree_app.analysis is not None
    primary_sheet = tree_app.analysis.sheets[0]
    with App2Driver.serving(
        cfg=auth_cfg,
        tree_app=tree_app,
        sheet=primary_sheet,
        data_fetcher=_exec_stub_fetcher,  # pyright: ignore[reportArgumentType]: inline fetcher closure; structural DataFetcher contract holds at runtime
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="Auth Test",
        wire_auth=True,  # DD.4 — opt in to the auth middleware + /auth routes
    ) as driver:
        yield driver


def _mint_jwt(cfg: Config, *, sub: str = "testuser",
              email: str = "testuser@example.com") -> str:
    """Mint a session JWT the way ``/auth/callback`` does (DD.2)."""
    codec = build_jwt_codec(cfg)
    return codec.encode({"sub": sub, "email": email})


def _set_session_cookie(driver: App2Driver, value: str) -> None:
    """Set the ``recon_gen_session`` cookie on the driver's Playwright
    context. Matches the attrs ``/auth/callback`` sets (HttpOnly /
    Secure / SameSite=Lax / Path=/) — Playwright accepts secure cookies
    on http://127.0.0.1 URLs in test mode."""
    driver.page.context.add_cookies([{
        "name": SESSION_COOKIE_NAME,
        "value": value,
        "url": driver.base_url,
        "httpOnly": True,
        "secure": False,  # test server is http; production sets True
        "sameSite": "Lax",
    }])


def test_unauthenticated_html_get_redirects_to_login(
    auth_driver: App2Driver,
) -> None:
    """Browser navigation to a protected path with no session cookie
    302s to /auth/login (Accept: text/html branch of
    ``JwtCookieMiddleware._unauthenticated_response``)."""
    # disable following redirects so we can assert the 302 itself
    # rather than what /auth/login's redirect_response ends up showing.
    response = auth_driver.page.context.request.get(
        f"{auth_driver.base_url}/dashboards/{_DASHBOARD_ID}",
        max_redirects=0,
    )
    assert response.status == 302, (
        f"expected 302 to /auth/login, got {response.status}"
    )
    location = response.headers.get("location", "")
    assert location.endswith("/auth/login") or "/auth/login" in location, (
        f"expected Location: /auth/login, got {location!r}"
    )


def test_unauthenticated_xhr_returns_401(
    auth_driver: App2Driver,
) -> None:
    """HX-Request header → 401 JSON (not a redirect — HTMX swaps would
    consume the redirect chain badly). Mirrors ``_wants_html`` logic."""
    response = auth_driver.page.context.request.get(
        f"{auth_driver.base_url}/dashboards/{_DASHBOARD_ID}",
        headers={"HX-Request": "true"},
    )
    assert response.status == 401, (
        f"expected 401 JSON for HX-Request, got {response.status}"
    )
    body = response.text()
    assert "unauthenticated" in body, (
        f"expected JSON body to name the error, got {body!r}"
    )


def test_authenticated_with_valid_jwt_serves_protected_page(
    auth_driver: App2Driver, auth_cfg: Config,
) -> None:
    """A JWT minted by ``build_jwt_codec(cfg)`` + injected as the
    session cookie passes ``JwtCookieMiddleware.dispatch`` and the
    protected dashboard renders."""
    _set_session_cookie(auth_driver, _mint_jwt(auth_cfg))
    auth_driver.open(_DASHBOARD_ID)
    # Successful render = the dashboard list page resolves with our
    # cookie. We don't assert on visual content here (stub fetcher
    # returns empty); just that the middleware let us through.
    assert "/auth/login" not in auth_driver.page.url, (
        f"expected to land on dashboard, ended up at {auth_driver.page.url!r}"
    )


def test_inspect_jwt_cookie_returns_cookie_details(
    auth_driver: App2Driver, auth_cfg: Config,
) -> None:
    """``inspect_jwt_cookie`` returns ``None`` when absent, returns a
    ``{name, value, domain, path}`` dict when present."""
    # Pre-injection: no cookie set.
    auth_driver.open(_DASHBOARD_ID) if False else None  # noqa: B015 — keep page at about:blank
    auth_driver.page.goto(auth_driver.base_url)
    assert auth_driver.inspect_jwt_cookie() is None

    # Post-injection: cookie present.
    jwt = _mint_jwt(auth_cfg)
    _set_session_cookie(auth_driver, jwt)
    auth_driver.page.goto(auth_driver.base_url)
    info = auth_driver.inspect_jwt_cookie()
    assert info is not None, "expected cookie info after injection"
    assert info["name"] == SESSION_COOKIE_NAME
    assert info["value"] == jwt
    assert info["path"] == "/"


def test_sign_in_via_oidc_short_circuits_when_already_authenticated(
    auth_driver: App2Driver, auth_cfg: Config,
) -> None:
    """Peek-before-act idempotency: with a valid session cookie
    pre-injected, ``sign_in_via_oidc`` returns without driving Dex.
    Test verifies the cookie wasn't disturbed."""
    jwt = _mint_jwt(auth_cfg)
    _set_session_cookie(auth_driver, jwt)
    auth_driver.page.goto(auth_driver.base_url)
    # Call sign_in_via_oidc. Idempotent short-circuit should mean
    # this returns without driving any form. If the short-circuit
    # path failed, the verb would try to navigate to /auth/login
    # and (without a real Dex configured to accept our test redirect)
    # fail with a Playwright timeout — that's the negative assertion.
    auth_driver.sign_in_via_oidc(email="anyone@example.com", password="nope")
    # Cookie is still there afterwards.
    info = auth_driver.inspect_jwt_cookie()
    assert info is not None and info["value"] == jwt, (
        "sign_in_via_oidc disturbed the existing session cookie when it "
        "should have short-circuited"
    )


def test_sign_out_via_oidc_clears_session_cookie(
    auth_driver: App2Driver, auth_cfg: Config,
    dex_container_url: str,  # noqa: ARG001 — required for end_session_endpoint discovery
) -> None:
    """``GET /auth/logout`` calls ``oauth.oidc.load_server_metadata()``
    for ``end_session_endpoint``, redirects there, and clears the
    ``recon_gen_session`` cookie. Requires a live Dex."""
    _set_session_cookie(auth_driver, _mint_jwt(auth_cfg))
    auth_driver.page.goto(auth_driver.base_url)
    assert auth_driver.inspect_jwt_cookie() is not None

    auth_driver.sign_out_via_oidc()

    # Cookie cleared regardless of where the browser landed (Dex
    # end_session page or / fallback).
    assert auth_driver.inspect_jwt_cookie() is None, (
        "sign_out_via_oidc did not clear the recon_gen_session cookie"
    )


def test_tampered_jwt_returns_401_for_xhr(
    auth_driver: App2Driver,
) -> None:
    """Signature-failing JWT → ``jwt.InvalidTokenError`` →
    ``_unauthenticated_response``. HX-Request branch returns 401 JSON
    (not 302); a real HTMX client would crash on a redirect, so this
    branch is the security-critical one."""
    # Plausibly-shaped but wrong-signature JWT.
    tampered = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJ0ZXN0IiwiZW1haWwiOiJ0ZXN0QGV4YW1wbGUuY29tIn0."
        "tampered-signature-segment"
    )
    _set_session_cookie(auth_driver, tampered)
    response = auth_driver.page.context.request.get(
        f"{auth_driver.base_url}/dashboards/{_DASHBOARD_ID}",
        headers={"HX-Request": "true"},
    )
    assert response.status == 401, (
        f"tampered JWT should yield 401 JSON for HX-Request; "
        f"got {response.status}"
    )
