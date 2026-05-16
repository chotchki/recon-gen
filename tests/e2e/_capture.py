"""Shared failure-capture hook for browser e2e driver fixtures.

AA.H.6 bridged the pytest yield-fixture semantics gap by capturing 6
diagnostic artifacts (screenshot, DOM, console, network, qs-error
overlay, trace.zip) from a fixture's teardown after a failed test.
Originally lived in ``conftest.py`` and was wired only into
``_parametrized_dashboard_driver`` — the other two QS-driver fixtures
(``qs_driver`` in conftest, ``per_dialect_qs_driver`` in
``test_audit_dashboard_agreement.py``) silently dropped artifacts on
failure. AA.H.10 lifts the helper here so all three (and any future
fixture) can import it from a single import path.

The bug it fixes: pytest doesn't re-throw the test-body exception back
into the generator-fixture's ``yield`` — the ``with`` block exits
cleanly, ``webkit_page``'s ``except BaseException:`` never fires, and
the 6 artifacts never land. Today's audit-agreement chain (4 failures
in ``test_invariant_four_way_agreement``) dropped zero artifacts even
though AA.H.6's regression-test pinned the capture path — because the
fixture this test uses (``per_dialect_qs_driver``) wasn't wired.
"""

from __future__ import annotations


def maybe_capture_on_failure(request, driver) -> None:  # type: ignore[no-untyped-def]: pytest types + driver duck-typing
    """Bridge the pytest yield-fixture gap.

    Invoked from a fixture's teardown (after ``yield``), this consults
    ``request.node.rep_call`` (set by the ``pytest_runtest_makereport``
    hook in ``conftest.py``) and triggers ``trigger_failure_capture``
    when the test body actually failed. No-op on pass / skip /
    fixture-setup-failure.

    Driver duck-typing: ``QsEmbedDriver`` exposes ``._page``,
    ``App2Driver`` exposes ``.page``. Try both; if neither resolves to
    a Playwright Page, the capture is silently skipped (a non-browser
    driver has nothing to dump).
    """
    rep = getattr(request.node, "rep_call", None)
    if rep is None or not rep.failed:
        return
    page = getattr(driver, "_page", None) or getattr(driver, "page", None)
    if page is None:
        return
    # typing-smell: ignore[no-playwright-leak]: this is the dedicated
    # bridge from pytest's makereport hook to the capture pipeline; it
    # ISN'T an e2e test reaching into Playwright, it's a shared helper
    # gluing the fixture-yield-semantics gap. trigger_failure_capture
    # IS the DashboardDriver-friendly verb — it takes the Page from
    # ``driver._page`` / ``driver.page`` and writes 6 artifacts. There's
    # nowhere else to invoke it from.
    from quicksight_gen.common.browser.helpers import (  # typing-smell: ignore[no-playwright-leak]: shared capture-bridge module
        _sanitize_test_id,
        trigger_failure_capture,
    )

    test_id = _sanitize_test_id(
        request.node.nodeid.replace("/", "_").replace("::", "__").replace(".py", "")
    )
    trigger_failure_capture(page, test_id=test_id)
