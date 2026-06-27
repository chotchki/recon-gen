"""Unit tests for the ``triage`` + ``triage-down`` runner verbs.

Coverage:

- ``_infer_layer_from_nodeid`` path-prefix rules (per-tier subdirs,
  root-e2e parametrized files, unit/json/cli prefixes, audit+data
  fallback, unknowns, selector stripping, leading ``./`` stripping,
  absolute paths).
- argparse surface (nodeid required, ``--keep-*`` defaults).
- ``_screen_session_exists`` / ``_screen_kill`` mocked subprocess
  behaviors (idempotent on absent session, real failures surface).
- Triage state file round-trip + unparseable rejection.
- ``cmd_triage`` early-exit gates (existing session without --force,
  empty / absolute nodeid, unknown nodeid).
- ``cmd_triage_down`` gates (--yes required, missing state file is a
  successful no-op).
- Extraction-correctness checks for the ``_build_deploy_command``
  refactor + ``_setup_thin_chain_environment`` helper.
"""

from __future__ import annotations

import argparse
import io
import subprocess
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from recon_gen._dev import runner as r


# ---------------------------------------------------------------------------
# _infer_layer_from_nodeid — path-prefix table.
# ---------------------------------------------------------------------------


def test_infer_layer_qs_browser_subdir() -> None:
    """Rule 1: nodeids under tests/e2e/qs_browser/ -> qs_browser."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/qs_browser/test_inv_anomaly_qs.py::test_x"
    ) == "qs_browser"


def test_infer_layer_agreement_subdir() -> None:
    """Rule 2 (DW.3): nodeids under tests/e2e/agreement/ -> agreement."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/agreement/test_inv_anomaly_agreement.py::test_anomaly_agreement"
    ) == "agreement"


def test_infer_layer_qs_api_subdir() -> None:
    """Rule 3: nodeids under tests/e2e/qs_api/ -> qs_api."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/qs_api/test_describe_foo.py::test_y"
    ) == "qs_api"


def test_infer_layer_app2_subdir() -> None:
    """Rule 4: nodeids under tests/e2e/app2/ -> app2."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/app2/test_bv33_trainer.py::test_x"
    ) == "app2"


def test_infer_layer_db_subdir() -> None:
    """Rule 5: nodeids under tests/e2e/db/ -> db."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/db/test_dataset_sql_smoke.py::test_one"
    ) == "db"


@pytest.mark.parametrize(
    "filename",
    [
        "test_l1_filters.py",
        "test_l2ft_chains_dropdowns.py",
        "test_inv_drilldown.py",
        "test_exec_sheet_visuals.py",
        "test_dashboard_driver.py",
        "test_cq_picker_search_and_find.py",
        "test_studio_deploy_browser.py",
        "test_parameter_anchored_sheets.py",
        "test_db3_parity_snaps.py",
    ],
)
def test_infer_layer_root_e2e_parametrized(filename: str) -> None:
    """Rule 5: tests/e2e/<root parametrized file> → qs_browser.

    qs_browser is a strict superset of app2's prereqs, so default to
    the higher layer; operator can downshift via ``--layer=app2``.
    """
    nodeid = f"tests/e2e/{filename}::test_renders[qs]"
    assert r._infer_layer_from_nodeid(nodeid) == "qs_browser"


def test_infer_layer_root_e2e_parametrized_app2_param_still_qs_browser() -> None:
    """Even the [app2] callspec of a root-e2e parametrized test
    defaults to qs_browser (operator downshifts via --layer)."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/test_l1_filters.py::test_x[app2]"
    ) == "qs_browser"


@pytest.mark.parametrize(
    "prefix",
    [
        "tests/unit/",
        "tests/json/",
        "tests/cli/",
        "tests/docs/",
        "tests/schema/",
        "tests/l2/",
    ],
)
def test_infer_layer_unit_prefixes(prefix: str) -> None:
    """Rule 6: pytest-only trees -> unit."""
    nodeid = f"{prefix}test_foo.py::test_bar"
    assert r._infer_layer_from_nodeid(nodeid) == "unit"


