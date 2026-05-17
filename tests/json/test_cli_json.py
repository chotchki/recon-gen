"""Unit tests for the ``recon-gen json`` CLI surface.

Exercises the four sub-commands (``apply`` / ``clean`` / ``test`` /
``probe``) through Click's ``CliRunner`` with the per-app builders +
deploy + cleanup + probe helpers patched out. The tests verify the
CLI's orchestration responsibility — argument parsing, the
``--execute`` opt-in, the auto-emit-datasource gate, error
conversion to ``ClickException`` — without re-exercising the
underlying generator (covered by the per-app contract suites).
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from recon_gen.cli.json import json_


def _make_yaml_config(tmp_path: Path) -> Path:
    """Write a minimal config.yaml the CLI loader will accept."""
    # Z.C — deployment_name + db_table_prefix are required cfg fields.
    body = {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "deployment_name": "recon-cli-test",
        "db_table_prefix": "test",
        "datasource_arn": (
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
    }
    p = tmp_path / "config.yaml"
    p.write_text(_json.dumps(body), encoding="utf-8")
    return p


def _make_demo_yaml_config(tmp_path: Path) -> Path:
    """Like ``_make_yaml_config`` but sets ``demo_database_url`` —
    triggers the V.1.a auto-emit-datasource path."""
    body = {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "deployment_name": "recon-cli-demo",
        "db_table_prefix": "test",
        "demo_database_url": "postgresql://u:p@h:5432/d",
    }
    p = tmp_path / "config.yaml"
    p.write_text(_json.dumps(body), encoding="utf-8")
    return p


def _patch_generators(monkeypatch) -> dict[str, list]:
    """Replace the four per-app generator helpers with no-op spies that
    record their args. The underlying generator is exercised by the
    per-app contract test suites — here we only assert the CLI hands
    each generator the right config + output dir."""
    calls: dict[str, list] = {
        "investigation": [], "executives": [],
        "l1_dashboard": [], "l2_flow_tracing": [],
    }

    def _spy(name: str):
        def fn(config: str, output: str, *, l2_instance_path: str | None = None):
            calls[name].append((config, output, l2_instance_path))
        return fn

    import recon_gen.cli._app_builders as ab
    monkeypatch.setattr(ab, "_generate_investigation",  _spy("investigation"))
    monkeypatch.setattr(ab, "_generate_executives",     _spy("executives"))
    monkeypatch.setattr(ab, "_generate_l1_dashboard",   _spy("l1_dashboard"))
    monkeypatch.setattr(ab, "_generate_l2_flow_tracing",_spy("l2_flow_tracing"))
    return calls


# -- json apply --------------------------------------------------------------


def test_apply_without_execute_writes_jsons_and_skips_deploy(
    tmp_path, monkeypatch,
):
    """Default behavior: emit JSON to ``out/`` (or ``-o DIR``), do NOT
    talk to AWS. The bottom-of-output line must announce that
    ``--execute`` is the next step."""
    cfg = _make_yaml_config(tmp_path)
    out_dir = tmp_path / "out"
    calls = _patch_generators(monkeypatch)
    deploy_called: list[Any] = []
    import recon_gen.common.deploy as dep
    monkeypatch.setattr(dep, "deploy", lambda *a, **k: deploy_called.append((a, k)) or 0)

    rc = CliRunner().invoke(json_, [
        "apply", "-c", str(cfg), "-o", str(out_dir),
    ])
    assert rc.exit_code == 0, rc.output
    # Each of the four app generators got called exactly once.
    assert len(calls["investigation"]) == 1
    assert len(calls["executives"]) == 1
    assert len(calls["l1_dashboard"]) == 1
    assert len(calls["l2_flow_tracing"]) == 1
    # Deploy was NOT invoked — no --execute.
    assert deploy_called == []
    # User got the "re-run with --execute" hint.
    assert "Re-run with --execute" in rc.output


def test_apply_with_execute_invokes_deploy(tmp_path, monkeypatch):
    cfg = _make_yaml_config(tmp_path)
    _patch_generators(monkeypatch)
    deploy_calls: list[tuple] = []

    def _spy_deploy(cfg_arg, out_arg, app_list):
        deploy_calls.append((cfg_arg, out_arg, list(app_list)))
        return 0

    import recon_gen.common.deploy as dep
    monkeypatch.setattr(dep, "deploy", _spy_deploy)

    rc = CliRunner().invoke(json_, [
        "apply", "-c", str(cfg), "-o", str(tmp_path / "out"), "--execute",
    ])
    assert rc.exit_code == 0, rc.output
    assert len(deploy_calls) == 1
    cfg_arg, out_arg, app_list = deploy_calls[0]
    # APPS list must include all four bundled apps; ordering is the
    # CLI's source-of-truth.
    assert set(app_list) == {
        "investigation", "executives", "l1-dashboard", "l2-flow-tracing",
    }
    # Output dir round-trips.
    assert str(out_arg).endswith("out")


def test_apply_with_execute_propagates_deploy_failure(tmp_path, monkeypatch):
    """A non-zero deploy exit becomes a ``ClickException`` so the shell
    sees a non-zero CLI exit too — the ``--execute`` contract."""
    cfg = _make_yaml_config(tmp_path)
    _patch_generators(monkeypatch)
    import recon_gen.common.deploy as dep
    monkeypatch.setattr(dep, "deploy", lambda *_a, **_k: 7)

    rc = CliRunner().invoke(json_, [
        "apply", "-c", str(cfg), "-o", str(tmp_path / "out"), "--execute",
    ])
    assert rc.exit_code != 0
    assert "exit code 7" in rc.output


def test_apply_demo_database_url_auto_emits_datasource_json(
    tmp_path, monkeypatch,
):
    """V.1.a — when ``demo_database_url`` is set (and no explicit
    ``datasource_arn``), the derived ARN is one we own, so the deploy
    expects a ``datasource.json`` next to the dataset JSONs. Without
    auto-emit, a single-app deploy would orphan the shared datasource
    (the bug #263 backlog item). Asserts the file lands; defers
    content correctness to the build_datasource unit tests.

    Strip the ``RECON_GEN_DATASOURCE_ARN`` env fallback first: an ambient
    value (``tests/audit/test_dashboard_extract.py`` sets one via
    module-level ``os.environ.setdefault``, and pytest collects that
    module before this one in a full run) would leak into the loader's
    env-override path, populate ``cfg.datasource_arn`` from the env, and
    flip ``datasource_arn_was_derived`` to False — so the auto-emit gate
    wouldn't fire and the file wouldn't land. Same defensive pattern as
    ``test_apply_no_demo_database_url_skips_datasource_emit`` below."""
    from recon_gen.common.env_keys import RECON_GEN_DATASOURCE_ARN
    monkeypatch.delenv(RECON_GEN_DATASOURCE_ARN.name, raising=False)

    cfg = _make_demo_yaml_config(tmp_path)
    out_dir = tmp_path / "out"
    _patch_generators(monkeypatch)
    import recon_gen.common.deploy as dep
    monkeypatch.setattr(dep, "deploy", lambda *_a, **_k: 0)

    rc = CliRunner().invoke(json_, [
        "apply", "-c", str(cfg), "-o", str(out_dir),
    ])
    assert rc.exit_code == 0, rc.output
    ds_path = out_dir / "datasource.json"
    assert ds_path.is_file()
    payload = _json.loads(ds_path.read_text())
    assert "DataSourceId" in payload


def test_apply_no_demo_database_url_skips_datasource_emit(
    tmp_path, monkeypatch,
):
    """When the integrator uses their own pre-existing datasource
    (production deploys), don't write a ``datasource.json`` —
    deploy would otherwise overwrite the customer-managed resource."""
    # Strip the env-var fallback so the cfg yaml's missing
    # demo_database_url is what actually drives the test. Otherwise
    # an ambient RECON_GEN_DEMO_DATABASE_URL (e.g. set by the runner in
    # CI mode for the db layer) leaks into the loader's env-fallback
    # path and quietly populates cfg.demo_database_url.
    from recon_gen.common.env_keys import RECON_GEN_DEMO_DATABASE_URL
    monkeypatch.delenv(RECON_GEN_DEMO_DATABASE_URL.name, raising=False)

    cfg = _make_yaml_config(tmp_path)  # no demo_database_url
    out_dir = tmp_path / "out"
    _patch_generators(monkeypatch)
    import recon_gen.common.deploy as dep
    monkeypatch.setattr(dep, "deploy", lambda *_a, **_k: 0)

    rc = CliRunner().invoke(json_, [
        "apply", "-c", str(cfg), "-o", str(out_dir), "--execute",
    ])
    assert rc.exit_code == 0, rc.output
    assert not (out_dir / "datasource.json").exists()


# -- json clean --------------------------------------------------------------


def test_clean_without_execute_calls_run_cleanup_in_dry_run(
    tmp_path, monkeypatch,
):
    """Default: dry-run, ``skip_confirm=True`` (the ``--execute``
    flag itself is the confirmation; no extra prompt either way)."""
    cfg = _make_yaml_config(tmp_path)
    cleanup_calls: list[dict] = []

    def _spy(cfg_arg, out_dir, **kwargs) -> int:
        cleanup_calls.append({"out_dir": out_dir, **kwargs})
        return 0

    import recon_gen.common.cleanup as cu
    monkeypatch.setattr(cu, "run_cleanup", _spy)

    rc = CliRunner().invoke(json_, [
        "clean", "-c", str(cfg), "-o", str(tmp_path / "out"),
    ])
    assert rc.exit_code == 0, rc.output
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]["dry_run"] is True
    assert cleanup_calls[0]["skip_confirm"] is True


def test_clean_with_execute_runs_cleanup_for_real(tmp_path, monkeypatch):
    cfg = _make_yaml_config(tmp_path)
    cleanup_calls: list[dict] = []
    import recon_gen.common.cleanup as cu
    monkeypatch.setattr(
        cu, "run_cleanup",
        lambda *_a, **kwargs: cleanup_calls.append(kwargs) or 0,
    )

    rc = CliRunner().invoke(json_, [
        "clean", "-c", str(cfg), "-o", str(tmp_path / "out"), "--execute",
    ])
    assert rc.exit_code == 0
    assert cleanup_calls[0]["dry_run"] is False
    # ``purge_all`` defaults False — preserves the carve-out semantics
    # for everyday cleanup.
    assert cleanup_calls[0]["purge_all"] is False


def test_clean_all_flag_threads_purge_all_through(tmp_path, monkeypatch):
    """v8.6.13 — ``--all`` opts into purge mode (ignore out/, sweep
    every matching resource including the live deploy)."""
    cfg = _make_yaml_config(tmp_path)
    cleanup_calls: list[dict] = []
    import recon_gen.common.cleanup as cu
    monkeypatch.setattr(
        cu, "run_cleanup",
        lambda *_a, **kwargs: cleanup_calls.append(kwargs) or 0,
    )

    rc = CliRunner().invoke(json_, [
        "clean", "-c", str(cfg), "-o", str(tmp_path / "out"),
        "--all", "--execute",
    ])
    assert rc.exit_code == 0
    assert cleanup_calls[0]["purge_all"] is True
    assert cleanup_calls[0]["dry_run"] is False


def test_clean_all_without_execute_is_dry_run(tmp_path, monkeypatch):
    """``--all`` alone (no ``--execute``) previews what purge would
    sweep without deleting — independent flags."""
    cfg = _make_yaml_config(tmp_path)
    cleanup_calls: list[dict] = []
    import recon_gen.common.cleanup as cu
    monkeypatch.setattr(
        cu, "run_cleanup",
        lambda *_a, **kwargs: cleanup_calls.append(kwargs) or 0,
    )

    rc = CliRunner().invoke(json_, [
        "clean", "-c", str(cfg), "-o", str(tmp_path / "out"), "--all",
    ])
    assert rc.exit_code == 0
    assert cleanup_calls[0]["purge_all"] is True
    assert cleanup_calls[0]["dry_run"] is True


def test_clean_propagates_cleanup_failures(tmp_path, monkeypatch):
    cfg = _make_yaml_config(tmp_path)
    import recon_gen.common.cleanup as cu
    monkeypatch.setattr(cu, "run_cleanup", lambda *_a, **_k: 3)

    rc = CliRunner().invoke(json_, [
        "clean", "-c", str(cfg), "-o", str(tmp_path / "out"),
    ])
    assert rc.exit_code != 0
    assert "exit code 3" in rc.output


# -- json probe --------------------------------------------------------------


def test_probe_iterates_every_app_and_prints_a_report(
    tmp_path, monkeypatch,
):
    """``json probe`` reads ``-o DIR`` to find each app's
    ``DashboardId`` and walks the dashboards via
    ``probe_dashboard``. Patch the boto-touching path; assert
    one probe per declared app."""
    cfg = _make_yaml_config(tmp_path)

    import recon_gen.cli._app_builders as ab
    monkeypatch.setattr(
        ab, "_dashboard_id_for_app",
        lambda app, out: f"qs-{app}-dashboard-id",
    )

    seen: list[str] = []

    import recon_gen.common.probe as prob
    monkeypatch.setattr(
        prob, "probe_dashboard",
        lambda **kw: seen.append(kw["dashboard_id"]) or [],
    )
    monkeypatch.setattr(prob, "format_report", lambda did, _r: f"report:{did}")

    rc = CliRunner().invoke(json_, [
        "probe", "-c", str(cfg), "-o", str(tmp_path / "out"),
    ])
    assert rc.exit_code == 0, rc.output
    # Four apps probed, one call each.
    assert len(seen) == 4
    # Each emitted a "report:<id>" line.
    for did in seen:
        assert f"report:{did}" in rc.output


# -- json test ---------------------------------------------------------------


def test_test_subcommand_invokes_pytest_and_pyright(monkeypatch):
    """``json test`` shells out to pytest + pyright; both must run.
    Failures bubble up as a single ClickException listing what
    failed."""
    invoked: list[list[str]] = []

    def _fake_call(argv) -> int:
        invoked.append(list(argv))
        return 0  # both pass

    import subprocess as _sp
    monkeypatch.setattr(_sp, "call", _fake_call)

    rc = CliRunner().invoke(json_, ["test"])
    assert rc.exit_code == 0, rc.output
    # Both subprocesses fired; pytest first, then pyright.
    cmds = [argv[1:3] for argv in invoked]
    assert ["-m", "pytest"] in cmds
    assert ["-m", "pyright"] in cmds


def test_test_subcommand_aggregates_failures_into_one_error(monkeypatch):
    import subprocess as _sp
    # Both fail.
    monkeypatch.setattr(_sp, "call", lambda _a: 1)

    rc = CliRunner().invoke(json_, ["test"])
    assert rc.exit_code != 0
    # The failure listing names BOTH tools, not just the first one.
    assert "pytest" in rc.output and "pyright" in rc.output


@pytest.mark.parametrize("subcmd", ["apply", "clean", "test", "probe"])
def test_subcommand_help_renders(subcmd):
    """Cheap sanity: every sub-command's --help renders without
    raising. Catches Click decorator regressions early."""
    rc = CliRunner().invoke(json_, [subcmd, "--help"])
    assert rc.exit_code == 0
