"""CF.4.a — typed list-toolbar primitive.

Pins the contract for `common/html/_components.py`'s
`ListToolbarState` dataclass + `render_list_toolbar()` helper.
Future CF.4.b/c/d/h consumers depend on this shape; CG was folded
into CF.4 per the design review (`docs/audits/cf_4_design_review.md`)
so this is also the load-bearing contract test for the typed primitive.
"""

from __future__ import annotations

import pytest

from recon_gen.common.html._components import (
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
    SORT_AXES_BY_KIND,
    SORT_AXIS_LABELS,
    ListToolbarState,
    render_list_toolbar,
)
from recon_gen.common.l2.editor import SINGLETON_KINDS


# ---------------------------------------------------------------------------
# SortAxis / SORT_AXES_BY_KIND typed contract
# ---------------------------------------------------------------------------

def test_sort_axes_cover_every_list_kind() -> None:
    """Every non-singleton ``EntityKind`` has an entry in
    ``SORT_AXES_BY_KIND`` — otherwise a kind would route to a list page
    with no toolbar at all (pyright catches the typed-Mapping miss
    locally, this gate verifies it dynamically)."""
    list_kinds = {
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    }
    # Singletons must NOT appear (they have no list view).
    assert list_kinds.isdisjoint(SINGLETON_KINDS)
    assert set(SORT_AXES_BY_KIND.keys()) == list_kinds


def test_universal_axes_present_on_every_kind() -> None:
    """`default` / `name_asc` / `name_desc` are the universal axes;
    every kind exposes them so the dropdown reads consistently."""
    universal = {"default", "name_asc", "name_desc"}
    for kind, axes in SORT_AXES_BY_KIND.items():
        assert universal.issubset(set(axes)), (
            f"{kind!r} missing universal axes; has {axes!r}"
        )


def test_per_kind_sentinels_only_on_their_kind() -> None:
    """`rail_subtype` only on rail, `template_leg_count` only on
    transfer_template, `chain_parent` only on chain — the typed map
    encodes the per-kind shape (see `[feedback_invariants_in_types]`).
    """
    assert "rail_subtype" in SORT_AXES_BY_KIND["rail"]
    assert "template_leg_count" in SORT_AXES_BY_KIND["transfer_template"]
    assert "chain_parent" in SORT_AXES_BY_KIND["chain"]
    # And they don't bleed across kinds.
    for kind, axes in SORT_AXES_BY_KIND.items():
        if kind != "rail":
            assert "rail_subtype" not in axes
        if kind != "transfer_template":
            assert "template_leg_count" not in axes
        if kind != "chain":
            assert "chain_parent" not in axes


def test_every_axis_has_a_label() -> None:
    """Every axis that any kind exposes has a human-readable label."""
    all_axes = {axis for axes in SORT_AXES_BY_KIND.values() for axis in axes}
    assert set(SORT_AXIS_LABELS.keys()) >= all_axes


# ---------------------------------------------------------------------------
# ListToolbarState invariants
# ---------------------------------------------------------------------------

def test_state_defaults() -> None:
    """Bare construction: q empty, sort_axis=default, page_offset=0,
    page_size=PAGE_SIZE_DEFAULT, total_count=0."""
    state = ListToolbarState(kind="rail")
    assert state.q == ""
    assert state.sort_axis == "default"
    assert state.page_offset == 0
    assert state.page_size == PAGE_SIZE_DEFAULT
    assert state.total_count == 0
    assert state.url_prefix == ""


def test_state_rejects_singleton_kind() -> None:
    """Singletons have no list view — constructing state for them is a
    typo / category error. Fail at construction."""
    for kind in SINGLETON_KINDS:
        with pytest.raises(ValueError, match="singleton kind"):
            ListToolbarState(kind=kind)  # type: ignore[arg-type]: deliberately passing a singleton EntityKind so pyright would block in real code; this test exercises the runtime __post_init__ guard


def test_state_rejects_sort_axis_not_in_kind_universe() -> None:
    """`template_leg_count` is rail-incompatible; constructing such a
    state must fail (pyright also catches the literal value but the
    state machine validates dynamic input from URL params)."""
    with pytest.raises(ValueError, match="not in SORT_AXES_BY_KIND"):
        ListToolbarState(kind="rail", sort_axis="template_leg_count")
    with pytest.raises(ValueError, match="not in SORT_AXES_BY_KIND"):
        ListToolbarState(kind="account", sort_axis="rail_subtype")


def test_state_strips_whitespace_search_term() -> None:
    """Operator types `  foo  ` → state.q == `foo`. Critical for the
    URL serializer to omit the key entirely on empty searches."""
    state = ListToolbarState(kind="rail", q="  foo  ")
    assert state.q == "foo"
    state = ListToolbarState(kind="rail", q="   ")
    assert state.q == ""


