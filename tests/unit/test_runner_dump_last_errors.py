"""Unit tests for ``./run_tests.sh dump-last-errors`` (the triage
shortcut).

Builds synthetic ``runs/<id>/<variant>/<layer>/`` trees with fake
``cmd.json`` + ``stdout.log`` shapes and asserts the dump output:

- Clean run (all ``exit_code: 0``) → ``(no failing layers ...)``.
- Failing layer → header + env + per-test traceback block.
- Browser failure WITH capture artifacts → no missing-capture warning.
- Browser failure WITHOUT capture artifacts → AA.H.6 warning fires.
- ``--run`` arg picks the named run; default = latest by mtime.
- ``--variant`` arg narrows to one cell.
- Non-pytest layer failure → "Non-pytest failure" stdout-tail dump.
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from recon_gen._dev import runner as r


_RUN_ID_OLD = "20260101T000000Z-aaaaaaa"
_RUN_ID_NEW = "20260102T000000Z-bbbbbbb"


def _write_layer(
    run_dir: Path, variant: str, layer: str, *,
    exit_code: int, duration: float = 1.0,
    stdout: str = "",
    env: dict[str, str] | None = None,
) -> Path:
    """Write a synthetic per-cell layer dir."""
    layer_dir = run_dir / variant / layer
    layer_dir.mkdir(parents=True, exist_ok=True)
    cmd_json = {
        "layer": layer, "cmd": ["pytest", "tests/"],
        "cwd": "/x", "env_overrides": env or {},
        "exit_code": exit_code, "duration_seconds": duration,
    }
    (layer_dir / "cmd.json").write_text(json.dumps(cmd_json, indent=2))
    (layer_dir / "stdout.log").write_text(stdout)
    return layer_dir


def _dump(runs_dir: Path, *, run: str | None = None, variant: str | None = None) -> str:
    """Invoke cmd_dump_last_errors with RUNS_DIR pointed at the tmp tree."""
    args = argparse.Namespace(run=run, variant=variant)
    buf = io.StringIO()
    saved = r.RUNS_DIR
    try:
        # type: ignore[attr-defined]: Final hint is a hint, not a runtime lock
        r.RUNS_DIR = runs_dir  # type: ignore[misc, assignment]: Final hint isn't a runtime lock — monkey-patch for the test
        with redirect_stdout(buf):
            rc = r.cmd_dump_last_errors(args)
    finally:
        r.RUNS_DIR = saved  # type: ignore[misc, assignment]: restore the original Final-typed runs_dir
    assert rc == r.EXIT_SUCCESS, "dump-last-errors should always exit 0"
    return buf.getvalue()


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    """A pristine runs/ root under tmp_path."""
    target = tmp_path / "runs"
    target.mkdir()
    return target


# -- clean-run / latest-run path --------------------------------------------


def test_dump_clean_run_reports_no_failures(runs_dir: Path) -> None:
    """Every layer exit_code == 0 → "no failing layers" message."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(run_dir, "sp_pg_lo", "db", exit_code=0, stdout="42 passed")
    _write_layer(run_dir, "sp_pg_lo", "app2", exit_code=0, stdout="10 passed")
    out = _dump(runs_dir)
    assert "no failing layers" in out
    assert "Failing layers in 20260102" in out


def test_dump_picks_latest_by_mtime(runs_dir: Path) -> None:
    """Without --run, picks the run with most-recent mtime."""
    older = runs_dir / _RUN_ID_OLD
    newer = runs_dir / _RUN_ID_NEW
    older.mkdir()
    newer.mkdir()
    # Use os.utime to pin the mtimes so the test is deterministic
    # regardless of disk write order.
    import os
    import time

    now = time.time()
    os.utime(older, (now - 7200, now - 7200))
    os.utime(newer, (now, now))
    _write_layer(older, "sp_pg_lo", "db", exit_code=1, stdout="FAILED tests/old.py::test_old\n")
    _write_layer(newer, "sp_pg_lo", "db", exit_code=0, stdout="42 passed")
    out = _dump(runs_dir)
    assert "20260102" in out  # newer
    assert "20260101" not in out
    assert "no failing layers" in out