@pytest.mark.parametrize("prefix", ["tests/audit/", "tests/data/"])
def test_infer_layer_audit_data_fallback(prefix: str) -> None:
    """Rule 7: gap-handling — audit/data return unit (safe floor)."""
    nodeid = f"{prefix}test_foo.py::test_bar"
    assert r._infer_layer_from_nodeid(nodeid) == "unit"


def test_infer_layer_unknown_returns_none() -> None:
    """Rule 8: unknown prefix returns None."""
    assert r._infer_layer_from_nodeid("tests/foo/test_bar.py::baz") is None


def test_infer_layer_unknown_root_e2e_returns_none() -> None:
    """tests/e2e/<not_a_parametrized_file> returns None."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/test_weird_file_not_in_list.py"
    ) is None


def test_infer_layer_handles_selectors_and_params() -> None:
    """Strip the ``::`` selector portion before prefix matching."""
    assert r._infer_layer_from_nodeid(
        "tests/e2e/db/test_x.py::test_y[param-1-2]"
    ) == "db"


def test_infer_layer_strips_leading_dot_slash() -> None:
    """./tests/... normalizes to the same answer as tests/..."""
    assert r._infer_layer_from_nodeid(
        "./tests/e2e/db/test_x.py"
    ) == r._infer_layer_from_nodeid("tests/e2e/db/test_x.py")


def test_infer_layer_rejects_absolute_path() -> None:
    """Absolute paths return None (cmd_triage maps to EXIT_CONFIG_ERROR)."""
    assert r._infer_layer_from_nodeid(
        "/Users/chotchki/repo/tests/e2e/db/test_x.py"
    ) is None


def test_infer_layer_empty_string_returns_none() -> None:
    """Empty input returns None."""
    assert r._infer_layer_from_nodeid("") is None


# ---------------------------------------------------------------------------
# argparse surface.
# ---------------------------------------------------------------------------


def test_argparse_triage_requires_nodeid() -> None:
    """``triage`` with no positional arg errors out."""
    parser = r._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["triage"])


def test_argparse_triage_parses_nodeid_and_flags() -> None:
    """Smoke check that argparse accepts the full flag surface."""
    parser = r._build_parser()
    ns = parser.parse_args([
        "triage",
        "tests/e2e/qs_browser/test_x.py::test_y",
        "--layer", "qs_browser",
        "--allow-dirty-deploy",
        "--force",
    ])
    assert ns.nodeid == "tests/e2e/qs_browser/test_x.py::test_y"
    assert ns.layer == "qs_browser"
    assert ns.allow_dirty_deploy is True
    assert ns.force is True


def test_argparse_triage_layer_choices_rejects_unknown() -> None:
    """``--layer=bogus`` is rejected by argparse choices."""
    parser = r._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "triage", "tests/unit/test_x.py::test_y", "--layer", "bogus",
        ])


def test_argparse_triage_down_default_no_keeps() -> None:
    """Both ``--keep-container`` and ``--keep-qs`` default to False."""
    parser = r._build_parser()
    ns = parser.parse_args(["triage-down", "--yes"])
    assert ns.yes is True
    assert ns.keep_container is False
    assert ns.keep_qs is False


def test_argparse_triage_down_yes_default_false() -> None:
    """``--yes`` defaults False; ``cmd_triage_down`` enforces it."""
    parser = r._build_parser()
    ns = parser.parse_args(["triage-down"])
    assert ns.yes is False


# ---------------------------------------------------------------------------
# _screen_session_exists / _screen_kill — subprocess mocks.
# ---------------------------------------------------------------------------


def _mk_completed(
    *, returncode: int = 0, stdout: str = "", stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def test_screen_session_exists_finds_running_tab_separated() -> None:
    """Standard screen -ls output with tab separator -> True."""
    fake_output = (
        "There is a screen on:\n"
        "\t12345.recon-gen-triage\t(Detached)\n"
        "1 Socket in /tmp/screens/S-user.\n"
    )
    with patch.object(
        r.subprocess, "run",
        return_value=_mk_completed(stdout=fake_output),
    ):
        assert r._screen_session_exists("recon-gen-triage") is True


def test_screen_session_exists_finds_running_space_separated() -> None:
    """Alternate format with space separator -> True."""
    fake_output = (
        "There is a screen on:\n"
        "12345.recon-gen-triage  (Detached)\n"
    )
    with patch.object(
        r.subprocess, "run",
        return_value=_mk_completed(stdout=fake_output),
    ):
        assert r._screen_session_exists("recon-gen-triage") is True


def test_screen_session_exists_handles_absent() -> None:
    """No-sockets output -> False (regardless of rc)."""
    with patch.object(
        r.subprocess, "run",
        return_value=_mk_completed(
            returncode=1, stdout="No Sockets found in /tmp/screens/S-user.\n",
        ),
    ):
        assert r._screen_session_exists("recon-gen-triage") is False


def test_screen_kill_idempotent_on_absent() -> None:
    """`screen -S X -X quit` with no matching session -> True (idempotent)."""
    with patch.object(
        r.subprocess, "run",
        return_value=_mk_completed(
            returncode=1, stderr="No screen session found.\n",
        ),
    ):
        assert r._screen_kill("recon-gen-triage") is True


def test_screen_kill_returns_true_on_clean_quit() -> None:
    """rc=0 -> True (session was running, now killed)."""
    with patch.object(
        r.subprocess, "run",
        return_value=_mk_completed(returncode=0),
    ):
        assert r._screen_kill("recon-gen-triage") is True


def test_screen_kill_returns_false_on_real_failure() -> None:
    """Non-zero rc with different stderr -> False (genuine failure)."""
    with patch.object(
        r.subprocess, "run",
        return_value=_mk_completed(
            returncode=255, stderr="cannot open /tmp/screens: permission denied\n",
        ),
    ):
        assert r._screen_kill("recon-gen-triage") is False


# ---------------------------------------------------------------------------
# Triage state file.
# ---------------------------------------------------------------------------


@pytest.fixture
def triage_state_tmp(tmp_path: Path):
    """Redirect the triage state file to a tmp location for the test."""
    fake_state = tmp_path / ".triage-state.json"
    saved = r._TRIAGE_STATE_FILE
    r._TRIAGE_STATE_FILE = fake_state  # type: ignore[misc, assignment]: Final hint isn't a runtime lock
    try:
        yield fake_state
    finally:
        r._TRIAGE_STATE_FILE = saved  # type: ignore[misc, assignment]: Final hint isn't a runtime lock — restore the saved sentinel after the fixture's teardown


def test_triage_state_write_read_roundtrip(triage_state_tmp: Path) -> None:
    """_write_triage_state + _read_triage_state preserves all fields."""
    state: dict[str, Any] = {
        "run_id": "20260614T120000Z-deadbee",
        "run_dir": "/runs/x",
        "nodeid": "tests/e2e/qs_browser/test_foo.py::test_bar",
        "layer": "qs_browser",
        "screen_name": "recon-gen-triage",
        "cfg_path": "/run/config.yaml",
        "deployed": True,
        "as_of_anchor": "2026-06-14",
    }
    r._write_triage_state(state)
    assert triage_state_tmp.is_file()
    loaded = r._read_triage_state()
    assert loaded == state


def test_triage_state_read_missing_returns_none(
    triage_state_tmp: Path,
) -> None:
    """Absent state file → None (signals idempotent teardown)."""
    assert not triage_state_tmp.exists()
    assert r._read_triage_state() is None


def test_triage_state_unparseable_raises(triage_state_tmp: Path) -> None:
    """Malformed JSON in state file → ValueError."""
    triage_state_tmp.write_text("{not valid json")
    with pytest.raises(ValueError, match="unparseable"):
        r._read_triage_state()


def test_triage_state_non_object_raises(triage_state_tmp: Path) -> None:
    """JSON array (not object) → ValueError."""
    triage_state_tmp.write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="not a JSON object"):
        r._read_triage_state()


# ---------------------------------------------------------------------------
# cmd_triage — early-exit gates (no container spin, no screen spawn).
# ---------------------------------------------------------------------------


def _mk_triage_args(
    nodeid: str = "tests/e2e/qs_browser/test_x.py::test_y",
    *,
    layer: str | None = None,
    allow_dirty_deploy: bool = False,
    force: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        nodeid=nodeid, layer=layer,
        allow_dirty_deploy=allow_dirty_deploy, force=force,
    )


def test_cmd_triage_existing_session_without_force_bails(
    triage_state_tmp: Path,
) -> None:
    """Existing recon-gen-triage session + no --force → EXIT_NEEDS_OPERATOR."""
    args = _mk_triage_args(force=False)
    with patch.object(r, "_screen_session_exists", return_value=True):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r.cmd_triage(args)
    assert rc == r.EXIT_NEEDS_OPERATOR
    out = buf.getvalue()
    assert "existing screen session" in out
    assert "--force" in out
    assert "screen -x recon-gen-triage" in out


def test_cmd_triage_empty_nodeid_returns_config_error(
    triage_state_tmp: Path,
) -> None:
    """Empty nodeid → EXIT_CONFIG_ERROR (no infer, no spin)."""
    args = _mk_triage_args(nodeid="")
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = r.cmd_triage(args)
    assert rc == r.EXIT_CONFIG_ERROR
    assert "repo-relative" in buf.getvalue()


def test_cmd_triage_absolute_nodeid_returns_config_error(
    triage_state_tmp: Path,
) -> None:
    """Absolute path nodeid → EXIT_CONFIG_ERROR."""
    args = _mk_triage_args(nodeid="/abs/tests/e2e/db/test_x.py::test_y")
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = r.cmd_triage(args)
    assert rc == r.EXIT_CONFIG_ERROR
    assert "repo-relative" in buf.getvalue()


def test_cmd_triage_unknown_nodeid_returns_config_error(
    triage_state_tmp: Path,
) -> None:
    """Unknown prefix + no --layer override → EXIT_CONFIG_ERROR."""
    args = _mk_triage_args(nodeid="tests/foo/test_x.py::test_y")
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = r.cmd_triage(args)
    assert rc == r.EXIT_CONFIG_ERROR
    msg = buf.getvalue()
    assert "cannot infer layer" in msg
    assert "--layer=" in msg


# ---------------------------------------------------------------------------
# cmd_triage_down — gates + idempotent teardown.
# ---------------------------------------------------------------------------


def _mk_triage_down_args(
    *, yes: bool = False, keep_container: bool = False,
    keep_qs: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        yes=yes, keep_container=keep_container, keep_qs=keep_qs,
    )


def test_cmd_triage_down_requires_yes(triage_state_tmp: Path) -> None:
    """Without --yes (and no env var) → EXIT_NEEDS_OPERATOR."""
    args = _mk_triage_down_args(yes=False)
    # Ensure RECON_GEN_RUNNER_YES isn't set in env.
    with patch.dict(r.os.environ, {}, clear=False):
        r.os.environ.pop("RECON_GEN_RUNNER_YES", None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = r.cmd_triage_down(args)
    assert rc == r.EXIT_NEEDS_OPERATOR
    assert "destructive" in buf.getvalue()


def test_cmd_triage_down_no_state_file_succeeds(
    triage_state_tmp: Path,
) -> None:
    """No state file → EXIT_SUCCESS (idempotent no-op)."""
    args = _mk_triage_down_args(yes=True)
    assert not triage_state_tmp.exists()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = r.cmd_triage_down(args)
    assert rc == r.EXIT_SUCCESS
    out = buf.getvalue()
    assert "no active triage state" in out
    assert "nothing to do" in out


def test_cmd_triage_down_unparseable_state_returns_needs_operator(
    triage_state_tmp: Path,
) -> None:
    """Malformed state file → EXIT_NEEDS_OPERATOR (don't silently fall back)."""
    triage_state_tmp.write_text("garbage{")
    args = _mk_triage_down_args(yes=True)
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = r.cmd_triage_down(args)
    assert rc == r.EXIT_NEEDS_OPERATOR
    assert "unparseable" in buf.getvalue()


def test_cmd_triage_down_kills_screen_and_skips_qs_when_not_deployed(
    triage_state_tmp: Path,
) -> None:
    """When state says deployed=False, QS sweep is skipped + screen killed."""
    state: dict[str, Any] = {
        "run_id": "20260614T120000Z-deadbee",
        "run_dir": "/runs/x",
        "nodeid": "tests/e2e/db/test_foo.py::test_bar",
        "layer": "db",
        "screen_name": "recon-gen-triage",
        "cfg_path": "/run/config.yaml",
        "deployed": False,
        "as_of_anchor": "2026-06-14",
    }
    r._write_triage_state(state)
    args = _mk_triage_down_args(yes=True, keep_container=True)
    with (
        patch.object(r, "_screen_kill", return_value=True) as mock_kill,
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r.cmd_triage_down(args)
    assert rc == r.EXIT_SUCCESS
    mock_kill.assert_called_once_with(r._TRIAGE_SCREEN_NAME)
    out = buf.getvalue()
    assert "killing screen session" in out
    assert "screen session terminated" in out
    assert "skipping QS sweep" in out
    assert "--keep-container" in out
    # State file must be removed.
    assert not triage_state_tmp.exists()


def test_cmd_triage_down_keep_qs_skips_sweep(
    triage_state_tmp: Path,
) -> None:
    """--keep-qs skips the QS sweep even when deployed=True."""
    state: dict[str, Any] = {
        "run_id": "20260614T120000Z-deadbee",
        "run_dir": "/runs/x",
        "nodeid": "tests/e2e/qs_browser/test_foo.py::test_bar",
        "layer": "qs_browser",
        "screen_name": "recon-gen-triage",
        "cfg_path": "/run/config.yaml",
        "deployed": True,
        "as_of_anchor": "2026-06-14",
    }
    r._write_triage_state(state)
    args = _mk_triage_down_args(yes=True, keep_qs=True, keep_container=True)
    with (
        patch.object(r, "_screen_kill", return_value=True),
        patch.object(r, "_triage_qs_sweep") as mock_sweep,
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r.cmd_triage_down(args)
    assert rc == r.EXIT_SUCCESS
    mock_sweep.assert_not_called()
    assert "skipping QS resource sweep" in buf.getvalue()
    assert not triage_state_tmp.exists()


# ---------------------------------------------------------------------------
# Extraction-correctness — _build_deploy_command parity.
# ---------------------------------------------------------------------------


def test_build_deploy_command_returns_none_when_l2_missing(
    tmp_path: Path,
) -> None:
    """No RECON_GEN_TEST_L2_INSTANCE in env → None."""
    variant_env: dict[str, str] = {
        r.RECON_GEN_CONFIG.name: "/path/to/cfg.yaml",
    }
    result = r._build_deploy_command(variant_env, tmp_path)
    assert result is None


def test_build_deploy_command_with_cfg_and_l2_returns_cmd(
    tmp_path: Path,
) -> None:
    """When cfg + L2 both present, returns the expected argv shape."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("placeholder")
    l2 = tmp_path / "l2.yaml"
    l2.write_text("placeholder")
    variant_env = {
        r.RECON_GEN_CONFIG.name: str(cfg),
        r.RECON_GEN_TEST_L2_INSTANCE.name: str(l2),
    }
    result = r._build_deploy_command(variant_env, tmp_path)
    assert result is not None
    cmd, env_addl = result
    assert "json" in cmd
    assert "apply" in cmd
    assert "--execute" in cmd
    assert str(cfg) in cmd
    assert str(l2) in cmd
    assert env_addl == {}
    # out_dir must have been created.
    assert (tmp_path / "deploy" / "out").is_dir()


def test_build_deploy_command_prefers_qs_cfg_over_local_cfg(
    tmp_path: Path,
) -> None:
    """When both RECON_GEN_QS_CONFIG and RECON_GEN_CONFIG are set, QS wins."""
    local_cfg = tmp_path / "local.yaml"
    qs_cfg = tmp_path / "qs.yaml"
    local_cfg.write_text("placeholder")
    qs_cfg.write_text("placeholder")
    l2 = tmp_path / "l2.yaml"
    l2.write_text("placeholder")
    variant_env = {
        r.RECON_GEN_CONFIG.name: str(local_cfg),
        r.RECON_GEN_QS_CONFIG.name: str(qs_cfg),
        r.RECON_GEN_TEST_L2_INSTANCE.name: str(l2),
    }
    result = r._build_deploy_command(variant_env, tmp_path)
    assert result is not None
    cmd, _env = result
    assert str(qs_cfg) in cmd
    assert str(local_cfg) not in cmd


def test_deploy_retired_from_layers_tuple() -> None:
    """DI phase: ``deploy`` is gone from the LAYERS chain.

    The session-autouse ``qs_deployed`` fixture in
    tests/e2e/conftest.py owns QS deploy; qs_api inherits as the
    first AWS-touching chain layer. Locks the design lock here so
    a future re-introduction of the deploy chain layer trips this
    test instead of silently splitting the deploy code path.
    """
    assert "deploy" not in r.LAYERS
    assert r.LAYERS == (
        "unit", "db", "app2", "agreement", "qs_api", "qs_browser",
    )


def test_aws_touching_layers_starts_at_qs_api() -> None:
    """DI phase: AWS_TOUCHING_LAYERS = (qs_api, qs_browser).

    The qs_deployed fixture fires when a session's collected tests
    pull in AWS-dependent fixtures (qs_client / qs_driver / etc.) —
    which only the qs_api + qs_browser tiers do. Db + app2 tiers — and
    the DW.3 ``agreement`` tier, a pure artifact reader — inherit
    tests/e2e/conftest.py but their fixture closures don't touch AWS,
    so qs_deployed bails via _session_needs_aws.
    """
    assert r.AWS_TOUCHING_LAYERS == ("qs_api", "qs_browser")
    assert "deploy" not in r.AWS_TOUCHING_LAYERS
    assert "agreement" not in r.AWS_TOUCHING_LAYERS


def test_layer_command_deploy_arm_removed(tmp_path: Path) -> None:
    """DI phase: ``_layer_command("deploy", ...)`` returns None
    because the deploy arm is gone. Locks the design lock so a
    future re-introduction trips this test.
    """
    variant_env = {
        r.RECON_GEN_CONFIG.name: str(tmp_path / "cfg.yaml"),
        r.RECON_GEN_TEST_L2_INSTANCE.name: str(tmp_path / "l2.yaml"),
    }
    assert r._layer_command(
        "deploy", tmp_path, variant_env=variant_env,
    ) is None


def test_is_aws_touching_layer_qs_api_and_later() -> None:
    """DI phase: dirty-tree refusal triggers on qs_api and qs_browser
    (not earlier). Renamed from _is_deploy_or_later.
    """
    assert r._is_aws_touching_layer("qs_api") is True
    assert r._is_aws_touching_layer("qs_browser") is True
    assert r._is_aws_touching_layer("app2") is False
    assert r._is_aws_touching_layer("db") is False
    assert r._is_aws_touching_layer("unit") is False


def test_chain_through_qs_browser_skips_deploy() -> None:
    """``chain_through("qs_browser")`` no longer transits ``deploy``.

    Concrete proof that ``up_to=qs_browser`` is a 6-layer chain
    post-DW.3 (the ``agreement`` tier slotted in before qs_api). The
    qs_deployed fixture absorbs the deploy work into the qs_api /
    qs_browser session at start.
    """
    chain = r.chain_through("qs_browser")
    assert chain == [
        "unit", "db", "app2", "agreement", "qs_api", "qs_browser",
    ]
    assert "deploy" not in chain


def test_chain_through_qs_api_is_5_layers() -> None:
    """``up_to=qs_api`` runs the full chain through qs_api — 5 layers
    post-DW.3 (``agreement`` precedes qs_api)."""
    chain = r.chain_through("qs_api")
    assert chain == ["unit", "db", "app2", "agreement", "qs_api"]
    assert "deploy" not in chain


def test_chain_through_agreement_is_aws_free() -> None:
    """``up_to=agreement`` stops short of the AWS-touching QS tiers —
    the DW.3 design intent (the supported gate post-QuickSight)."""
    chain = r.chain_through("agreement")
    assert chain == ["unit", "db", "app2", "agreement"]
    assert "qs_api" not in chain
    assert "qs_browser" not in chain
    assert not any(layer in r.AWS_TOUCHING_LAYERS for layer in chain)


# ---------------------------------------------------------------------------
# cmd_triage — DI phase: no inline container spin / deploy spawn.
# ---------------------------------------------------------------------------


def test_cmd_triage_state_file_omits_run_id_layer_as_of_anchor(
    triage_state_tmp: Path,
) -> None:
    """DI phase: triage state file slimmed. ``run_id`` / ``layer`` /
    ``as_of_anchor`` were dropped (none read by triage-down). Only the
    fields actually consumed downstream are persisted.
    """
    # Read-side parity — _read_triage_state still accepts any dict
    # shape; we're locking the WRITER's shape via cmd_triage's body.
    # Sniff the source for the field names the writer uses; the
    # writer body is small enough that a string-match is sufficient
    # signal without a full integration spin.
    import inspect
    cmd_triage_source = inspect.getsource(r.cmd_triage)
    state_block_start = cmd_triage_source.index("state: dict[str, Any] = {")
    state_block_end = cmd_triage_source.index("}", state_block_start)
    state_block = cmd_triage_source[state_block_start:state_block_end]
    # Slim shape — these stay.
    assert '"run_dir"' in state_block
    assert '"nodeid"' in state_block
    assert '"screen_name"' in state_block
    assert '"cfg_path"' in state_block
    assert '"qs_cfg_path"' in state_block
    assert '"deployed"' in state_block
    # Slimmed away.
    assert '"run_id"' not in state_block
    assert '"layer"' not in state_block
    assert '"as_of_anchor"' not in state_block


def test_cmd_triage_body_drops_inline_deploy_block() -> None:
    """DI phase: cmd_triage no longer calls ``_build_deploy_command``
    or ``_spawn_with_tee`` from inside its body. Deploy is owned by
    the session-autouse qs_deployed fixture in tests/e2e/conftest.py;
    cmd_triage's body purely sets up env + spawns the screen session
    that fires pytest (which fires the fixture).
    """
    import inspect
    cmd_triage_source = inspect.getsource(r.cmd_triage)
    # The old block called these helpers directly from cmd_triage's
    # body; deploy retirement removed those callsites.
    assert "_build_deploy_command(" not in cmd_triage_source, (
        "cmd_triage should not call _build_deploy_command — deploy "
        "is owned by tests/e2e/conftest.py::qs_deployed."
    )
    # _spawn_with_tee survives elsewhere in cmd_triage's flow (for
    # things like sweep / seed); the assertion that proves deploy is
    # gone is the _build_deploy_command absence above.


def test_cmd_triage_no_inline_deploy_step_log_string() -> None:
    """DI phase: the operator-facing deploy-step banner
    (``runner: deploying QS resources``) is gone from cmd_triage.
    The qs_deployed fixture prints its own banner from inside the
    pytest session.
    """
    import inspect
    cmd_triage_source = inspect.getsource(r.cmd_triage)
    assert "deploying QS resources" not in cmd_triage_source
