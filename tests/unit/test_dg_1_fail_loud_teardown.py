"""DG.1 — pin the fail-loud-teardown contract.

When an ``isolated_cfg`` teardown fails:

1. The failure appends to ``_TEARDOWN_FAILURES`` (collected per-worker
   process) — NOT swallowed silently with a print.
2. The root conftest's ``pytest_sessionfinish`` reads the list +
   prints a clearly-marked summary section + raises
   ``session.exitstatus`` to non-zero so the CI run fails.

Per operator lock 2026-06-13 (DG.0 audit): "still needs to be a
failure so it doesn't get ignored and blow up the next run."
"""

from __future__ import annotations

from typing import Any

from tests.e2e import _isolation


class _FakeSession:
    """Stand-in for ``pytest.Session`` with just the ``exitstatus``
    attribute the hook reads + writes. The real ``Session`` would also
    need a ``config`` etc., but ``pytest_sessionfinish`` only touches
    ``session.exitstatus``."""
    exitstatus: int

    def __init__(self, exitstatus: int) -> None:
        self.exitstatus = exitstatus


def _reset_failures() -> None:
    _isolation._TEARDOWN_FAILURES.clear()  # pyright: ignore[reportPrivateUsage]: testing internal contract


def test_teardown_failures_starts_empty() -> None:
    """Module-level collector starts empty for a fresh import."""
    _reset_failures()
    assert _isolation.teardown_failures() == []


def test_appending_failure_surfaces_via_accessor() -> None:
    """``teardown_failures()`` returns a snapshot of the collector.
    The ``isolated_cfg`` teardown path appends ``_TeardownFailure``
    entries on schema-drop exceptions; the conftest reads them via
    this accessor."""
    _reset_failures()
    f = _isolation._TeardownFailure(  # pyright: ignore[reportPrivateUsage]: dataclass is part of the public-by-convention DG.1 contract
        suffix="abc123",
        dialect="postgres",
        db_table_prefix="qsgen_postgres_abc123",
        exc_repr="psycopg.errors.DiskFull('no space')",
        traceback="(simulated)",
    )
    _isolation._TEARDOWN_FAILURES.append(f)  # pyright: ignore[reportPrivateUsage]: testing internal contract
    out = _isolation.teardown_failures()
    assert len(out) == 1
    assert out[0].suffix == "abc123"
    assert out[0].dialect == "postgres"
    _reset_failures()


def test_accessor_returns_copy_not_reference() -> None:
    """``teardown_failures()`` returns a defensive copy so caller-side
    mutation doesn't drain the live collector mid-session."""
    _reset_failures()
    _isolation._TEARDOWN_FAILURES.append(  # pyright: ignore[reportPrivateUsage]: testing internal contract
        _isolation._TeardownFailure(  # pyright: ignore[reportPrivateUsage]: dataclass is part of the public-by-convention DG.1 contract
            suffix="x",
            dialect="postgres",
            db_table_prefix="p_x",
            exc_repr="(e)",
            traceback="(tb)",
        )
    )
    snap = _isolation.teardown_failures()
    snap.clear()
    # Live collector unaffected.
    assert len(_isolation.teardown_failures()) == 1
    _reset_failures()


def test_sessionfinish_no_failures_leaves_exit_code_alone(
    capsys: Any,  # pyright: ignore[reportExplicitAny]: pytest CaptureFixture stub partially-unknown across versions
) -> None:
    """When no teardown failures accumulated, ``pytest_sessionfinish``
    must leave the exit code untouched. (Otherwise running any test
    in any file flips green runs to red.)"""
    _reset_failures()
    from tests.conftest import pytest_sessionfinish

    session = _FakeSession(exitstatus=0)
    pytest_sessionfinish(session, exitstatus=0)
    assert session.exitstatus == 0, "Empty failure list must not flip exit code"
    out = capsys.readouterr().out
    assert "DG.1" not in out, "No DG.1 banner when no failures"


def test_sessionfinish_with_failure_flips_exit_to_non_zero_and_prints_summary(
    capsys: Any,  # pyright: ignore[reportExplicitAny]: pytest CaptureFixture stub partially-unknown across versions
) -> None:
    """One simulated teardown failure flips the run's exit code to
    non-zero AND prints a clearly-marked summary block (so operators
    grep it out of red CI logs).

    The real ``isolated_cfg`` teardown path needs a live PG/Oracle to
    fail meaningfully (db-tier), but the collector → conftest →
    exitstatus wire is unit-testable in isolation."""
    _reset_failures()
    _isolation._TEARDOWN_FAILURES.append(  # pyright: ignore[reportPrivateUsage]: testing internal contract
        _isolation._TeardownFailure(  # pyright: ignore[reportPrivateUsage]: testing internal contract
            suffix="badcafe",
            dialect="postgres",
            db_table_prefix="qsgen_postgres_badcafe",
            exc_repr="psycopg.errors.DiskFull('no space left on device')",
            traceback="(simulated traceback line 1)\n(line 2)",
        )
    )
    from tests.conftest import pytest_sessionfinish

    session = _FakeSession(exitstatus=0)
    pytest_sessionfinish(session, exitstatus=0)
    assert session.exitstatus != 0, (
        f"Non-empty failure list must flip exit code; got {session.exitstatus}"
    )
    out = capsys.readouterr().out
    assert "DG.1 — isolated_cfg teardown failures" in out
    assert "badcafe" in out
    assert "psycopg.errors.DiskFull" in out
    assert "simulated traceback" in out
    _reset_failures()


def test_sessionfinish_does_not_override_existing_non_zero_exit(
    capsys: Any,  # pyright: ignore[reportExplicitAny]: pytest CaptureFixture stub partially-unknown across versions
) -> None:
    """If pytest already failed (exitstatus != 0), the DG.1 hook
    leaves its code alone. We still print the failure summary — the
    operator needs to see it — but we don't risk hiding a more
    informative test-failure exit code by overwriting it."""
    _reset_failures()
    _isolation._TEARDOWN_FAILURES.append(  # pyright: ignore[reportPrivateUsage]: testing internal contract
        _isolation._TeardownFailure(  # pyright: ignore[reportPrivateUsage]: testing internal contract
            suffix="abc",
            dialect="postgres",
            db_table_prefix="p_abc",
            exc_repr="(e)",
            traceback="(tb)",
        )
    )
    from tests.conftest import pytest_sessionfinish

    session = _FakeSession(exitstatus=2)
    pytest_sessionfinish(session, exitstatus=2)
    assert session.exitstatus == 2, (
        "Pre-existing pytest failure code must survive DG.1 hook"
    )
    out = capsys.readouterr().out
    assert "DG.1 — isolated_cfg teardown failures" in out
    _reset_failures()
