"""DC.3 — pre-flight TLS reconciliation helper.

Covers the four branches of ``_ensure_tls_if_configured``:
- chain target is non-TLS-touching (unit / db) ⇒ no-op even with tls block set.
- chain target is TLS-touching, tls block absent ⇒ no-op.
- chain target is TLS-touching, ensure_dev_env succeeds ⇒ 0.
- chain target is TLS-touching, ensure_dev_env raises ValueError (token) ⇒ EXIT_NEEDS_OPERATOR.
- chain target is TLS-touching, ensure_dev_env raises other ⇒ EXIT_NEEDS_OPERATOR.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from recon_gen._dev.runner import EXIT_NEEDS_OPERATOR, _ensure_tls_if_configured


_MIN_CFG_PG = """\
aws:
  account_id: '123456789012'
  region: us-east-1
  deployment_name: test-deploy
db:
  dialect: postgres
  url: postgresql://u:p@h:5432/d
"""

_CFG_WITH_TLS = _MIN_CFG_PG + """\
app2:
  tls:
    cert_path: /tmp/cert.pem
    key_path: /tmp/key.pem
    account_email: ops@example.com
    env: dev
"""


def _write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(body)
    return p


def test_noop_when_layer_not_tls_touching(tmp_path: Path) -> None:
    """``unit`` layer skips the pre-flight even with the tls block set —
    unit runs never need to mint certs."""
    cfg_path = _write_cfg(tmp_path, _CFG_WITH_TLS)
    with patch("recon_gen._dev.tls.ensure_dev_env") as ensure:
        rc = _ensure_tls_if_configured(cfg_path, layer="unit")
    assert rc == 0
    ensure.assert_not_called()


def test_noop_when_tls_block_absent(tmp_path: Path) -> None:
    """``cfg.app2.tls`` absent ⇒ no-op even for TLS-touching layers."""
    cfg_path = _write_cfg(tmp_path, _MIN_CFG_PG)
    with patch("recon_gen._dev.tls.ensure_dev_env") as ensure:
        rc = _ensure_tls_if_configured(cfg_path, layer="app2")
    assert rc == 0
    ensure.assert_not_called()


def test_fires_on_app2_layer_with_tls_block(tmp_path: Path) -> None:
    """TLS-touching layer + tls block ⇒ ensure_dev_env called with
    cfg-derived args."""
    cfg_path = _write_cfg(tmp_path, _CFG_WITH_TLS)
    with patch("recon_gen._dev.tls.ensure_dev_env") as ensure:
        rc = _ensure_tls_if_configured(cfg_path, layer="app2")
    assert rc == 0
    ensure.assert_called_once()
    _, kwargs = ensure.call_args
    assert kwargs["cert_path"] == Path("/tmp/cert.pem")
    assert kwargs["key_path"] == Path("/tmp/key.pem")
    assert kwargs["account_email"] == "ops@example.com"


def test_fires_on_qs_browser_layer(tmp_path: Path) -> None:
    """qs_browser layer is TLS-touching."""
    cfg_path = _write_cfg(tmp_path, _CFG_WITH_TLS)
    with patch("recon_gen._dev.tls.ensure_dev_env") as ensure:
        rc = _ensure_tls_if_configured(cfg_path, layer="qs_browser")
    assert rc == 0
    ensure.assert_called_once()


def test_missing_token_returns_needs_operator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ensure_dev_env raises ValueError on missing token ⇒
    EXIT_NEEDS_OPERATOR + actionable stderr."""
    cfg_path = _write_cfg(tmp_path, _CFG_WITH_TLS)
    with patch(
        "recon_gen._dev.tls.ensure_dev_env",
        side_effect=ValueError("RECON_GEN_CLOUDFLARE_TOKEN not set"),
    ):
        rc = _ensure_tls_if_configured(cfg_path, layer="app2")
    assert rc == EXIT_NEEDS_OPERATOR
    err = capsys.readouterr().err
    assert "TLS pre-flight failed" in err
    assert "RECON_GEN_CLOUDFLARE_TOKEN" in err
    assert "run/secrets.env" in err


def test_backend_error_returns_needs_operator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ensure_dev_env raises non-ValueError (Cloudflare 4xx, ACME rate
    limit, etc.) ⇒ EXIT_NEEDS_OPERATOR with diagnostic stderr."""
    cfg_path = _write_cfg(tmp_path, _CFG_WITH_TLS)
    with patch(
        "recon_gen._dev.tls.ensure_dev_env",
        side_effect=RuntimeError("Cloudflare API 403: token scope"),
    ):
        rc = _ensure_tls_if_configured(cfg_path, layer="app2")
    assert rc == EXIT_NEEDS_OPERATOR
    err = capsys.readouterr().err
    assert "TLS pre-flight failed (RuntimeError)" in err
    assert "Zone:DNS:Edit" in err


def test_tilde_in_cert_paths_expands(tmp_path: Path) -> None:
    """``~/`` in cfg cert_path / key_path expands to absolute home —
    matches the convention the operator doc tells people to use."""
    cfg_path = _write_cfg(
        tmp_path,
        _MIN_CFG_PG
        + """\
app2:
  tls:
    cert_path: ~/cert.pem
    key_path: ~/key.pem
    account_email: ops@example.com
    env: dev
""",
    )
    with patch("recon_gen._dev.tls.ensure_dev_env") as ensure:
        rc = _ensure_tls_if_configured(cfg_path, layer="app2")
    assert rc == 0
    _, kwargs = ensure.call_args
    # expanduser() converts the leading ~ to the absolute home dir.
    assert "~" not in str(kwargs["cert_path"])
    assert kwargs["cert_path"].is_absolute()
    assert str(kwargs["cert_path"]).endswith("/cert.pem")


def test_ci_env_passes_through(tmp_path: Path) -> None:
    """``env: ci`` cfg value maps to ``Env.CI`` in the ensure_dev_env call."""
    from recon_gen._dev.tls import Env
    cfg_path = _write_cfg(
        tmp_path,
        _MIN_CFG_PG
        + """\
app2:
  tls:
    cert_path: /tmp/cert.pem
    key_path: /tmp/key.pem
    account_email: ops@example.com
    env: ci
""",
    )
    with patch("recon_gen._dev.tls.ensure_dev_env") as ensure:
        rc = _ensure_tls_if_configured(cfg_path, layer="app2")
    assert rc == 0
    args, _ = ensure.call_args
    assert args[0] is Env.CI
