"""Top-level conftest — Y.2.gate.c.2 timings capture hook.

When invoked under the test layer chain runner, ``RECON_GEN_RUN_DIR`` and
``RECON_GEN_LAYER`` are set in the env (see ``runner.py::_layer_command``);
``pytest_runtest_makereport`` writes one JSONL line per test ``call`` phase
into ``$RECON_GEN_RUN_DIR/timings/<layer>.jsonl``.

When invoked directly (``pytest tests/...`` without the runner), both env
vars are unset and the hook is a no-op — direct invocation behavior is
unchanged.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_GEN_FUZZ_SEED,
    RECON_GEN_LAYER,
    RECON_GEN_RUN_DIR,
)


def pytest_configure(config: Any) -> None:
    """Pin a session-stable fuzz seed + redirect runner.RUNS_DIR to a
    session tmp dir so tests don't pollute the real ``runs/``.

    **Fuzz seed pin (j.6.fix).** Without this, modules that materialize
    a fuzz seed at import time (e.g.,
    ``tests/data/test_l2_seed_contract.py::FUZZ_SEED``) compute a
    fresh ``secrets.randbits(32)`` PER WORKER PROCESS — each worker
    then collects ``[fuzz-seed-NNNNN]`` parametrize IDs with a
    different N, and pytest-xdist refuses to start with "Different
    tests were collected between gw0 and gwN". Fix: controller sets
    ``RECON_GEN_FUZZ_SEED`` once at session start; xdist passes env vars
    from controller to worker subprocesses via execnet, so workers
    inherit the same seed. Operator-pinned seeds
    (``RECON_GEN_FUZZ_SEED=12345 pytest ...``) flow through unchanged.

    **runs/ isolation (#741).** Tests that call
    ``runner.main(["up_to=..."])`` (e.g. ``test_cmd_up_to_*``)
    create real run dirs under the operator's ``runs/`` and call
    ``prune_old_runs``. Under matrix parallel fan-out
    (13 cells × ~16 xdist workers = ~200 invocations) this generated
    200+ transient run dirs and 200+ concurrent prune races; in-flight
    cells' ``_synth_l2.yaml`` files got nuked by sibling pruners.
    Fix: monkeypatch the runner's ``RUNS_DIR`` module attr to a
    session-tmp dir. All in-process ``runner.main`` calls land their
    fake runs in tmp; the operator's real ``runs/`` stays clean; prune
    races vanish (all workers prune their own session-tmp tree).
    Tests that explicitly override RUNS_DIR per-test (with
    ``monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)``) still win
    by pytest fixture-scope precedence.
    """
    if RECON_GEN_FUZZ_SEED.get_or_none() is None:
        os.environ[RECON_GEN_FUZZ_SEED.name] = str(secrets.randbits(32))

    # Y.7-followup — when pytest-xdist is active and `-n` workers were
    # requested (xdist then defaults `dist` to "load"), bump to "loadgroup"
    # so `@pytest.mark.xdist_group` markers pin grouped tests to a single
    # worker. Needed because xdist re-runs module/session-scoped fixtures
    # ONCE PER WORKER: a module-scoped fixture that mutates a shared
    # external resource (e.g. test_audit_dashboard_agreement.py's
    # seeded_audit re-applying the Oracle schema) races across workers —
    # Oracle's DDL auto-commits, so the second worker's CREATE TABLE hits
    # ORA-00955 while the first worker's run is still in flight. Done here,
    # NOT via pyproject `addopts = "--dist ..."`, because a no-xdist env
    # (the CI `test` job, the wheel-smoke job) chokes on an unrecognized
    # `--dist`. An explicit `--dist <mode>` on the command line still wins
    # (only the implicit "load" default — set by `-n` alone — gets bumped).
    if config.pluginmanager.hasplugin("xdist") and getattr(config.option, "dist", "no") == "load":
        config.option.dist = "loadgroup"

    # #741 — redirect runner.RUNS_DIR so in-process runner.main calls
    # land in session tmp instead of the operator's real runs/. Lazy
    # import to avoid circular-import surprises at conftest load time.
    #
    # The _dev package is excluded from the customer wheel
    # (pyproject.toml::tool.setuptools.packages.find::exclude). When
    # this conftest runs against the installed wheel (release.yml's
    # `Smoke test wheel` job), _dev is absent — and that's fine: no
    # test reachable from the wheel can call runner.main, so there's
    # no runs/ pollution to guard against. Swallow the ImportError.
    try:
        from recon_gen._dev import runner  # noqa: PLC0415 — lazy: only patch when tests are actually running
    except ImportError:
        return
    session_runs_tmp = Path(tempfile.mkdtemp(prefix="qs-gen-test-runs-"))  # typing-smell: ignore[qs-gen-prefix]: tempdir disambiguator, not an AWS resource ID
    runner.RUNS_DIR = session_runs_tmp  # type: ignore[misc]: patching module-level Final at session start; the Final mark documents intent for prod, tests legitimately rebind


# ---------------------------------------------------------------------------
# SQLite connection-leak detector (opt-in via RECON_GEN_SQLITE_LEAK_GATE=1)
# ---------------------------------------------------------------------------
#
# Surfaced 2026-05-27 — aiosqlite#258 (still open) leaks thread locks on
# per-request connect+close, and `with sqlite3.connect(...)` (Python's
# sqlite3 context manager handles transactions, NOT close) is a common
# foot-gun. Both shapes accumulate live Connection objects until OOM —
# explains the local browser-tier OOM during the 13-variant sweep.
#
# This fixture snapshots the live sqlite3 / aiosqlite Connection count
# before each test + asserts no net growth after. Defaults OFF because
# (a) a few legitimately-session-scoped DB fixtures hold connections
# across tests, (b) third-party libs may also leak; user opts in per
# branch / per release-gate run when the leak surface needs sweeping.
#
# Usage:  `RECON_GEN_SQLITE_LEAK_GATE=1 pytest tests/...`


def _count_live_sqlite_connections() -> int:
    """Sweep ``gc.get_objects()`` for live sqlite3 / aiosqlite Connections.

    Forces a ``gc.collect()`` first so legitimately-out-of-scope
    connections are reaped before the count. aiosqlite import is
    soft — environments without it count only stdlib sqlite3 conns.
    """
    import gc as _gc  # noqa: PLC0415
    import sqlite3 as _sqlite3  # noqa: PLC0415

    aiosqlite_conn_cls: tuple[type, ...]
    try:
        import aiosqlite as _aiosqlite  # noqa: PLC0415

        aiosqlite_conn_cls = (_aiosqlite.Connection,)
    except ImportError:
        aiosqlite_conn_cls = ()

    # Count only OPEN sqlite3 / aiosqlite connections — a closed
    # Connection object can linger in pytest's traceback / fixture-result
    # caches even after the test's own `conn.close()` ran, which would
    # false-positive the gate. We probe each candidate by calling
    # `execute("SELECT 1")` and only count it if it doesn't raise
    # `ProgrammingError("Cannot operate on a closed database.")`.
    for _ in range(3):
        _gc.collect()
    live = 0
    for o in _gc.get_objects():
        if isinstance(o, _sqlite3.Connection):
            try:
                o.execute("SELECT 1")
                live += 1
            except _sqlite3.ProgrammingError:
                pass
        elif aiosqlite_conn_cls and isinstance(o, aiosqlite_conn_cls):
            # aiosqlite.Connection wraps a background thread; the thread's
            # presence is the leak signal. `aiosqlite.Connection._running`
            # is True while the worker thread is alive.
            if getattr(o, "_running", False):
                live += 1
    return live


_SQLITE_LEAK_BASELINE: dict[str, int] = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: Any) -> Generator[None, None, None]:  # typing-smell: ignore[explicit-any]: pytest Item from late import
    """Stash the pre-setup sqlite-conn count when the leak gate is enabled.

    Pair with ``pytest_runtest_teardown`` (below) which compares after
    ALL fixture finalizers have run — fixes the autouse-fixture timing
    bug where the gate fires before per-test fixtures close their conns.
    """
    if os.environ.get("RECON_GEN_SQLITE_LEAK_GATE") == "1":
        _SQLITE_LEAK_BASELINE[item.nodeid] = _count_live_sqlite_connections()
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: Any) -> Generator[None, None, None]:  # typing-smell: ignore[explicit-any]: pytest Item from late import
    """Fail if the test left more sqlite conns than it found (gate opt-in).

    Surfaced 2026-05-27 — aiosqlite#258 leaks thread locks on per-request
    connect+close, and `with sqlite3.connect(...)` (Python's sqlite3
    context manager handles transactions, NOT close) is a common
    foot-gun. Both accumulate live Connection objects until OOM.

    Opt in via ``RECON_GEN_SQLITE_LEAK_GATE=1`` — default OFF because
    legitimate session-scoped DB fixtures hold connections across tests
    and would false-positive without explicit baseline-shift tracking.
    """
    yield  # let all other teardown hooks + finalizers run first
    if os.environ.get("RECON_GEN_SQLITE_LEAK_GATE") != "1":
        return
    before = _SQLITE_LEAK_BASELINE.pop(item.nodeid, None)
    if before is None:
        return
    after = _count_live_sqlite_connections()
    leaked = after - before
    if leaked > 0:
        raise AssertionError(
            f"sqlite-leak-gate: test {item.nodeid!r} leaked {leaked} "
            f"Connection instance(s) (before={before} → after={after}). "
            f"Likely culprits: `with sqlite3.connect(...) as c:` "
            f"(commits transaction, DOES NOT close) or "
            f"`async with aiosqlite.connect(...)` (aiosqlite#258 leaks "
            f"thread locks). Use the `aiosqlitepool`-backed pool from "
            f"common/db.py or close connections explicitly in a try/finally."
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Generator[None, Any, None]:
    """Y.2.gate.c.2 — write per-test timing JSONL when the runner is driving.

    Hook signature is the standard pytest wrapper form. The makereport hook
    fires three times per test (setup / call / teardown phases); we only
    record the ``call`` phase since that's the actual test execution time
    drift detection cares about.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    # Sidecar contract — swallow registry validator failures (a test
    # that monkeypatches RECON_GEN_RUN_DIR to an invalid path for its
    # own purposes must not cause the timings hook to crash the
    # worker).
    try:
        run_dir_path = RECON_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        return
    layer = RECON_GEN_LAYER.get_or_none()
    if not run_dir_path or not layer:
        return
    run_dir = str(run_dir_path)

    record = {
        "layer": layer,
        "test_id": report.nodeid,
        "duration_seconds": float(report.duration),
        "outcome": str(report.outcome),
    }
    # Per-worker file when xdist (c.6) is active — avoids append contention.
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
    suffix = f"-{worker_id}" if worker_id else ""
    # Sidecar contract (Y.2.gate.c.12 alignment): capture failures must
    # never break a passing test. A test that monkeypatches
    # RECON_GEN_RUN_DIR for its own purposes (e.g., the loader sidecar
    # tests) might point us at an unwritable path; swallow OSError
    # rather than crashing the worker.
    try:
        timings_dir = Path(run_dir) / "timings"
        timings_dir.mkdir(parents=True, exist_ok=True)
        target = timings_dir / f"{layer}{suffix}.jsonl"
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass
