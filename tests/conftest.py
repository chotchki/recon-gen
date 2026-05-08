"""Top-level conftest — Y.2.gate.c.2 timings capture hook.

When invoked under the test layer chain runner, ``QS_GEN_RUN_DIR`` and
``QS_GEN_LAYER`` are set in the env (see ``runner.py::_layer_command``);
``pytest_runtest_makereport`` writes one JSONL line per test ``call`` phase
into ``$QS_GEN_RUN_DIR/timings/<layer>.jsonl``.

When invoked directly (``pytest tests/...`` without the runner), both env
vars are unset and the hook is a no-op — direct invocation behavior is
unchanged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Generator

import pytest

from quicksight_gen.common.env_keys import (
    EnvVarInvalid,
    QS_GEN_LAYER,
    QS_GEN_RUN_DIR,
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
    # that monkeypatches QS_GEN_RUN_DIR to an invalid path for its
    # own purposes must not cause the timings hook to crash the
    # worker).
    try:
        run_dir_path = QS_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        return
    layer = QS_GEN_LAYER.get_or_none()
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
    # QS_GEN_RUN_DIR for its own purposes (e.g., the loader sidecar
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
