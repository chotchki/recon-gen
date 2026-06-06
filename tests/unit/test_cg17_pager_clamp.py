"""CG.17 — `parse_toolbar_state` clamps an out-of-range
`page_offset` to the offset of the page that contains the last item,
not to the raw operator-provided value.

Cold-read v4 P0 #2: `/l2_shape/account/?page_offset=999` on a
16-row list returned `Showing 1000–16 of 16` — negative offsets
were already clamped to 0, high-end clamp was missing.

The fix lands an operator on the actual last page when their URL
is stale (e.g. they bookmarked `?page_offset=80` when the list had
100 entries; the list has since shrunk to 60). That's more useful
than dropping them back at page 1.
"""

from __future__ import annotations

import pytest

from recon_gen.common.html._components import (
    PAGE_SIZE_DEFAULT,
    parse_toolbar_state,
)


def test_offset_999_on_16_rows_clamps_to_first_page() -> None:
    """The cold-read v4 P0 scenario: 16 rows, page_size=25,
    operator supplies `?page_offset=999`. The last page CONTAINS
    item 16 and starts at offset 0 (the whole list fits on one
    page). Operator should land on that page."""
    state = parse_toolbar_state(
        {"page_offset": "999"},
        kind="account",
        total_count=16,
    )
    assert state.page_offset == 0
    assert state.page_start == 1
    assert state.page_end == 16


def test_offset_overflow_lands_on_last_page_for_multi_page_list() -> None:
    """60 rows + default page size + stale URL `?page_offset=999`
    → operator should land on the page containing item 60, not back
    at page 1. With PAGE_SIZE_DEFAULT (25 per the constant),
    last-page offset is 50."""
    assert PAGE_SIZE_DEFAULT == 25  # guard the test's arithmetic
    state = parse_toolbar_state(
        {"page_offset": "999"},
        kind="rail",
        total_count=60,
    )
    assert state.page_offset == 50
    assert state.page_start == 51
    assert state.page_end == 60


def test_offset_overflow_on_empty_list_clamps_to_zero() -> None:
    """Empty list — the only valid offset is 0."""
    state = parse_toolbar_state(
        {"page_offset": "999"},
        kind="rail",
        total_count=0,
    )
    assert state.page_offset == 0
    assert state.page_start == 0
    assert state.page_end == 0


def test_valid_offset_is_not_clamped() -> None:
    """The clamp only fires when the offset is out of range. A
    legitimate `?page_offset=25` on a 60-row list with page_size=25
    must land on page 2 unchanged."""
    state = parse_toolbar_state(
        {"page_offset": "25"},
        kind="rail",
        total_count=60,
    )
    assert state.page_offset == 25
    assert state.page_start == 26
    assert state.page_end == 50


def test_negative_offset_still_clamped_to_zero() -> None:
    """The pre-existing low-end clamp survives the high-end clamp
    addition — negative offsets still snap to 0."""
    state = parse_toolbar_state(
        {"page_offset": "-5"},
        kind="rail",
        total_count=60,
    )
    assert state.page_offset == 0


def test_offset_equal_to_last_page_offset_passes_through() -> None:
    """Boundary: the actual last-page offset must not be clamped
    away. 60 rows + page_size=25 → last-page offset is 50."""
    state = parse_toolbar_state(
        {"page_offset": "50"},
        kind="rail",
        total_count=60,
    )
    assert state.page_offset == 50


def test_offset_with_custom_page_size_clamps_to_size_aligned_page() -> None:
    """Page-size honored: 100 rows + page_size=10 → last page starts
    at offset 90; `?page_offset=999&page_size=10` clamps to 90, not
    to some other page boundary."""
    state = parse_toolbar_state(
        {"page_offset": "999", "page_size": "10"},
        kind="rail",
        total_count=100,
    )
    assert state.page_size == 10
    assert state.page_offset == 90
    assert state.page_start == 91
    assert state.page_end == 100


@pytest.mark.parametrize("total,page_size,expected_offset", [
    (1, 25, 0),
    (25, 25, 0),
    (26, 25, 25),
    (50, 25, 25),
    (51, 25, 50),
    (100, 25, 75),
    (16, 10, 10),
    (10, 10, 0),
    (11, 10, 10),
])
def test_last_page_offset_formula(
    total: int, page_size: int, expected_offset: int,
) -> None:
    """Property: for any non-zero list, the clamped offset is the
    one that puts the LAST item on the page (i.e. the page-aligned
    offset just below total_count)."""
    state = parse_toolbar_state(
        {"page_offset": "999999", "page_size": str(page_size)},
        kind="rail",
        total_count=total,
    )
    assert state.page_offset == expected_offset
    assert state.page_end == total
