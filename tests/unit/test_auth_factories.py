"""DD.1 — OIDC client + JWT codec factory tests.

Pins the cfg → factory wire-up:

- ``build_oidc_client`` reads secret from env var named by cfg,
  raises ``AuthConfigError`` when block absent OR env var unset.
- ``build_jwt_codec`` same shape for the session block.
- The codec's ``encode`` / ``decode`` round-trip works + a tampered
  token fails verification.
- Loader rejects partial OIDC blocks (missing required fields) with
  ``MissingFieldError`` carrying the field path.

Issuer discovery + token exchange (DD.2's ``/auth/{login,callback}``)
is NOT exercised here — those land with the route + middleware tests.
This file is the **builder unit**.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.auth import (
    AuthConfigError,
    JwtCodec,
    build_jwt_codec,
    build_oidc_client,
)
from recon_gen.common.config import (
    MissingFieldError,
    load_config,
)


_MIN_CFG = """\
aws:
  account_id: '123456789012'
  region: us-east-1
  deployment_name: test-deploy
db:
  dialect: postgres
  url: postgresql://u:p@h:5432/d
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def _oidc_cfg_text() -> str:
    return _MIN_CFG + """\
auth:
  oidc:
    issuer_url: https://idp.example.com
    client_id: recon-gen-app2
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://localhost:8765/auth/callback
  session:
    jwt_secret_env: RECON_GEN_JWT_SECRET
"""


# ---------------------------------------------------------------------------
# Loader hardening: partial blocks raise MissingFieldError
# ---------------------------------------------------------------------------


def test_oidc_block_missing_client_id_raises(tmp_path: Path) -> None:
    """Operator dropped ``client_id`` from the oidc: block — should
    raise MissingFieldError with the field path, not a bare KeyError."""
    cfg_text = _MIN_CFG + """\
auth:
  oidc:
    issuer_url: https://idp.example.com
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://localhost:8765/auth/callback
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    with pytest.raises(MissingFieldError, match="auth.oidc.client_id"):
        load_config(p)


def test_oidc_block_missing_issuer_raises(tmp_path: Path) -> None:
    """Missing ``issuer_url`` raises with the field path."""
    cfg_text = _MIN_CFG + """\
auth:
  oidc:
    client_id: recon-gen-app2
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://localhost:8765/auth/callback
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    with pytest.raises(MissingFieldError, match="auth.oidc.issuer_url"):
        load_config(p)


def test_session_block_missing_secret_env_raises(tmp_path: Path) -> None:
    """Session block without ``jwt_secret_env`` raises with field path."""
    cfg_text = _MIN_CFG + """\
auth:
  session: {}
"""
    p = _write(tmp_path, "cfg.yaml", cfg_text)
    with pytest.raises(MissingFieldError, match="auth.session.jwt_secret_env"):
        load_config(p)


# ---------------------------------------------------------------------------
# build_oidc_client
# ---------------------------------------------------------------------------


def test_build_oidc_client_raises_when_block_absent(tmp_path: Path) -> None:
    """No oidc: block at all ⇒ AuthConfigError telling operator how to fix.
    Studio + Dashboards CLI consume this signal to refuse to start in
    auth-required posture."""
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    with pytest.raises(AuthConfigError, match="cfg.auth.oidc block is absent"):
        build_oidc_client(cfg)


def test_build_oidc_client_raises_when_secret_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env var named by ``client_secret_env`` is unset ⇒ AuthConfigError
    naming the env var. Operators MUST set the secret out-of-band per
    [[feedback_no_credential_friction]]."""
    p = _write(tmp_path, "cfg.yaml", _oidc_cfg_text())
    cfg = load_config(p)
    monkeypatch.delenv("RECON_GEN_OIDC_CLIENT_SECRET", raising=False)  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (auth.oidc.client_secret_env) per [[feedback_no_credential_friction]]
    with pytest.raises(AuthConfigError, match="RECON_GEN_OIDC_CLIENT_SECRET"):
        build_oidc_client(cfg)


def test_build_oidc_client_builds_when_secret_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secret env var set ⇒ authlib OAuth2Client built. Doesn't fire
    discovery (no httpx mock needed)."""
    p = _write(tmp_path, "cfg.yaml", _oidc_cfg_text())
    cfg = load_config(p)
    monkeypatch.setenv("RECON_GEN_OIDC_CLIENT_SECRET", "dummy-secret-for-tests")  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (auth.oidc.client_secret_env) per [[feedback_no_credential_friction]]
    client = build_oidc_client(cfg)
    # authlib OAuth2Client exposes client_id + scope; pin those to
    # confirm we passed cfg values through correctly.
    assert client.client_id == "recon-gen-app2"
    # authlib's scope is a space-joined string; we passed tuple
    # ("openid", "email", "profile") — defaults from cfg shape.
    assert "openid" in client.scope
    assert "email" in client.scope
    assert "profile" in client.scope


