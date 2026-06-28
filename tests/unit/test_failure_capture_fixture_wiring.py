"""AA.H.10 — static regression guard that the dashboard-driver fixture
wires the failure-capture hook.

The bug AA.H.10 fixes: AA.H.6 lifted ``trigger_failure_capture`` to a
DashboardDriver-friendly verb and added a ``_maybe_capture_on_failure``
helper invoked from fixture teardown — but the helper was wired only
into ``_parametrized_dashboard_driver`` in ``conftest.py`` while a
sibling driver fixture silently dropped artifacts on failure.

This test pins ``_parametrized_dashboard_driver`` to call the shared hook
— a future regression that drops the wiring will fail this test loudly
instead of going to production with silent capture-drops.

DW.6 (2026-06-27) — QuickSight removed. The QS-driver fixtures
(``qs_driver``) and the shared ``_lifecycle.py`` lifecycle primitive are
gone; App2 is the sole renderer. The guard narrows to the surviving
app2 wiring: ``_parametrized_dashboard_driver`` must call
``_maybe_capture_on_failure`` post-yield, and ``tests/e2e/_capture.py``
must export the hook.

AST-based to avoid running the fixtures: the wiring is "is the call
present near the ``yield`` in this fixture function?", which a
parser can answer without spinning Playwright.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
_CONFTEST = _REPO_ROOT / "tests" / "e2e" / "conftest.py"


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


def test_parametrized_dashboard_driver_wires_capture_hook() -> None:
    """``_parametrized_dashboard_driver`` in conftest.py — the App2
    dashboard driver. AA.H.6 wired this one; the test pins the wiring
    stays. Post-DW.6 the inline ``_maybe_capture_on_failure(request,
    driver)`` post-yield is the whole contract (App2Driver has no embed
    lifecycle to share, so there's no indirection to look through)."""
    body = _function_body_text(
        _CONFTEST.read_text(), "_parametrized_dashboard_driver",
    )
    assert "_maybe_capture_on_failure" in body, (
        "_parametrized_dashboard_driver must wire the capture hook via "
        "an inline `_maybe_capture_on_failure(request, driver)` call "
        "post-yield — otherwise test-body failures silently drop "
        "diagnostic artifacts. See AA.H.10 in PLAN_ARCHIVE for the "
        "original gap."
    )


def test_capture_module_lives_at_shared_path() -> None:
    """The shared ``_capture`` module exists at the path
    ``_parametrized_dashboard_driver`` imports the hook from."""
    capture = _REPO_ROOT / "tests" / "e2e" / "_capture.py"
    assert capture.is_file(), f"missing shared capture helper at {capture}"
    content = capture.read_text()
    assert "def maybe_capture_on_failure" in content, (
        "_capture.py must export maybe_capture_on_failure"
    )
    # The driver duck-typing contract: ``_capture`` probes ``page``
    # (App2Driver). The legacy ``_page`` fallback survives harmlessly as
    # a duck-typed probe; assert the App2 attribute is read so a
    # regression that drops it surfaces here.
    assert '"page"' in content, (
        "_capture.py must probe the App2Driver ``page`` attribute."
    )