def test_state_rejects_negative_page_offset() -> None:
    with pytest.raises(ValueError, match="page_offset must be >= 0"):
        ListToolbarState(kind="rail", page_offset=-1)


def test_state_rejects_page_size_zero() -> None:
    with pytest.raises(ValueError, match=r"page_size must be in \[1,"):
        ListToolbarState(kind="rail", page_size=0)


def test_state_rejects_page_size_over_max() -> None:
    """Risk #6 mitigation: a crafted `?page_size=9999999` would exhaust
    memory rendering ~9M cards. Cap at construction."""
    with pytest.raises(ValueError, match=r"page_size must be in \[1,"):
        ListToolbarState(kind="rail", page_size=PAGE_SIZE_MAX + 1)


def test_state_accepts_page_size_at_max() -> None:
    state = ListToolbarState(kind="rail", page_size=PAGE_SIZE_MAX)
    assert state.page_size == PAGE_SIZE_MAX


def test_state_rejects_negative_total_count() -> None:
    with pytest.raises(ValueError, match="total_count must be >= 0"):
        ListToolbarState(kind="rail", total_count=-5)


# ---------------------------------------------------------------------------
# URL key namespacing (Q1A vs Q1B)
# ---------------------------------------------------------------------------

def test_url_keys_bare_when_no_prefix() -> None:
    """Per-kind page (`/l2_shape/<kind>/`) uses bare keys matching
    `_tree_fetcher.py`'s URL contract."""
    state = ListToolbarState(kind="rail")
    assert state.key_q == "q"
    assert state.key_sort == "sort_column"
    assert state.key_offset == "page_offset"
    assert state.key_size == "page_size"


def test_url_keys_prefixed_when_url_prefix_set() -> None:
    """Home page embed (`/`) uses kind-namespaced keys so each
    section's state survives a unioned URL like
    `?rail_q=foo&template_page_offset=25`."""
    state = ListToolbarState(kind="rail", url_prefix="rail")
    assert state.key_q == "rail_q"
    assert state.key_sort == "rail_sort_column"
    assert state.key_offset == "rail_page_offset"
    assert state.key_size == "rail_page_size"


# ---------------------------------------------------------------------------
# Pagination math
# ---------------------------------------------------------------------------

def test_pagination_math_first_page_full() -> None:
    state = ListToolbarState(
        kind="rail", page_offset=0, page_size=25, total_count=117,
    )
    assert state.page_start == 1
    assert state.page_end == 25
    assert not state.has_prev
    assert state.has_next


def test_pagination_math_middle_page() -> None:
    state = ListToolbarState(
        kind="rail", page_offset=25, page_size=25, total_count=117,
    )
    assert state.page_start == 26
    assert state.page_end == 50
    assert state.has_prev
    assert state.has_next


def test_pagination_math_last_page_partial() -> None:
    """Last page may be smaller than page_size — page_end clamps to
    total_count so "Showing 101-117 of 117" reads correctly."""
    state = ListToolbarState(
        kind="rail", page_offset=100, page_size=25, total_count=117,
    )
    assert state.page_start == 101
    assert state.page_end == 117
    assert state.has_prev
    assert not state.has_next


def test_pagination_math_empty_total() -> None:
    """No entities → "0 of 0" reads without an off-by-one."""
    state = ListToolbarState(
        kind="rail", page_offset=0, page_size=25, total_count=0,
    )
    assert state.page_start == 0
    assert state.page_end == 0
    assert not state.has_prev
    assert not state.has_next


# ---------------------------------------------------------------------------
# render_list_toolbar markup contract
# ---------------------------------------------------------------------------

def test_render_emits_search_input_with_current_value() -> None:
    state = ListToolbarState(kind="rail", q="ach", total_count=42)
    html = render_list_toolbar(
        state, submit_url="/l2_shape/rail/", swap_target_id="rail-list",
    )
    assert 'type="search"' in html
    assert 'name="q"' in html
    assert 'value="ach"' in html
    # Kind-aware placeholder.
    assert 'placeholder="Search rails…"' in html


def test_render_emits_sort_dropdown_with_kind_options() -> None:
    """rail's dropdown must include `rail_subtype`; account's must not."""
    rail_html = render_list_toolbar(
        ListToolbarState(kind="rail"),
        submit_url="/l2_shape/rail/", swap_target_id="x",
    )
    assert 'value="rail_subtype"' in rail_html
    assert ">Two-leg first<" in rail_html

    account_html = render_list_toolbar(
        ListToolbarState(kind="account"),
        submit_url="/l2_shape/account/", swap_target_id="x",
    )
    assert 'value="rail_subtype"' not in account_html