def test_dump_no_runs_dir_is_clean(tmp_path: Path) -> None:
    """``runs/`` absent entirely → friendly message, exit 0."""
    out = _dump(tmp_path / "does-not-exist")
    # Stdout is empty (warning goes to stderr); just confirm exit 0
    # via the assertion inside _dump.
    assert out == ""


# -- failing-layer path -----------------------------------------------------


_PYTEST_STDOUT_WITH_FAILURE = """\
============================= test session starts ==============================
collected 5 items

tests/e2e/test_x.py::test_a PASSED                                       [ 20%]
tests/e2e/test_x.py::test_b FAILED                                       [ 40%]

=================================== FAILURES ===================================
_______________________________ test_b _______________________________

    def test_b():
>       assert 1 == 2, "rows didn't match"
E       AssertionError: rows didn't match
E       assert 1 == 2

tests/e2e/test_x.py:42: AssertionError
=========================== short test summary info ============================
FAILED tests/e2e/test_x.py::test_b - AssertionError: rows didn't match
1 failed, 1 passed in 2.34s
"""


def test_dump_failing_layer_surfaces_header_env_and_traceback(
    runs_dir: Path,
) -> None:
    """A failing layer renders the header (variant/layer/exit/duration),
    the key env vars from cmd.json, and the per-failed-test traceback
    block extracted from stdout."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(
        run_dir, "sp_pg_aw", "browser",
        exit_code=1, duration=12.5,
        stdout=_PYTEST_STDOUT_WITH_FAILURE,
        env={
            "QS_GEN_DEPLOYMENT_NAME": "recon-sp_pg_aw",
            "QS_GEN_FUZZ_SEED": "42",
            "QS_GEN_TEST_L2_INSTANCE": "/x/y.yaml",
        },
    )
    out = _dump(runs_dir)
    assert "[sp_pg_aw/browser] exit=1 duration=12.5s" in out
    assert "QS_GEN_DEPLOYMENT_NAME=recon-sp_pg_aw" in out
    assert "QS_GEN_FUZZ_SEED=42" in out
    assert "1 FAILED test(s)" in out
    assert "tests/e2e/test_x.py::test_b" in out
    # The traceback body lands inside a code block.
    assert "AssertionError: rows didn't match" in out


def test_dump_non_pytest_failure_emits_stdout_tail(runs_dir: Path) -> None:
    """Layer exit_code != 0 but no ``FAILED ...`` lines → render the
    stdout tail instead of an empty failures section. Covers
    non-pytest crashes (Docker errors / AWS API exceptions / etc.)."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(
        run_dir, "sp_pg_lo", "deploy",
        exit_code=1, duration=5.0,
        stdout="docker-compose: error: container failed to start\n"
               "Error response from daemon: port already allocated\n",
    )
    out = _dump(runs_dir)
    assert "Non-pytest failure" in out
    assert "container failed to start" in out
    assert "port already allocated" in out


# -- AA.H.6 capture warning -------------------------------------------------


def test_dump_browser_failure_with_capture_dir_no_warning(
    runs_dir: Path,
) -> None:
    """A failed browser test WITH an artifact dir under
    ``<cell>/browser/<sanitized_test_id>/`` → no warning."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(
        run_dir, "sp_pg_aw", "browser",
        exit_code=1,
        stdout=_PYTEST_STDOUT_WITH_FAILURE,
    )
    # Build the sanitized capture dir matching the FAILED test's
    # sanitized nodeid (same algorithm as common.browser.helpers).
    nodeid = "tests/e2e/test_x.py::test_b"
    slug = (
        nodeid.replace("/", "_").replace("::", "__").replace(".py", "")
    )
    cap = run_dir / "sp_pg_aw" / "browser" / slug
    cap.mkdir(parents=True)
    (cap / "screenshot.png").write_bytes(b"fake-png")
    out = _dump(runs_dir)
    assert "AA.H.6 capture artifacts missing" not in out


def test_dump_browser_failure_without_capture_warns(runs_dir: Path) -> None:
    """A failed browser test with NO matching capture dir → warning
    fires with the failing nodeid listed."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(
        run_dir, "sp_pg_aw", "browser",
        exit_code=1,
        stdout=_PYTEST_STDOUT_WITH_FAILURE,
    )
    out = _dump(runs_dir)
    assert "AA.H.6 capture artifacts missing" in out
    assert "tests/e2e/test_x.py::test_b" in out
    assert "AA.H.10 wired the hook" in out  # the actionable hint


