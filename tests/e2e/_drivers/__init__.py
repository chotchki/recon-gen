"""X.2.q — dialect-aware e2e drivers.

``DashboardDriver`` is the protocol (the e2e test vocabulary, results as
plain Python); ``App2Driver`` drives the self-hosted HTMX renderer;
``QsEmbedDriver`` drives the embedded QuickSight iframe. See ``base.py``
for the design.

``skips_if_unsupported`` is the bridge for parametrized ``[qs, app2]``
tests that call a verb one renderer doesn't implement: a verb raising
``NotImplementedError`` (the protocol's "this verb isn't meaningful for
this renderer" signal — see CLAUDE.md) becomes a ``pytest.skip`` for
that param, not a failure.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator

import pytest

from tests.e2e._drivers.app2 import App2Driver
from tests.e2e._drivers.base import DashboardDriver
from tests.e2e._drivers.qs import QsEmbedDriver

__all__ = [
    "App2Driver",
    "DashboardDriver",
    "QsEmbedDriver",
    "skips_if_unsupported",
]


@contextlib.contextmanager
def skips_if_unsupported() -> Generator[None, None, None]:
    """Run the body; convert a driver verb's ``NotImplementedError`` into
    a ``pytest.skip`` carrying that verb's message.

    Use in a parametrized ``[qs, app2]`` test around a verb that only one
    renderer implements (``set_slider`` on QS, ``drill_from_first_row`` /
    ``cross_link`` on App2, …): the test runs whichever legs support it
    and skips — not fails — the legs that don't::

        with skips_if_unsupported():
            driver.cross_link("Money Trail")
    """
    try:
        yield
    except NotImplementedError as exc:  # noqa: BLE001 — the skip IS the handling
        pytest.skip(str(exc))
