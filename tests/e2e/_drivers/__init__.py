"""X.2.q — dialect-aware e2e drivers.

``DashboardDriver`` is the protocol (the e2e test vocabulary, results as
plain Python); ``App2Driver`` drives the self-hosted HTMX renderer — the
sole renderer post-DW (QuickSight removed). See ``base.py`` for the design.

``skips_if_unsupported`` is the renderer-agnostic bridge for tests that
call a verb a renderer doesn't implement: a verb raising
``NotImplementedError`` (the protocol's "this verb isn't meaningful for
this renderer" signal — see CLAUDE.md) becomes a ``pytest.skip``, not a
failure.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator

import pytest

from tests.e2e._drivers.app2 import App2Driver
from tests.e2e._drivers.base import DashboardDriver

__all__ = [
    "App2Driver",
    "DashboardDriver",
    "skips_if_unsupported",
]


@contextlib.contextmanager
def skips_if_unsupported() -> Generator[None, None, None]:
    """Run the body; convert a driver verb's ``NotImplementedError`` into
    a ``pytest.skip`` carrying that verb's message.

    Wrap a verb a renderer may not implement so the test skips — not
    fails — when that renderer signals "this verb isn't meaningful here"::

        with skips_if_unsupported():
            driver.cross_link("Money Trail")

    Renderer-agnostic: kept for any future multi-renderer parametrization
    even though App2 is the sole renderer post-DW.
    """
    try:
        yield
    except NotImplementedError as exc:  # noqa: BLE001 — the skip IS the handling
        pytest.skip(str(exc))
