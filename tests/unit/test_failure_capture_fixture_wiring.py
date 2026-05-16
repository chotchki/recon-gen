"""AA.H.10 — static regression guard that every QS-driver fixture
wires the failure-capture hook.

The bug AA.H.10 fixes: AA.H.6 lifted ``trigger_failure_capture`` to a
DashboardDriver-friendly verb and added a ``_maybe_capture_on_failure``
helper invoked from fixture teardown — but the helper was wired only
into ``_parametrized_dashboard_driver`` in ``conftest.py``. Two other
QS-driver fixtures (``qs_driver`` in the same conftest, and
``per_dialect_qs_driver`` in ``test_audit_dashboard_agreement.py``)
silently dropped artifacts on failure. Today's chain lost all 4 of
the audit-agreement failures' DOM / screenshot / trace.zip because
of exactly that gap.

This test pins all three fixtures to call the shared hook — a future
new QS-driver fixture without the wiring will fail this test loudly
instead of going to production with silent capture-drops.

AST-based to avoid running the fixtures: the wiring is "is the call
present near the ``yield`` in this fixture function?", which a
parser can answer without spinning Playwright.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_CONFTEST = _REPO_ROOT / "tests" / "e2e" / "conftest.py"
_AUDIT_TEST = _REPO_ROOT / "tests" / "e2e" / "test_audit_dashboard_agreement.py"


def _function_body_text(source: str, fn_name: str) -> str:
    """Return the source text of the named top-level function/generator,
    body only (line range from ``def`` through the last contained
    line). Helper rather than re-parsing — the call-site assertions
    below are content-substring checks, not AST walks."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name == fn_name:
                lines = source.splitlines()
                start = node.lineno - 1
                end = (node.end_lineno or start + 1)
                return "\n".join(lines[start:end])
    raise AssertionError(
        f"function {fn_name!r} not found at module scope in source",
    )


def test_qs_driver_fixture_wires_capture_hook() -> None:
    """``qs_driver`` in conftest.py — the basic per-test QS embed
    driver. Pre-AA.H.10 didn't wire the hook; failures from any
    browser e2e using this fixture dropped no artifacts."""
    body = _function_body_text(_CONFTEST.read_text(), "qs_driver")
    assert "_maybe_capture_on_failure" in body, (
        "qs_driver fixture missing _maybe_capture_on_failure call — "
        "test-body failures will silently drop diagnostic artifacts. "
        "Add `_maybe_capture_on_failure(request, d)` after the yield. "
        "See AA.H.10 in PLAN_ARCHIVE for the original gap."
    )


def test_parametrized_dashboard_driver_wires_capture_hook() -> None:
    """``_parametrized_dashboard_driver`` in conftest.py — the
    [qs, app2] parametrized driver. AA.H.6 wired this one; the test
    pins it doesn't regress."""
    body = _function_body_text(
        _CONFTEST.read_text(), "_parametrized_dashboard_driver",
    )
    assert "_maybe_capture_on_failure" in body, (
        "_parametrized_dashboard_driver fixture missing "
        "_maybe_capture_on_failure call — AA.H.6's regression "
        "guard fell out."
    )
    # Both branches (qs + app2) must wire the hook — a one-sided
    # regression would silently drop artifacts on the missing branch.
    assert body.count("_maybe_capture_on_failure") >= 2, (
        "_parametrized_dashboard_driver must wire the capture hook "
        "in BOTH the qs and app2 branches — found only 1 call."
    )


def test_per_dialect_qs_driver_fixture_wires_capture_hook() -> None:
    """``per_dialect_qs_driver`` in test_audit_dashboard_agreement.py
    — the audit-agreement test's per-dialect QS driver. Pre-AA.H.10
    didn't wire the hook; today's chain lost all 4 audit-agreement
    failures' diagnostic artifacts because of this gap. Pin so a
    future refactor of the audit-agreement fixture doesn't drop the
    wiring."""
    body = _function_body_text(
        _AUDIT_TEST.read_text(), "per_dialect_qs_driver",
    )
    assert "maybe_capture_on_failure" in body, (
        "per_dialect_qs_driver fixture missing "
        "maybe_capture_on_failure call — audit-agreement test "
        "failures will silently drop diagnostic artifacts. Add "
        "`maybe_capture_on_failure(request, driver)` after the yield."
    )


def test_capture_module_lives_at_shared_path() -> None:
    """The shared ``_capture`` module exists at the path the three
    fixtures import from. Catches an accidental rename / move that
    would break all three at import time."""
    capture = _REPO_ROOT / "tests" / "e2e" / "_capture.py"
    assert capture.is_file(), f"missing shared capture helper at {capture}"
    content = capture.read_text()
    assert "def maybe_capture_on_failure" in content, (
        "_capture.py must export maybe_capture_on_failure"
    )
    # The driver duck-typing contract: both _page (QsEmbedDriver) and
    # page (App2Driver) must be probed. A regression that drops one
    # would silently break capture for that driver type.
    assert '"_page"' in content and '"page"' in content, (
        "_capture.py must probe both ``_page`` (QS) and ``page`` "
        "(App2) attributes to support both driver types."
    )