def test_dump_browser_failure_with_empty_capture_dir_warns(
    runs_dir: Path,
) -> None:
    """Capture dir exists but has none of the 6 expected files →
    still warns (an empty dir is as broken as a missing one)."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(
        run_dir, "sp_pg_aw", "browser",
        exit_code=1,
        stdout=_PYTEST_STDOUT_WITH_FAILURE,
    )
    nodeid = "tests/e2e/test_x.py::test_b"
    slug = (
        nodeid.replace("/", "_").replace("::", "__").replace(".py", "")
    )
    cap = run_dir / "sp_pg_aw" / "browser" / slug
    cap.mkdir(parents=True)
    # Empty dir — no expected files.
    out = _dump(runs_dir)
    assert "AA.H.6 capture artifacts missing" in out


def test_dump_non_browser_layer_skips_capture_check(runs_dir: Path) -> None:
    """The capture-status check is browser-only; a failing db layer
    doesn't trigger the warning (the helper takes the early return on
    ``layer_dir.name != 'browser'``)."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(
        run_dir, "sp_pg_lo", "db",
        exit_code=1,
        stdout=_PYTEST_STDOUT_WITH_FAILURE,
    )
    out = _dump(runs_dir)
    assert "AA.H.6 capture artifacts missing" not in out


# -- --run / --variant filters ----------------------------------------------


def test_dump_run_arg_targets_specific_run(runs_dir: Path) -> None:
    """``--run RUN_ID`` picks the named run regardless of mtime order."""
    older = runs_dir / _RUN_ID_OLD
    newer = runs_dir / _RUN_ID_NEW
    older.mkdir()
    newer.mkdir()
    _write_layer(older, "sp_pg_lo", "db", exit_code=1, stdout="FAILED tests/old.py::test_a\n")
    _write_layer(newer, "sp_pg_lo", "db", exit_code=0)
    out = _dump(runs_dir, run=_RUN_ID_OLD)
    assert "20260101" in out
    assert "20260102" not in out


def test_dump_missing_run_arg_returns_needs_operator(runs_dir: Path) -> None:
    """``--run RUN_ID`` for a non-existent run → EXIT_NEEDS_OPERATOR."""
    runs_dir_arg = runs_dir
    args = argparse.Namespace(run="nope", variant=None)
    saved = r.RUNS_DIR
    try:
        r.RUNS_DIR = runs_dir_arg  # type: ignore[misc, assignment]: Final hint isn't a runtime lock — monkey-patch for the test
        rc = r.cmd_dump_last_errors(args)
    finally:
        r.RUNS_DIR = saved  # type: ignore[misc, assignment]: restore the original Final-typed runs_dir
    assert rc == r.EXIT_NEEDS_OPERATOR


def test_dump_variant_arg_narrows_cells(runs_dir: Path) -> None:
    """``--variant NAME`` shows only the matching cell, hides others."""
    run_dir = runs_dir / _RUN_ID_NEW
    run_dir.mkdir()
    _write_layer(run_dir, "sp_pg_aw", "browser", exit_code=1, stdout="FAILED tests/aw.py::test_aw\n")
    _write_layer(run_dir, "sp_or_lo", "browser", exit_code=1, stdout="FAILED tests/or.py::test_or\n")
    out_aw = _dump(runs_dir, variant="sp_pg_aw")
    assert "sp_pg_aw/browser" in out_aw
    assert "sp_or_lo" not in out_aw
    out_or = _dump(runs_dir, variant="sp_or_lo")
    assert "sp_or_lo/browser" in out_or
    assert "sp_pg_aw" not in out_or


# -- prelude handling --------------------------------------------------------


def test_dump_includes_prelude_unit_failures(runs_dir: Path) -> None:
    """The unit-prelude lives under ``runs/<id>/_prelude/unit/`` (not
    a variant). A prelude failure should surface."""
    run_dir = runs_dir / _RUN_ID_NEW
    (run_dir / "_prelude" / "unit").mkdir(parents=True)
    _write_layer(
        run_dir, "_prelude", "unit",
        exit_code=1, stdout="FAILED tests/unit/test_x.py::test_a\n",
    )
    out = _dump(runs_dir)
    assert "[_prelude/unit]" in out
