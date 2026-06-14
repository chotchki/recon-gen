"""DD.1 — OAuth/OIDC client + JWT session codec factories.

Two builder functions consumed by Studio + Dashboards CLI when
``cfg.auth.oidc`` and/or ``cfg.auth.session`` are present:

- ``build_oidc_client(cfg)`` — returns an authlib ``OAuth2Client``
  configured against the discovered issuer's authorize / token / userinfo
  endpoints. Secret is loaded from ``os.environ[cfg.auth.oidc.client_secret_env]``
  per ``[[feedback_no_credential_friction]]`` — the cfg file carries
  the env-var NAME, never the secret itself.

- ``build_jwt_codec(cfg)`` — returns a ``JwtCodec`` (thin wrapper over
  PyJWT) for issuing / verifying the session cookie. HS256 over a
  cfg-supplied secret env var. Used by the JWT session middleware
  (DD.2) on every request.

Both factories raise ``AuthConfigError`` when the cfg block is absent
OR when the env var carrying the secret is unset — explicit signal so
the CLI command can refuse to start (Studio without auth in
production-ish posture is a footgun) rather than silently accept
unauthenticated requests.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from recon_gen.common.config import Config


class AuthConfigError(Exception):
    """Raised when the auth wiring can't be built — cfg block missing
    OR secret env var unset. Operator-facing message format: short
    cause + actionable next step (set this env var, add this cfg block)."""


# ---------------------------------------------------------------------------
# OIDC client
# ---------------------------------------------------------------------------


def build_oidc_client(cfg: "Config") -> Any:
    """Construct an authlib ``OAuth2Client`` against ``cfg.auth.oidc``.

    Secret loaded from ``os.environ[cfg.auth.oidc.client_secret_env]``.
    Issuer endpoints discovered lazily by the caller (DD.2's
    ``/auth/login`` route fetches ``<issuer_url>/.well-known/openid-configuration``
    via httpx + caches per-process). This factory only builds the client
    object + validates the env-var secret is set — does NOT fire an
    outbound discovery request, so unit tests don't need network or
    mocked httpx.

    Returns ``Any`` (not ``OAuth2Client``) because authlib's type stubs
    are partial + the OAuth2Client type pulls in starlette-incompatible
    Twisted imports under some installs.
    """
    if cfg.auth.oidc is None:
        raise AuthConfigError(
            "cfg.auth.oidc block is absent; OAuth login cannot be "
            "configured. Add the oidc: block to your cfg.yaml or "
            "disable auth by leaving the block off (HTTP-local-dev posture)."
        )
    secret_env = cfg.auth.oidc.client_secret_env
    secret = os.environ.get(secret_env)
    if not secret:
        raise AuthConfigError(
            f"env var {secret_env!r} (referenced by "
            f"cfg.auth.oidc.client_secret_env) is unset or empty. "
            f"Export the OIDC client secret as {secret_env} before "
            f"starting Studio / Dashboards."
        )
    # Lazy import: authlib is in the [prod] extra; running `recon-gen
    # json apply` or unit tests doesn't need it. Only the auth-enabled
    # serve paths fire this builder.
    from authlib.integrations.httpx_client import OAuth2Client  # noqa: PLC0415

    return OAuth2Client(
        client_id=cfg.auth.oidc.client_id,
        client_secret=secret,
        scope=" ".join(cfg.auth.oidc.scopes),
        redirect_uri=cfg.auth.oidc.redirect_uri,
    )


# ---------------------------------------------------------------------------
# JWT session codec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JwtCodec:
    """Sign + verify session JWTs. HS256 over a cfg-supplied secret.

    Kept thin — middleware (DD.2) needs encode(claims) → str and
    decode(token) → claims dict. Anything beyond that (refresh,
    rotation, audience claim) belongs in the middleware, not here.
    """
    secret: str
    algorithm: str = "HS256"
    ttl_seconds: int = 8 * 60 * 60  # 8h — covers a workday

    def encode(self, claims: dict[str, Any]) -> str:
        """Sign claims into a JWT. Caller supplies ``sub`` (user id),
        ``email``; we stamp ``iat`` + ``exp``."""
        import jwt  # noqa: PLC0415 — lazy: PyJWT only on auth path
        now = int(time.time())
        payload: dict[str, Any] = {
            **claims,
            "iat": now,
            "exp": now + self.ttl_seconds,
        }
        # PyJWT >=2.0 (floor 2.8) returns str unambiguously.
        token: str = jwt.encode(payload, self.secret, algorithm=self.algorithm)
        return token

    def decode(self, token: str) -> dict[str, Any]:
        """Verify + decode. Raises ``jwt.InvalidTokenError`` on failure
        (expired, wrong signature, malformed). Middleware catches +
        redirects to /auth/login."""
        import jwt  # noqa: PLC0415
        decoded: dict[str, Any] = jwt.decode(  # pyright: ignore[reportUnknownMemberType]: PyJWT decode returns Any; we own the issuance path so the claims shape is known to us
            token, self.secret, algorithms=[self.algorithm],
        )
        return decoded


def build_jwt_codec(cfg: "Config") -> JwtCodec:
    """Build a ``JwtCodec`` against ``cfg.auth.session.jwt_secret_env``.

    Secret loaded from the env var named in the cfg
    (``[[feedback_no_credential_friction]]``). Raises
    ``AuthConfigError`` when the session block is absent OR the env
    var is unset.
    """
    if cfg.auth.session is None:
        raise AuthConfigError(
            "cfg.auth.session block is absent; JWT session codec cannot "
            "be configured. Add the session: block with jwt_secret_env "
            "pointing at the env var carrying the HS256 signing secret."
        )
    secret_env = cfg.auth.session.jwt_secret_env
    secret = os.environ.get(secret_env)
    if not secret:
        raise AuthConfigError(
            f"env var {secret_env!r} (referenced by "
            f"cfg.auth.session.jwt_secret_env) is unset or empty. "
            f"Export the JWT signing secret as {secret_env} before "
            f"starting Studio / Dashboards (minimum 32 bytes for HS256)."
        )
    return JwtCodec(secret=secret)
