"""DC.1 — CLI ``--tls-cert`` / ``--tls-key`` flag wiring + uvicorn config.

Two CLI surfaces (``recon-gen studio`` + ``recon-gen dashboards``) gain
matching ``--tls-cert`` / ``--tls-key`` options that pass into
``run_html_server`` and from there into uvicorn's ``ssl_certfile`` /
``ssl_keyfile``. Half-set (cert without key OR key without cert) is a
loud UsageError, not a silent HTTP fallback — operator's intent was
HTTPS, so failing tells them the cfg shape is wrong.

These tests pin the **flag plumbing** at three layers:

1. The Click commands declare both options + accept env-var fallback.
2. ``run_html_server`` raises UsageError on half-set TLS.
3. ``run_html_server`` builds the uvicorn.Config kwargs with
   ssl_certfile/ssl_keyfile when both are set.

CFG-fallback to ``cfg.app2.tls.{cert_path, key_path}`` lands in DE.2
(the legacy ``Config`` doesn't carry tls fields; once the callsite
sweep flips to v14 ``Config``, the tls fallback wires in cleanly).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from recon_gen.cli.dashboards import dashboards
from recon_gen.cli.studio import studio


def _option_names(cmd: click.Command) -> set[str]:
    """Collect ``--name`` strings declared on a Click command."""
    names: set[str] = set()
    for param in cmd.params:
        if isinstance(param, click.Option):
            names.update(param.opts)
    return names


def test_dashboards_declares_tls_options() -> None:
    """``recon-gen dashboards`` exposes both ``--tls-cert`` and ``--tls-key``."""
    names = _option_names(dashboards)
    assert "--tls-cert" in names
    assert "--tls-key" in names


def test_studio_declares_tls_options() -> None:
    """``recon-gen studio`` exposes the same TLS option pair."""
    names = _option_names(studio)
    assert "--tls-cert" in names
    assert "--tls-key" in names


def _find_option(cmd: click.Command, flag: str) -> click.Option:
    for param in cmd.params:
        if isinstance(param, click.Option) and flag in param.opts:
            return param
    raise AssertionError(f"Option {flag} not on {cmd.name}")


def test_dashboards_tls_envvar_fallback_not_via_click_envvar() -> None:
    """``RECON_GEN_TLS_CERT`` / ``RECON_GEN_TLS_KEY`` env vars wire via
    the env_keys registry (``must_be_file`` validated + access-log +
    deprecation channel) in ``_html_serve``, NOT via Click's ``envvar=``
    bypass. Post-v14 audit fix #267 — the click bypass had no typed
    validator; a typo silently fell through to None.
    """
    cert_opt = _find_option(dashboards, "--tls-cert")
    key_opt = _find_option(dashboards, "--tls-key")
    assert cert_opt.envvar is None
    assert key_opt.envvar is None


def test_studio_tls_envvar_fallback_not_via_click_envvar() -> None:
    """Studio carries the same registry-routed env-var fallback shape."""
    cert_opt = _find_option(studio, "--tls-cert")
    key_opt = _find_option(studio, "--tls-key")
    assert cert_opt.envvar is None
    assert key_opt.envvar is None


def test_run_html_server_rejects_half_set_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--tls-cert`` without ``--tls-key`` (or vice versa) raises
    ``click.UsageError``. Operator intent was HTTPS; a half-set
    pair is a typo, not a graceful HTTP fallback."""
    from recon_gen.cli._html_serve import run_html_server

    # Half-set: cert only
    with pytest.raises(click.UsageError, match="must be set together"):
        run_html_server(
            cfg=MagicMock(),
            instance=MagicMock(),
            l2_instance_path=None,
            host="127.0.0.1",
            port=8765,
            dev_log=False,
            app_name="smoke",
            stub=True,
            embed_docs=False,
            tls_cert="/etc/ssl/cert.pem",
            tls_key=None,
        )

    # Half-set: key only
    with pytest.raises(click.UsageError, match="must be set together"):
        run_html_server(
            cfg=MagicMock(),
            instance=MagicMock(),
            l2_instance_path=None,
            host="127.0.0.1",
            port=8765,
            dev_log=False,
            app_name="smoke",
            stub=True,
            embed_docs=False,
            tls_cert=None,
            tls_key="/etc/ssl/key.pem",
        )


def test_run_html_server_passes_tls_kwargs_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH ``--tls-cert`` and ``--tls-key`` are set, the
    uvicorn.Config call carries ssl_certfile + ssl_keyfile. The actual
    server.serve() is mocked — we're testing the param wiring, not
    network I/O.

    Patches `uvicorn.Config` to capture kwargs; the rest of the body
    (DB pool, app build, asyncio loop) gets stubbed via the smoke +
    stub path so no DB / no L2 lookup fires.
    """
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

    # The cfg.smoke path is the DB-free fixture; pair with stub=True so
    # we never touch a real fetcher. build_smoke_app needs SOME cfg
    # shape — give it the legacy Mock and let the smoke-app builder
    # error if it touches fields; smoke is our cleanest fast-path here.
    cfg = MagicMock()
    cfg.aws.deployment_name = "test"
    cfg.db.table_prefix = "test"

    # Patch uvicorn inside the function's lazy-import scope.
    with patch.dict("sys.modules", {"uvicorn": fake_uvicorn}):
        # Also stub the heavy theme + smoke-app modules so the call
        # doesn't have to reach mkdocs / a real DB.
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
                tls_cert="/etc/ssl/cert.pem",
                tls_key="/etc/ssl/key.pem",
            )

    assert captured_kwargs.get("ssl_certfile") == "/etc/ssl/cert.pem"
    assert captured_kwargs.get("ssl_keyfile") == "/etc/ssl/key.pem"
    assert captured_kwargs.get("host") == "127.0.0.1"
    assert captured_kwargs.get("port") == 8765


def test_run_html_server_omits_tls_kwargs_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither flag is set, uvicorn.Config gets NO ssl_certfile /
    ssl_keyfile kwargs — uvicorn defaults to HTTP. Mirrors pre-DC.1
    behavior."""
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
    # DE.4 — explicitly disable cfg.app2.tls fallback so this test
    # genuinely exercises "no TLS anywhere" (MagicMock auto-vivifies
    # cfg.app2.tls.cert_path otherwise, which would fire the fallback).
    cfg.app2.tls = None

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
                tls_cert=None,
                tls_key=None,
            )

    assert "ssl_certfile" not in captured_kwargs
    assert "ssl_keyfile" not in captured_kwargs


def test_dashboards_cli_help_includes_tls() -> None:
    """`recon-gen dashboards --help` mentions TLS so operators can
    discover the option without reading source."""
    runner = CliRunner()
    result = runner.invoke(dashboards, ["--help"])
    assert result.exit_code == 0
    assert "--tls-cert" in result.output
    assert "--tls-key" in result.output


def test_studio_cli_help_includes_tls() -> None:
    """Same for studio."""
    runner = CliRunner()
    result = runner.invoke(studio, ["--help"])
    assert result.exit_code == 0
    assert "--tls-cert" in result.output
    assert "--tls-key" in result.output
