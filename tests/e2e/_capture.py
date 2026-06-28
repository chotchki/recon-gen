"""Shared failure-capture hook for browser e2e driver fixtures.

AA.H.6 bridged the pytest yield-fixture semantics gap by capturing 6
diagnostic artifacts (screenshot, DOM, console, network, db_counts,
trace.zip) from a fixture's teardown after a failed test. Originally
lived in ``conftest.py``; AA.H.10 lifts the helper here so any driver
fixture can import it from a single import path.

The bug it fixes: pytest doesn't re-throw the test-body exception back
into the generator-fixture's ``yield`` — the ``with`` block exits
cleanly, ``webkit_page``'s ``except BaseException:`` never fires, and
the 6 artifacts never land.
"""

from __future__ import annotations

from typing import Any

import pytest


def maybe_capture_on_failure(request: pytest.FixtureRequest, driver: Any) -> None:  # noqa: ARG001
    _maybe_capture_impl(request, driver)


def _maybe_capture_impl(request: Any, driver: Any) -> None:
    """Bridge the pytest yield-fixture gap.

    Invoked from a fixture's teardown (after ``yield``), this consults
    ``request.node.rep_call`` (set by the ``pytest_runtest_makereport``
    hook in ``conftest.py``) and triggers ``trigger_failure_capture``
    when the test body actually failed. No-op on pass / skip /
    fixture-setup-failure.

    ``App2Driver`` exposes ``.page``. If it doesn't resolve to a
    Playwright Page, the capture is silently skipped (a non-browser
    driver has nothing to dump).
    """
    rep = getattr(request.node, "rep_call", None)
    if rep is None or not rep.failed:
        return
    page = getattr(driver, "page", None)
    if page is None:
        return
    # typing-smell: ignore[no-playwright-leak]: this is the dedicated
    # bridge from pytest's makereport hook to the capture pipeline; it
    # ISN'T an e2e test reaching into Playwright, it's a shared helper
    # gluing the fixture-yield-semantics gap. trigger_failure_capture
    # IS the DashboardDriver-friendly verb — it takes the Page from
    # ``driver.page`` and writes 6 artifacts. There's nowhere else to
    # invoke it from.
    from recon_gen.common.browser.helpers import (  # typing-smell: ignore[no-playwright-leak]: shared capture-bridge module
        _sanitize_test_id,
        trigger_failure_capture,
    )

    test_id = _sanitize_test_id(
        request.node.nodeid.replace("/", "_").replace("::", "__").replace(".py", "")
    )
    # Resolve cfg from the fixture so trigger_failure_capture can also
    # dump db_counts.txt (per-table row counts) — the first answer
    # every "visual rendered blank" triage needs. Soft-fall: missing
    # cfg fixture (e.g. non-conftest test) just skips the DB dump;
    # other artifacts still land. Sidecar contract applies — capture
    # failure must never mask the original test failure.
    cfg: object | None
    try:
        cfg = request.getfixturevalue("cfg")
    except Exception:
        cfg = None
    trigger_failure_capture(page, test_id=test_id, cfg=cfg)
