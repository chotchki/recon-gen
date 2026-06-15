"""DD.4 — pre-flight Dex OIDC IdP reconciliation helper.

Covers the eight branches of ``_ensure_oidc_if_configured``:
- non-OIDC-touching layer (unit/db) ⇒ no-op even with auth.oidc block.
- cfg.auth.oidc absent ⇒ no-op.
- cfg.auth.oidc set but cfg.app2.tls=None ⇒ EXIT_NEEDS_OPERATOR with DC.3 hint.
- RECON_GEN_DEX_URL env-URL short-circuit ⇒ no spinup, returns 0.
- success path ⇒ ensure_dev_idp called with cfg-derived kwargs.
- env: ci tier mapping ⇒ Env.CI passed through.
- ValueError ⇒ EXIT_NEEDS_OPERATOR with secrets-env-var breadcrumbs.
- backend error (RuntimeError) ⇒ EXIT_NEEDS_OPERATOR with diagnostic stderr.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from recon_gen._dev.runner import EXIT_NEEDS_OPERATOR, _ensure_oidc_if_configured


_MIN_CFG_PG = """\
aws:
  account_id: '123456789012'
  region: us-east-1
  deployment_name: test-deploy
db:
  dialect: postgres
  url: postgresql://u:p@h:5432/d
"""

_CFG_TLS_BLOCK = """\
app2:
  tls:
    cert_path: /tmp/cert.pem
    key_path: /tmp/key.pem
    account_email: ops@example.com
    env: dev
"""

_CFG_OIDC_BLOCK = """\
auth:
  oidc:
    issuer_url: https://localdev.recon-gen.hotchkiss.io:5557/dex
    client_id: recon-gen-app2-test
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://localhost:8765/auth/callback
  session:
    jwt_secret_env: RECON_GEN_JWT_SECRET
"""


def _write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(body)
    return p


@pytest.fixture(autouse=True)
def set_secret_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cfg's *_secret_env names are operator-supplied; the typed
    registry isn't the right shape for them."""
    monkeypatch.setenv("RECON_GEN_OIDC_CLIENT_SECRET", "test-client-secret-value")  # typing-smell: ignore[envvar-bypass]: cfg.auth.oidc.client_secret_env is operator-supplied
    monkeypatch.setenv("RECON_GEN_JWT_SECRET", "test-jwt-secret-with-32-bytes-min-len!")  # typing-smell: ignore[envvar-bypass]: cfg.auth.session.jwt_secret_env is operator-supplied
    monkeypatch.setenv("RECON_GEN_DEX_USER_PASSWORD", "test-password-123")  # typing-smell: ignore[envvar-bypass]: test fixture setting the typed env var; .serialize() doesn't fit setenv's signature


def test_noop_when_layer_not_oidc_touching(tmp_path: Path) -> None:
    """The ``db`` layer skips OIDC pre-flight even with auth.oidc + tls
    both set — Dex spinup is App2-only per spike lock."""
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG + _CFG_TLS_BLOCK + _CFG_OIDC_BLOCK)
    with patch("recon_gen._dev.oidc.ensure_dev_idp") as ensure:
        rc = _ensure_oidc_if_configured(cfg_path, layer="db")
    assert rc == 0
    ensure.assert_not_called()


def test_noop_when_oidc_block_absent(tmp_path: Path) -> None:
    """cfg.auth.oidc absent ⇒ no-op even for app2 layer."""
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG + _CFG_TLS_BLOCK)
    with patch("recon_gen._dev.oidc.ensure_dev_idp") as ensure:
        rc = _ensure_oidc_if_configured(cfg_path, layer="app2")
    assert rc == 0
    ensure.assert_not_called()


def test_tls_block_required_returns_needs_operator_with_dc3_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """cfg.auth.oidc set but cfg.app2.tls=None ⇒ EXIT_NEEDS_OPERATOR.
    Dex serves HTTPS using DC.3's LE cert, so DD.4 hard-depends on the
    tls block being present."""
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG + _CFG_OIDC_BLOCK)
    rc = _ensure_oidc_if_configured(cfg_path, layer="app2")
    assert rc == EXIT_NEEDS_OPERATOR
    err = capsys.readouterr().err
    assert "DC.3" in err
    assert "tls-setup.md" in err
    assert "cfg.app2.tls" in err


