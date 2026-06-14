"""DE.4 — cfg yaml loader carries DC.1 (app2.tls) + DD.1 (auth.oidc /
auth.session) blocks; CLI consumption falls back to them.

Pins:
- ``app2.tls:`` block in cfg yaml ⇒ ``cfg.app2.tls.{cert_path, key_path}``.
- ``auth.oidc:`` block ⇒ ``cfg.auth.oidc.*`` populated (issuer_url,
  client_id, client_secret_env, redirect_uri, scopes).
- ``auth.session:`` block ⇒ ``cfg.auth.session.jwt_secret_env`` populated.
- Partial blocks raise ``ValueError`` with field path (loader hardening
  symmetric to DE.1 sub-A's v14 behavior).
- ``run_html_server`` reads cfg.app2.tls as fallback when --tls-cert
  / --tls-key flags are absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from recon_gen.common.config import (
    App2TlsConfig,
    load_config,
)


_MIN_YAML = """\
aws:
  account_id: "123456789012"
  region: us-east-1
  deployment_name: test-deploy
  principal_arns:
    - arn:aws:iam::123456789012:role/TestRole
db:
  dialect: postgres
  url: postgresql://u:p@h:5432/d
  table_prefix: test_deploy
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# app2.tls block (DC.1)
# ---------------------------------------------------------------------------


def test_app2_tls_block_loads_when_present(tmp_path: Path) -> None:
    """``app2.tls:`` block populates cfg.app2_tls + cfg.app2.tls view."""
    cfg_text = _MIN_YAML + """\
app2:
  tls:
    cert_path: /etc/ssl/cert.pem
    key_path: /etc/ssl/key.pem
"""
    cfg = load_config(_write(tmp_path, cfg_text))
    assert isinstance(cfg.app2.tls, App2TlsConfig)
    assert cfg.app2.tls.cert_path == "/etc/ssl/cert.pem"
    assert cfg.app2.tls.key_path == "/etc/ssl/key.pem"


def test_app2_tls_missing_cert_path_raises(tmp_path: Path) -> None:
    """Partial app2.tls block (no cert_path) raises with the field path."""
    cfg_text = _MIN_YAML + """\
app2:
  tls:
    key_path: /etc/ssl/key.pem
"""
    with pytest.raises(ValueError, match="app2.tls.cert_path"):
        load_config(_write(tmp_path, cfg_text))


def test_app2_tls_absent_yields_none(tmp_path: Path) -> None:
    """No app2.tls: block ⇒ cfg.app2.tls is None (HTTP-only posture)."""
    cfg = load_config(_write(tmp_path, _MIN_YAML))
    assert cfg.app2.tls is None


def test_app2_unknown_key_raises(tmp_path: Path) -> None:
    """Unknown key under app2: rejects with a helpful list."""
    cfg_text = _MIN_YAML + """\
app2:
  spelled_wrong: yes
"""
    with pytest.raises(ValueError, match="app2 block contains unknown keys"):
        load_config(_write(tmp_path, cfg_text))


# ---------------------------------------------------------------------------
# auth.oidc block (DD.1)
# ---------------------------------------------------------------------------


def test_auth_oidc_block_loads_when_present(tmp_path: Path) -> None:
    cfg_text = _MIN_YAML + """\
auth:
  oidc:
    issuer_url: https://idp.example.com
    client_id: recon-gen-app2
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://localhost:8765/auth/callback
"""
    cfg = load_config(_write(tmp_path, cfg_text))
    assert cfg.auth.oidc is not None
    assert cfg.auth.oidc.issuer_url == "https://idp.example.com"
    assert cfg.auth.oidc.client_id == "recon-gen-app2"
    assert cfg.auth.oidc.client_secret_env == "RECON_GEN_OIDC_CLIENT_SECRET"
    # Default scopes when not specified
    assert cfg.auth.oidc.scopes == ("openid", "email", "profile")


def test_auth_oidc_custom_scopes(tmp_path: Path) -> None:
    cfg_text = _MIN_YAML + """\
auth:
  oidc:
    issuer_url: https://idp.example.com
    client_id: app2
    client_secret_env: SECRET
    redirect_uri: https://localhost/cb
    scopes:
      - openid
      - profile
      - groups
"""
    cfg = load_config(_write(tmp_path, cfg_text))
    assert cfg.auth.oidc is not None
    assert cfg.auth.oidc.scopes == ("openid", "profile", "groups")


def test_auth_oidc_missing_client_id_raises(tmp_path: Path) -> None:
    cfg_text = _MIN_YAML + """\
auth:
  oidc:
    issuer_url: https://idp.example.com
    client_secret_env: SECRET
    redirect_uri: https://localhost/cb
"""
    with pytest.raises(ValueError, match="auth.oidc.client_id"):
        load_config(_write(tmp_path, cfg_text))


def test_auth_oidc_missing_issuer_url_raises(tmp_path: Path) -> None:
    cfg_text = _MIN_YAML + """\
auth:
  oidc:
    client_id: app2
    client_secret_env: SECRET
    redirect_uri: https://localhost/cb
"""
    with pytest.raises(ValueError, match="auth.oidc.issuer_url"):
        load_config(_write(tmp_path, cfg_text))


# ---------------------------------------------------------------------------
# auth.session block (DD.1)
# ---------------------------------------------------------------------------


