"""DD.2 — Starlette JWT cookie middleware + OAuth routes.

Layered over DD.1's factories (``recon_gen.common.auth.build_oidc_client``,
``JwtCodec``, ``build_jwt_codec``):

- **``JwtCookieMiddleware``** — Starlette ASGI middleware. On every
  request, reads the session cookie, decodes via ``JwtCodec``, parks
  the user claims on ``request.state.user``. Missing / tampered /
  expired ⇒ 302 to ``/auth/login`` (browser navigation) or 401 (HTMX /
  XHR detected via the ``HX-Request`` header). Public-path prefixes
  (``/auth/``, ``/static/``, ``/docs/``) bypass without auth so the
  login page can load its CSS + the static handbook stays public.

- **``oauth_routes(oauth_client, jwt_codec, cfg)``** — returns the
  three ``Route`` objects (``/auth/login``, ``/auth/callback``,
  ``/auth/logout``) that drive the OIDC handshake via authlib's
  ``starlette_client.OAuth`` wrapper. Login redirects to the IdP's
  authorize endpoint (PKCE + state managed by authlib via Starlette's
  ``SessionMiddleware``); callback exchanges code → token → userinfo,
  mints our ``JwtCodec`` cookie, redirects to ``/``; logout clears
  the cookie + redirects to the IdP's ``end_session_endpoint``.

The middleware + routes fire ONLY when the operator has configured
``cfg.auth.oidc`` AND ``cfg.auth.session`` in their cfg yaml.
``make_app`` short-circuits on absence — keeps the HTTP local-dev
posture untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TYPE_CHECKING, cast

import jwt as _jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from recon_gen.common.auth import JwtCodec

if TYPE_CHECKING:
    from recon_gen.common.config import Config


# Cookie name for the long-lived session JWT. Single name; no envs to
# stagger between dev/ci because the cookie is scoped per-host.
SESSION_COOKIE_NAME = "recon_gen_session"

# Path prefixes that bypass auth. The login page itself must be
# reachable unauthenticated (otherwise auth-loop); /static/ serves
# CSS + JS for the login page; /docs/ stays anonymous because the
# handbook is intentionally public. /health is for liveness probes.
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/auth/",
    "/static/",
    "/docs/",
    "/health",
)


def _is_public_path(path: str) -> bool:
    """Return True when ``path`` is exempt from auth."""
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


def _wants_html(request: Request) -> bool:
    """HTMX / XHR clients want a JSON 401; browser navigations want a
    302 to the login page. Detect via headers."""
    if request.headers.get("HX-Request"):
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept or accept == ""


# ---------------------------------------------------------------------------
# JWT cookie middleware
# ---------------------------------------------------------------------------


class JwtCookieMiddleware(BaseHTTPMiddleware):
    """Decode the session JWT on every request; redirect / 401 on miss.

    Built on Starlette's ``BaseHTTPMiddleware`` so the dispatch contract
    is request-in / response-out (same shape as exception_handlers).

    Authentication outcomes:
      - Public path (``/auth/*``, ``/static/*``, ``/docs/*``, ``/health``):
        pass through; do NOT decode the cookie even if present.
      - Authenticated (valid cookie): ``request.state.user = claims``,
        pass through to the route handler.
      - Unauthenticated (missing / tampered / expired cookie):
        302 to ``/auth/login`` for HTML clients; 401 JSON for
        HTMX/XHR clients.
    """

    def __init__(self, app: ASGIApp, *, jwt_codec: JwtCodec) -> None:
        super().__init__(app)
        self._jwt_codec = jwt_codec

    async def dispatch(  # pyright: ignore[reportIncompatibleMethodOverride]: BaseHTTPMiddleware's dispatch signature includes the runtime call_next type which doesn't survive strict pyright; the override is intentional and matches the documented Starlette contract
        self,
        request: Request,
        call_next: Any,  # typing-smell: ignore[explicit-any]: Starlette's RequestResponseEndpoint is the right type but isn't exported by the stubs we have
    ) -> Response:
        if _is_public_path(request.url.path):
            return cast(Response, await call_next(request))

        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token is None:
            return self._unauthenticated_response(request)

        try:
            claims = self._jwt_codec.decode(token)
        except _jwt.InvalidTokenError:
            return self._unauthenticated_response(request)

        # Park the claims so route handlers can read request.state.user.
        request.state.user = claims
        return cast(Response, await call_next(request))

    @staticmethod
    def _unauthenticated_response(request: Request) -> Response:
        if _wants_html(request):
            return RedirectResponse(url="/auth/login", status_code=302)
        return Response(
            content='{"error":"unauthenticated"}',
            status_code=401,
            media_type="application/json",
        )


# ---------------------------------------------------------------------------
# OAuth routes
# ---------------------------------------------------------------------------


def build_starlette_oauth(cfg: "Config") -> Any:
    """Build authlib's starlette ``OAuth`` client registry.

    Returns the OAuth instance with a single client registered under
    the name ``"oidc"``. The login / callback / logout routes use
    ``oauth.oidc.authorize_redirect`` / ``authorize_access_token`` to
    drive the flow.

    Separate factory from DD.1's ``build_oidc_client`` because the two
    serve different surfaces: DD.1's httpx-based client is for backend
    token exchange (callable from non-request contexts); this one
    integrates with Starlette's request session.
    """
    if cfg.auth.oidc is None:
        from recon_gen.common.auth import AuthConfigError  # noqa: PLC0415
        raise AuthConfigError(
            "cfg.auth.oidc block is absent; OAuth routes cannot be wired."
        )
    import os  # noqa: PLC0415

    secret_env = cfg.auth.oidc.client_secret_env
    secret = os.environ.get(secret_env)
    if not secret:
        from recon_gen.common.auth import AuthConfigError  # noqa: PLC0415
        raise AuthConfigError(
            f"env var {secret_env!r} (referenced by "
            f"cfg.auth.oidc.client_secret_env) is unset or empty."
        )

    # Lazy import — keeps `recon-gen json apply` etc. from pulling
    # authlib's starlette tree.
    from authlib.integrations.starlette_client import OAuth  # noqa: PLC0415

    oauth = OAuth()
    cast(Any, oauth).register(  # pyright: ignore[reportUnknownMemberType]: authlib's OAuth.register is partially-typed; we own the kwargs shape per the spike
        name="oidc",
        server_metadata_url=(
            f"{cfg.auth.oidc.issuer_url.rstrip('/')}/.well-known/openid-configuration"
        ),
        client_id=cfg.auth.oidc.client_id,
        client_secret=secret,
        client_kwargs={"scope": " ".join(cfg.auth.oidc.scopes)},
    )
    return oauth


def oauth_routes(
    *,
    oauth: Any,  # typing-smell: ignore[explicit-any]: authlib OAuth registry is loose-typed; we own the call surface
    jwt_codec: JwtCodec,
    cfg: "Config",
) -> Sequence[Route]:
    """Build the three auth routes against the supplied OAuth client.

    Returned routes:
      - ``GET /auth/login`` — redirect to IdP's authorize endpoint.
      - ``GET /auth/callback`` — exchange code → token → userinfo,
        mint JWT cookie, redirect to ``/``.
      - ``GET /auth/logout`` — clear cookie, redirect to IdP's
        end_session_endpoint.

    The ``oauth`` client is the ``starlette_client.OAuth`` instance
    from ``build_starlette_oauth``; the named provider is ``"oidc"``.
    """
    if cfg.auth.oidc is None:
        from recon_gen.common.auth import AuthConfigError  # noqa: PLC0415
        raise AuthConfigError("cfg.auth.oidc absent; cannot wire oauth_routes")

    redirect_uri = cfg.auth.oidc.redirect_uri

    async def login(request: Request) -> Response:
        # authlib computes PKCE + state, parks them in
        # request.session (which SessionMiddleware backs), and returns
        # the 302 to the authorize endpoint.
        return cast(
            Response,
            await oauth.oidc.authorize_redirect(request, redirect_uri),
        )

    async def callback(request: Request) -> Response:
        # Exchange the code; authlib pulls state/PKCE from
        # request.session, verifies, and returns the parsed token
        # (incl. id_token claims as token['userinfo']).
        try:
            token = await oauth.oidc.authorize_access_token(request)
        except Exception:  # noqa: BLE001 — surface as an actionable 401 not a 500
            return Response(
                content='{"error":"oauth_callback_failed"}',
                status_code=401,
                media_type="application/json",
            )
        # Token shape (per authlib): {'access_token', 'id_token',
        # 'userinfo': {...id_token claims...}, ...}.
        userinfo: dict[str, Any] = cast(dict[str, Any], token.get("userinfo") or {})
        sub = str(userinfo.get("sub", ""))
        email = str(userinfo.get("email", ""))
        if not sub:
            return Response(
                content='{"error":"oauth_missing_sub"}',
                status_code=401,
                media_type="application/json",
            )
        session_token = jwt_codec.encode({"sub": sub, "email": email})
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=jwt_codec.ttl_seconds,
            path="/",
        )
        return response

    async def logout(request: Request) -> Response:
        # Look up the IdP's end_session_endpoint via the metadata URL
        # authlib already cached. Fall back to redirecting home if the
        # IdP doesn't advertise one.
        metadata = await oauth.oidc.load_server_metadata()
        end_session: str | None = metadata.get("end_session_endpoint")
        target = end_session or "/"
        response = RedirectResponse(url=target, status_code=302)
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            path="/",
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    return (
        Route("/auth/login", login, methods=["GET"], name="auth_login"),
        Route("/auth/callback", callback, methods=["GET"], name="auth_callback"),
        Route("/auth/logout", logout, methods=["GET"], name="auth_logout"),
    )


# ---------------------------------------------------------------------------
# Pervasive-coverage helper — used by tests/unit/test_auth_gate_routes.py
# ---------------------------------------------------------------------------


def is_public_path(path: str) -> bool:
    """Public-API mirror of ``_is_public_path``.

    The route-walking gate test imports this to decide which routes
    SHOULD be reachable without auth (and skip the 302 assertion for
    them) vs which MUST 302 / 401 unauthenticated.
    """
    return _is_public_path(path)


def public_path_prefixes() -> tuple[str, ...]:
    """The frozen public-path tuple (test/external introspection)."""
    return _PUBLIC_PATH_PREFIXES