def test_dex_url_env_short_circuits_skips_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECON_GEN_DEX_URL set ⇒ runner trusts the pre-spun container,
    skips ensure_dev_idp entirely. Used by CI's pre-spun shared-dex
    step (mirrors RECON_GEN_DEMO_DATABASE_URL_PG pattern)."""
    monkeypatch.setenv(  # typing-smell: ignore[envvar-bypass]: test fixture setting the typed env var; .serialize() doesn't fit setenv's signature
        "RECON_GEN_DEX_URL",
        "https://localci.recon-gen.hotchkiss.io:5556/dex",
    )
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG + _CFG_TLS_BLOCK + _CFG_OIDC_BLOCK)
    with patch("recon_gen._dev.oidc.ensure_dev_idp") as ensure:
        rc = _ensure_oidc_if_configured(cfg_path, layer="app2")
    assert rc == 0
    ensure.assert_not_called()


def test_success_calls_ensure_dev_idp_with_cfg_derived_args(
    tmp_path: Path,
) -> None:
    """Happy path: ensure_dev_idp receives env, cert paths (expanded),
    client_id, redirect_uri, and the testuser email from the cfg."""
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG + _CFG_TLS_BLOCK + _CFG_OIDC_BLOCK)
    with patch("recon_gen._dev.oidc.ensure_dev_idp") as ensure:
        rc = _ensure_oidc_if_configured(cfg_path, layer="app2")
    assert rc == 0
    ensure.assert_called_once()
    _, kwargs = ensure.call_args
    assert kwargs["cert_path"] == Path("/tmp/cert.pem")
    assert kwargs["key_path"] == Path("/tmp/key.pem")
    assert kwargs["client_id"] == "recon-gen-app2-test"
    assert kwargs["redirect_uri"] == "https://localhost:8765/auth/callback"
    assert kwargs["user_email"] == "testuser@example.com"
    assert kwargs["client_secret"] == "test-client-secret-value"
    assert kwargs["user_password"] == "test-password-123"


def test_env_ci_tier_passes_through(tmp_path: Path) -> None:
    """cfg.app2.tls.env=ci maps to Env.CI in the ensure_dev_idp call.
    Note the dev/ci field is shared with DC.3's TLS coordinator —
    one cfg field, two consumers."""
    from recon_gen._dev.oidc import Env
    cfg_text = _MIN_CFG_PG + _CFG_OIDC_BLOCK + """\
app2:
  tls:
    cert_path: /tmp/cert.pem
    key_path: /tmp/key.pem
    account_email: ops@example.com
    env: ci
"""
    cfg_path = _write_cfg(tmp_path, cfg_text)
    with patch("recon_gen._dev.oidc.ensure_dev_idp") as ensure:
        rc = _ensure_oidc_if_configured(cfg_path, layer="app2")
    assert rc == 0
    args, _ = ensure.call_args
    assert args[0] is Env.CI


def test_value_error_returns_needs_operator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ensure_dev_idp raises ValueError (missing operator config) ⇒
    EXIT_NEEDS_OPERATOR + actionable stderr listing the secrets env
    vars the operator needs to set."""
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG + _CFG_TLS_BLOCK + _CFG_OIDC_BLOCK)
    with patch(
        "recon_gen._dev.oidc.ensure_dev_idp",
        side_effect=ValueError("client_secret is empty"),
    ):
        rc = _ensure_oidc_if_configured(cfg_path, layer="app2")
    assert rc == EXIT_NEEDS_OPERATOR
    err = capsys.readouterr().err
    assert "OIDC pre-flight failed" in err
    assert "RECON_GEN_OIDC_CLIENT_SECRET" in err
    assert "RECON_GEN_JWT_SECRET" in err
    assert "RECON_GEN_DEX_USER_PASSWORD" in err
    assert "run/secrets.env" in err


def test_backend_error_returns_needs_operator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ensure_dev_idp raises non-ValueError (Docker / readiness /
    network) ⇒ EXIT_NEEDS_OPERATOR with diagnostic stderr that points
    at the Docker daemon + cert/key mount."""
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG + _CFG_TLS_BLOCK + _CFG_OIDC_BLOCK)
    with patch(
        "recon_gen._dev.oidc.ensure_dev_idp",
        side_effect=RuntimeError("Dex readiness check failed"),
    ):
        rc = _ensure_oidc_if_configured(cfg_path, layer="app2")
    assert rc == EXIT_NEEDS_OPERATOR
    err = capsys.readouterr().err
    assert "OIDC pre-flight failed (RuntimeError)" in err
    assert "Docker daemon" in err
    assert "cfg.app2.tls cert/key" in err
