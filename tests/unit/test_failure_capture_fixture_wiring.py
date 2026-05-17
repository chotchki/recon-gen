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


# AA.H.12 — the capture hook lives in two valid shapes:
#   (a) inlined: ``_maybe_capture_on_failure(request, driver)`` /
#       ``maybe_capture_on_failure(request, driver)`` directly in the
#       fixture body (the App2 branch of `_parametrized_dashboard_driver`
#       still uses this — App2Driver has no embed lifecycle to share).
#   (b) via lifecycle: ``qs_driver_or_none(...)`` (the AA.H.12 shared
#       context manager that bundles get_user_arn gate + embed + capture
#       hook). The hook fires inside the helper's ``try/finally``.
# Either shape satisfies the wiring contract — accept both.
_CAPTURE_HOOK_SHAPES = (
    "_maybe_capture_on_failure",
    "maybe_capture_on_failure",
    "qs_driver_or_none",
)


def _has_capture_wiring(body: str) -> bool:
    return any(s in body for s in _CAPTURE_HOOK_SHAPES)


def test_qs_driver_fixture_wires_capture_hook() -> None:
    """``qs_driver`` in conftest.py — the basic per-test QS embed
    driver. Pre-AA.H.10 didn't wire the hook; failures from any
    browser e2e using this fixture dropped no artifacts. Post-AA.H.12
    the wiring is via the shared ``qs_driver_or_none`` lifecycle."""
    body = _function_body_text(_CONFTEST.read_text(), "qs_driver")
    assert _has_capture_wiring(body), (
        "qs_driver fixture missing capture wiring — test-body failures "
        "will silently drop diagnostic artifacts. Wire either via the "
        "shared `qs_driver_or_none` lifecycle OR directly call "
        "`_maybe_capture_on_failure(request, d)` post-yield. "
        "See AA.H.10 / AA.H.12 in PLAN_ARCHIVE for the original gaps."
    )


def test_parametrized_dashboard_driver_wires_capture_hook() -> None:
    """``_parametrized_dashboard_driver`` in conftest.py — the
    [qs, app2] parametrized driver. AA.H.6 wired this one; the test
    pins both branches stay wired. AA.H.12 routes the qs branch
    through ``qs_driver_or_none`` while the app2 branch keeps the
    inline hook (App2Driver doesn't share an embed lifecycle)."""
    body = _function_body_text(
        _CONFTEST.read_text(), "_parametrized_dashboard_driver",
    )
    # Both branches (qs + app2) must wire the hook somehow — a one-sided
    # regression would silently drop artifacts on the missing branch.
    # qs branch satisfies via `qs_driver_or_none`; app2 branch via the
    # inlined `_maybe_capture_on_failure`. Distinct strings, so count
    # both forms.
    has_qs_wiring = "qs_driver_or_none" in body
    has_app2_wiring = "_maybe_capture_on_failure" in body
    assert has_qs_wiring and has_app2_wiring, (
        f"_parametrized_dashboard_driver must wire the capture hook "
        f"in BOTH the qs and app2 branches. "
        f"qs wiring (qs_driver_or_none): {has_qs_wiring}; "
        f"app2 wiring (_maybe_capture_on_failure): {has_app2_wiring}."
    )


def test_per_dialect_qs_driver_fixture_wires_capture_hook() -> None:
    """``per_dialect_qs_driver`` in test_audit_dashboard_agreement.py
    — the audit-agreement test's per-dialect QS driver. Pre-AA.H.10
    didn't wire the hook; today's chain lost all 4 audit-agreement
    failures' diagnostic artifacts because of this gap. AA.H.12
    routes through the shared ``qs_driver_or_none`` lifecycle."""
    body = _function_body_text(
        _AUDIT_TEST.read_text(), "per_dialect_qs_driver",
    )
    assert _has_capture_wiring(body), (
        "per_dialect_qs_driver fixture missing capture wiring — "
        "audit-agreement test failures will silently drop diagnostic "
        "artifacts. Wire either via the shared `qs_driver_or_none` "
        "lifecycle OR directly call `maybe_capture_on_failure(request, "
        "driver)` post-yield."
    )


def test_lifecycle_primitive_lives_at_shared_path() -> None:
    """The shared ``qs_driver_or_none`` context manager exists at the
    path the three fixtures import from. Catches an accidental
    rename / move that would break all three at import time."""
    lifecycle = _REPO_ROOT / "tests" / "e2e" / "_drivers" / "_lifecycle.py"
    assert lifecycle.is_file(), (
        f"missing shared lifecycle helper at {lifecycle} — AA.H.12 "
        f"extracted the QS embed lifecycle here; deleting it without "
        f"inlining the lifecycle back into all 3 fixtures will silently "
        f"break the capture-hook wiring across the e2e suite."
    )
    content = lifecycle.read_text()
    assert "def qs_driver_or_none" in content, (
        "_lifecycle.py must export qs_driver_or_none"
    )
    # The lifecycle MUST call the capture hook on exit. Without this,
    # the fixtures lose their AA.H.10 wiring.
    assert "maybe_capture_on_failure" in content, (
        "_lifecycle.py must call maybe_capture_on_failure post-yield "
        "— otherwise the AA.H.10 capture pipeline is silently broken "
        "for every fixture using the lifecycle primitive."
    )


def test_capture_module_lives_at_shared_path() -> None:
    """The shared ``_capture`` module exists at the path the lifecycle
    primitive + the App2 branch of `_parametrized_dashboard_driver`
    import from."""
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