def test_render_selects_current_sort_axis() -> None:
    state = ListToolbarState(
        kind="rail", sort_axis="name_desc",
    )
    html = render_list_toolbar(
        state, submit_url="/l2_shape/rail/", swap_target_id="x",
    )
    # The Z→A option carries `selected`.
    assert 'value="name_desc" selected>Z → A<' in html


def test_render_emits_pager_with_range_indicator() -> None:
    state = ListToolbarState(
        kind="rail", page_offset=25, page_size=25, total_count=117,
    )
    html = render_list_toolbar(
        state, submit_url="/l2_shape/rail/", swap_target_id="x",
    )
    assert "Showing 26–50 of 117" in html
    assert "← Prev" in html
    assert "Next →" in html


def test_render_pager_disables_prev_on_first_page() -> None:
    state = ListToolbarState(
        kind="rail", page_offset=0, page_size=25, total_count=117,
    )
    html = render_list_toolbar(
        state, submit_url="/l2_shape/rail/", swap_target_id="x",
    )
    # Disabled state renders as a span with `opacity-50 cursor-not-allowed`
    # so the anchor doesn't fire htmx — operator can't go below offset 0.
    assert "opacity-50 cursor-not-allowed" in html
    # And the disabled Prev is a span, not an anchor; the enabled Next
    # is still an anchor with hx-get.
    assert "<span class=" in html and "← Prev" in html
    assert 'hx-get=' in html  # the Next button


def test_render_pager_disables_next_on_last_page() -> None:
    state = ListToolbarState(
        kind="rail", page_offset=100, page_size=25, total_count=117,
    )
    html = render_list_toolbar(
        state, submit_url="/l2_shape/rail/", swap_target_id="x",
    )
    assert "opacity-50 cursor-not-allowed" in html
    # Prev should be enabled (anchor), Next disabled (span).
    assert html.index("← Prev") < html.index("Next →")


def test_render_empty_total_says_no_entities() -> None:
    state = ListToolbarState(
        kind="chain", page_offset=0, page_size=25, total_count=0,
    )
    html = render_list_toolbar(
        state, submit_url="/l2_shape/chain/", swap_target_id="x",
    )
    assert "0 entities" in html


def test_render_search_with_no_matches_says_no_matches() -> None:
    state = ListToolbarState(
        kind="rail", q="bogus",
        page_offset=0, page_size=25, total_count=0,
    )
    html = render_list_toolbar(
        state, submit_url="/l2_shape/rail/", swap_target_id="x",
    )
    assert "No matches" in html


def test_render_wires_htmx_target_and_submit_url() -> None:
    state = ListToolbarState(kind="rail", total_count=100)
    html = render_list_toolbar(
        state,
        submit_url="/l2_shape/rail/?embed=1",
        swap_target_id="rail-section-body",
    )
    assert 'hx-target="#rail-section-body"' in html
    assert 'hx-get="/l2_shape/rail/?embed=1"' in html


def test_render_omits_hx_push_url_in_embed_mode() -> None:
    """Embed mode keeps the host page's URL stable (operator clicks a
    Prev button inside the home page section, the home URL still
    reads `/`, not the section's `/l2_shape/rail/?…`)."""
    state = ListToolbarState(kind="rail", total_count=100)
    embed = render_list_toolbar(
        state, submit_url="/l2_shape/rail/?embed=1",
        swap_target_id="x", embed=True,
    )
    standalone = render_list_toolbar(
        state, submit_url="/l2_shape/rail/",
        swap_target_id="x", embed=False,
    )
    assert 'hx-push-url="true"' not in embed
    assert 'hx-push-url="true"' in standalone


def test_render_pager_urls_carry_state() -> None:
    """The pager's URL serializes ``q`` + ``sort_axis`` + page params
    so clicking Next preserves the operator's search + sort."""
    state = ListToolbarState(
        kind="rail", q="foo", sort_axis="name_desc",
        page_offset=25, page_size=25, total_count=117,
    )
    html = render_list_toolbar(
        state, submit_url="/l2_shape/rail/", swap_target_id="x",
    )
    # Next button points at offset=50 and carries q + sort.
    assert "page_offset=50" in html
    assert "q=foo" in html
    assert "sort_column=name_desc" in html


def test_render_url_prefix_namespacing_carries_through() -> None:
    """Home page embed: the pager's URL uses prefixed keys so the home
    URL can carry every section's state without collision."""
    state = ListToolbarState(
        kind="rail", url_prefix="rail",
        page_offset=25, page_size=25, total_count=117,
    )
    html = render_list_toolbar(
        state, submit_url="/", swap_target_id="x",
    )
    assert "rail_page_offset=50" in html
    assert 'name="rail_q"' in html
    assert 'name="rail_sort_column"' in html