# ---------------------------------------------------------------------------
# build_jwt_codec
# ---------------------------------------------------------------------------


def test_build_jwt_codec_raises_when_block_absent(tmp_path: Path) -> None:
    """No session: block ⇒ AuthConfigError. Mirrors the oidc shape."""
    p = _write(tmp_path, "cfg.yaml", _MIN_CFG)
    cfg = load_config(p)
    with pytest.raises(AuthConfigError, match="cfg.auth.session block is absent"):
        build_jwt_codec(cfg)


def test_build_jwt_codec_raises_when_secret_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``jwt_secret_env`` named but env var unset ⇒ AuthConfigError."""
    p = _write(tmp_path, "cfg.yaml", _oidc_cfg_text())
    cfg = load_config(p)
    monkeypatch.delenv("RECON_GEN_JWT_SECRET", raising=False)  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (auth.session.jwt_secret_env) per [[feedback_no_credential_friction]]
    with pytest.raises(AuthConfigError, match="RECON_GEN_JWT_SECRET"):
        build_jwt_codec(cfg)


def test_build_jwt_codec_builds_when_secret_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secret set ⇒ JwtCodec built, can encode + decode round-trip."""
    p = _write(tmp_path, "cfg.yaml", _oidc_cfg_text())
    cfg = load_config(p)
    monkeypatch.setenv(  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (auth.session.jwt_secret_env) per [[feedback_no_credential_friction]]
        "RECON_GEN_JWT_SECRET",
        "this-is-a-32-byte-test-secret-aaaa",
    )
    codec = build_jwt_codec(cfg)
    assert isinstance(codec, JwtCodec)
    token = codec.encode({"sub": "user-123", "email": "test@example.com"})
    assert isinstance(token, str)
    claims = codec.decode(token)
    assert claims["sub"] == "user-123"
    assert claims["email"] == "test@example.com"
    # iat + exp stamped by encode
    assert "iat" in claims
    assert "exp" in claims
    assert claims["exp"] > claims["iat"]


def test_jwt_codec_decode_rejects_tampered_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token signed by one secret cannot be decoded by another —
    pins the security property that JWT signing actually does what
    we claim. PyJWT raises InvalidSignatureError."""
    import jwt as pyjwt

    p = _write(tmp_path, "cfg.yaml", _oidc_cfg_text())
    cfg = load_config(p)
    monkeypatch.setenv("RECON_GEN_JWT_SECRET", "real-secret-aaaaaaaaaaaaaaaaaaaa")  # typing-smell: ignore[envvar-bypass]: cfg-supplied env var name (auth.session.jwt_secret_env) per [[feedback_no_credential_friction]]
    codec = build_jwt_codec(cfg)
    token = codec.encode({"sub": "user-123"})

    # Build a codec with a different secret + try to decode
    evil = JwtCodec(secret="wrong-secret-bbbbbbbbbbbbbbbbbbbbbbb")
    with pytest.raises(pyjwt.InvalidTokenError):
        evil.decode(token)


def test_jwt_codec_decode_rejects_expired_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A codec with ttl_seconds=0 issues a token already expired by
    the time decode runs (PyJWT enforces exp claim by default)."""
    import jwt as pyjwt

    codec = JwtCodec(
        secret="real-secret-aaaaaaaaaaaaaaaaaaaaaaaa", ttl_seconds=-1,
    )
    token = codec.encode({"sub": "user-123"})
    with pytest.raises(pyjwt.ExpiredSignatureError):
        codec.decode(token)
