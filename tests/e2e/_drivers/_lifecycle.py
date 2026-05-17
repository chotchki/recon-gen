"""Shared lifecycle primitive for QuickSight-embed-driver fixtures.

AA.H.12 — three fixtures pre-AA.H.12 open-coded the same
``QsEmbedDriver`` lifecycle ({get_user_arn gate + ``embed(...)`` +
``maybe_capture_on_failure``}); AA.H.10 just bit us once on exactly
this duplication (capture hook missing from 2 of 3). This module is
the single source of truth — the 3 fixtures collapse to thin
policy-shaped wrappers around this one helper.

Why a separate module (not inlined into ``_capture.py``):
``_capture.py``'s scope is "bridge the pytest yield-fixture gap";
this helper's scope is "compose the QS-driver lifecycle". Different
verbs, different test stays clean.

Why not a pytest fixture: fixture-level abstraction can't easily
express the policy knobs (skip vs yield-None on QS unavailable; tuple
vs scalar yield shape; tall vs default viewport) without an
explosion of fixture variants. A context manager invoked from inside
each fixture gives the right factoring — shared lifecycle, per-call
policy.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

from tests.e2e._capture import maybe_capture_on_failure

if TYPE_CHECKING:
    from tests.e2e._drivers.qs import QsEmbedDriver


@contextlib.contextmanager
def qs_driver_or_none(
    request,  # type: ignore[no-untyped-def]: pytest FixtureRequest — Untyped Any cascade if annotated
    *,
    account_id: str,
    region: str,
    viewport: tuple[int, int] = (1600, 1000),
) -> Iterator["QsEmbedDriver | None"]:
    """Context manager: yield a ``QsEmbedDriver`` if QS is available,
    else yield ``None``. On exit (success or failure) fires the
    failure-capture hook iff a driver was yielded.

    "QS available" = ``get_user_arn()`` resolves a QuickSight user ARN
    (either from cfg.auth.aws_profile derivation or the explicit
    ``QS_E2E_USER_ARN`` env override). When unavailable, yields
    ``None`` — the caller decides whether to ``pytest.skip`` or run
    with reduced renderer coverage. (``per_dialect_qs_driver`` in
    ``test_audit_dashboard_agreement.py`` takes the latter path so
    the SQLite cell + missing-ARN cases still exercise direct-SQL +
    App2 + PDF as a clean 3-way.)

    Viewport defaults to ``(1600, 1000)``. Pass ``(1600, 4000)`` for
    fixtures whose tests touch stacked KPI+chart+table layouts where
    the detail table sits below the fold (audit-agreement's stacked
    Pending Aging / Unbundled Aging / Supersession Audit sheets need
    this — QS lazy-renders below the fold and ``table_row_count``'s
    page-size-bump path needs the ``.grid-container`` close enough to
    the viewport to scroll into).

    The ``try/finally`` wrap around ``yield driver`` ensures
    ``maybe_capture_on_failure`` ALWAYS fires post-yield (no-op on
    pass, dumps the 6 artifacts on test-body failure). This is the
    AA.H.10 contract — pre-AA.H.10, callers open-coded the hook in
    teardown and 2 of 3 fixtures forgot it.
    """
    from quicksight_gen.common.browser.helpers import get_user_arn
    from tests.e2e._drivers.qs import QsEmbedDriver

    try:
        get_user_arn()
    except RuntimeError:
        # QS leg unavailable. Caller decides: pytest.skip (single-
        # renderer tests) or yield-through (multi-renderer tests that
        # can still run other legs).
        yield None
        return
    with QsEmbedDriver.embed(
        aws_account_id=account_id, aws_region=region, viewport=viewport,
    ) as driver:
        try:
            yield driver
        finally:
            maybe_capture_on_failure(request, driver)
