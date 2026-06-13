"""DG.3 — pin the dropdown-poll retry contract.

``wait_for_dropdown_options_present`` polls ``read_dropdown_options``
until ≥1 option appears OR the outer timeout expires. Pre-DG.3 a
single ``TimeoutError`` from the inner call (2s budget per attempt)
propagated out of the helper unchecked, making the outer timeout
illusory. The fix catches ``TimeoutError`` per-attempt and continues
the poll loop so the outer budget actually does what it says.

Surfaced by v13.15.1-gate CI failure
``test_cq_4_e_l1_picker_finds_known_value[qs-Transactions-Transfer]``:
the 8k+ row DS_L1_TX_IDS picker cold-loads slower than 2s on a fresh
deploy, and the unhandled inner timeout aborted the test instead of
retrying.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from recon_gen.common.browser.helpers import wait_for_dropdown_options_present


def test_returns_first_non_empty_result_immediately() -> None:
    """Happy path — inner read returns options on first call."""
    with patch(
        "recon_gen.common.browser.helpers.read_dropdown_options",
        return_value=["A", "B"],
    ) as m:
        page = MagicMock()
        out = wait_for_dropdown_options_present(page, "label", timeout_ms=15_000)
        assert out == ["A", "B"]
        assert m.call_count == 1


def test_retries_through_inner_timeout_until_success() -> None:
    """DG.3 contract — inner ``TimeoutError`` is caught + retried, not
    propagated. Eventual success returns the populated list."""
    from playwright.sync_api import TimeoutError as PWTimeout

    call_count = {"n": 0}

    def side_effect(*_args: Any, **_kwargs: Any) -> list[str]:
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise PWTimeout("simulated cold-load slow path")
        return ["found"]

    with patch(
        "recon_gen.common.browser.helpers.read_dropdown_options",
        side_effect=side_effect,
    ):
        page = MagicMock()
        out = wait_for_dropdown_options_present(page, "label", timeout_ms=15_000)
    assert out == ["found"]
    assert call_count["n"] == 3, (
        f"Expected 3 inner calls (2 timeouts + 1 success); got {call_count['n']}"
    )


def test_returns_empty_after_outer_deadline_on_all_timeouts() -> None:
    """If every poll attempt times out, return ``[]`` after the outer
    deadline — don't propagate the last ``TimeoutError``."""
    from playwright.sync_api import TimeoutError as PWTimeout

    with patch(
        "recon_gen.common.browser.helpers.read_dropdown_options",
        side_effect=PWTimeout("always slow"),
    ):
        page = MagicMock()
        # Tight outer budget — should give up cleanly after a couple polls.
        out = wait_for_dropdown_options_present(page, "label", timeout_ms=500)
    assert out == [], "Empty list, not a raised TimeoutError"


def test_does_not_swallow_non_timeout_exceptions() -> None:
    """Catching ``TimeoutError`` is targeted — other exceptions
    (e.g. a real bug in the dropdown reader) must still propagate."""

    class MyOtherError(RuntimeError):
        pass

    with patch(
        "recon_gen.common.browser.helpers.read_dropdown_options",
        side_effect=MyOtherError("real bug"),
    ):
        page = MagicMock()
        with pytest.raises(MyOtherError):
            wait_for_dropdown_options_present(page, "label", timeout_ms=5_000)