def test_auth_session_block_loads_when_present(tmp_path: Path) -> None:
    cfg_text = _MIN_YAML + """\
auth:
  session:
    jwt_secret_env: RECON_GEN_JWT_SECRET
"""
    cfg = load_config(_write(tmp_path, cfg_text))
    assert cfg.auth.session is not None
    assert cfg.auth.session.jwt_secret_env == "RECON_GEN_JWT_SECRET"


def test_auth_session_missing_secret_env_raises(tmp_path: Path) -> None:
    cfg_text = _MIN_YAML + """\
auth:
  session: {}
"""
    with pytest.raises(ValueError, match="auth.session.jwt_secret_env"):
        load_config(_write(tmp_path, cfg_text))


def test_auth_unknown_key_rejects(tmp_path: Path) -> None:
    """Unknown key under auth: includes oidc + session in the allowed list."""
    cfg_text = _MIN_YAML + """\
auth:
  spelled_wrong: yes
"""
    with pytest.raises(ValueError, match="auth block contains unknown keys"):
        load_config(_write(tmp_path, cfg_text))


# ---------------------------------------------------------------------------
# CLI fallback — _html_serve reads cfg.app2.tls when flags absent
# ---------------------------------------------------------------------------


def test_run_html_server_falls_back_to_cfg_app2_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``--tls-cert`` / ``--tls-key`` are None, run_html_server
    reads cfg.app2.tls.{cert_path, key_path} as fallback. uvicorn
    receives ssl_certfile / ssl_keyfile from the cfg block."""
    captured_kwargs: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, _config: Any) -> None: ...
        async def serve(self) -> None: ...

    def _fake_config(_app: Any, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return MagicMock()

    fake_uvicorn = MagicMock()
    fake_uvicorn.Config = _fake_config
    fake_uvicorn.Server = _FakeServer

    cfg = MagicMock()
    cfg.aws.deployment_name = "test"
    cfg.db.table_prefix = "test"
    # The cfg-fallback path: app2.tls populated, CLI flags absent
    cfg.app2.tls.cert_path = "/etc/ssl/from-cfg.pem"
    cfg.app2.tls.key_path = "/etc/ssl/from-cfg-key.pem"

    with patch.dict("sys.modules", {"uvicorn": fake_uvicorn}):
        with patch(
            "recon_gen.common.theme.resolve_l2_theme",
            return_value=None,
        ), patch(
            "recon_gen.common.html._smoke_app.build_smoke_app",
            return_value=(MagicMock(), MagicMock()),
        ), patch(
            "recon_gen.common.html._smoke_app.stub_money_trail_fetcher",
        ), patch(
            "recon_gen.common.html.server.make_app",
            return_value=MagicMock(),
        ):
            from recon_gen.cli._html_serve import run_html_server
            run_html_server(
                cfg=cfg,
                instance=MagicMock(),
                l2_instance_path=None,
                host="127.0.0.1",
                port=8765,
                dev_log=False,
                app_name="smoke",
                stub=True,
                embed_docs=False,
                tls_cert=None,  # CLI flag absent
                tls_key=None,
            )

    # Cfg fallback fired
    assert captured_kwargs.get("ssl_certfile") == "/etc/ssl/from-cfg.pem"
    assert captured_kwargs.get("ssl_keyfile") == "/etc/ssl/from-cfg-key.pem"


def test_run_html_server_cli_flag_wins_over_cfg_app2_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``--tls-cert`` IS set, the CLI value wins; cfg fallback
    doesn't override. Pins precedence per DC.1 + DE.4 design."""
    captured_kwargs: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, _config: Any) -> None: ...
        async def serve(self) -> None: ...

    def _fake_config(_app: Any, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return MagicMock()

    fake_uvicorn = MagicMock()
    fake_uvicorn.Config = _fake_config
    fake_uvicorn.Server = _FakeServer

    cfg = MagicMock()
    cfg.aws.deployment_name = "test"
    cfg.db.table_prefix = "test"
    cfg.app2.tls.cert_path = "/etc/ssl/from-cfg.pem"
    cfg.app2.tls.key_path = "/etc/ssl/from-cfg-key.pem"

    with patch.dict("sys.modules", {"uvicorn": fake_uvicorn}):
        with patch(
            "recon_gen.common.theme.resolve_l2_theme",
            return_value=None,
        ), patch(
            "recon_gen.common.html._smoke_app.build_smoke_app",
            return_value=(MagicMock(), MagicMock()),
        ), patch(
            "recon_gen.common.html._smoke_app.stub_money_trail_fetcher",
        ), patch(
            "recon_gen.common.html.server.make_app",
            return_value=MagicMock(),
        ):
            from recon_gen.cli._html_serve import run_html_server
            run_html_server(
                cfg=cfg,
                instance=MagicMock(),
                l2_instance_path=None,
                host="127.0.0.1",
                port=8765,
                dev_log=False,
                app_name="smoke",
                stub=True,
                embed_docs=False,
                tls_cert="/etc/ssl/from-flag.pem",  # CLI flag wins
                tls_key="/etc/ssl/from-flag-key.pem",
            )

    assert captured_kwargs.get("ssl_certfile") == "/etc/ssl/from-flag.pem"
    assert captured_kwargs.get("ssl_keyfile") == "/etc/ssl/from-flag-key.pem"
