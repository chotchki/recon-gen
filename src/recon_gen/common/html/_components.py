"""CF.4.a — shared list-toolbar primitive.

Net-new module that houses the typed contract for Studio's
search/sort/paginate toolbars. Lives at the address Q10A picked in
``docs/audits/cf_4_design_review.md``: Phase CG (the originally-
planned home of the primitive) was folded into CF.4 when the design
review surfaced that CG.2's first consumer IS CF.4 — i.e. the
primitive had no load-bearing call site outside CF.4. Per
`[no_future_cleanup_deferrals]` and `[typed_primitives_gt_ad_hoc]`,
the typed primitive ships at this address from day one with CF.4 as
its first consumer; CF.4.h adds additional consumers (Training plant
cards, Investigation account list, etc.) as those surfaces hit
pagination needs.

This module is intentionally consumer-blind. CF.4.b wires the
`/l2_shape/<kind>/` route to parse query params into
``ListToolbarState`` and pass it to ``render_list_toolbar()``; CF.4.d
extends the home page to thread kind-namespaced query params through
to each section's `hx-get` URL.

Operator locks (from CF.4 design review §3):
- Q2A: offset/limit pagination (not cursor).
- Q4B: universal Default / A-Z / Z-A axes + per-kind sentinels via
  the typed ``SORT_AXES_BY_KIND`` map (rail by subtype, template by
  leg-count, chain by parent).
- Q1A/B: URL keys mirror ``_tree_fetcher.py``'s pager
  (``q`` / ``sort_column`` / ``page_offset`` / ``page_size``) at the
  per-kind page; kind-namespaced on the home page (``rail_q``,
  ``template_page``, ...).
- Q7A: toolbar lives in the section body (non-sticky for CF.4; a
  future sticky variant can land as CF.4.j if a cold-read calls for
  it).
- Q9A: the section ``hx-get`` URL IS the state truth; mirrors the
  CF.3.m diagram-URL-is-state pattern.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import Literal

from recon_gen.common.html._studio_assets.tw_classes import (
    chrome_button_classes,
)
from recon_gen.common.l2.editor import SINGLETON_KINDS, EntityKind


# ---------------------------------------------------------------------------
# Typed axes
# ---------------------------------------------------------------------------

# CF.4 lock Q4B — universal axes apply to every list kind; per-kind
# sentinels declare themselves on the right rows of `SORT_AXES_BY_KIND`.
# Why a closed Literal: extending the type is a typed change that
# pyright catches at every consumer site (per
# `[feedback_invariants_in_types]`).
SortAxis = Literal[
    "default",            # YAML-declaration order
    "name_asc",           # A → Z by entity id / display name
    "name_desc",          # Z → A
    "rail_subtype",       # rail only — two-leg before single-leg
    "template_leg_count", # transfer_template only — fewer legs first
    "chain_parent",       # chain only — group by parent endpoint
]

# Human-readable dropdown labels. Kept here (not at the call site)
# so every consumer renders consistent vocabulary.
SORT_AXIS_LABELS: Mapping[SortAxis, str] = {
    "default": "Default",
    "name_asc": "A → Z",
    "name_desc": "Z → A",
    "rail_subtype": "Two-leg first",
    "template_leg_count": "Fewer legs first",
    "chain_parent": "By parent",
}

# Per-kind axis universe. Singleton kinds (theme / instance) have no
# list view and never reach the toolbar — omitted by construction.
# Universal axes appear on every list kind; per-kind sentinels gate
# on the kind tag so the dropdown stays honest.
SORT_AXES_BY_KIND: Mapping[EntityKind, tuple[SortAxis, ...]] = {
    "account": ("default", "name_asc", "name_desc"),
    "account_template": ("default", "name_asc", "name_desc"),
    "rail": ("default", "name_asc", "name_desc", "rail_subtype"),
    "transfer_template": (
        "default", "name_asc", "name_desc", "template_leg_count",
    ),
    "chain": ("default", "name_asc", "name_desc", "chain_parent"),
    "limit_schedule": ("default", "name_asc", "name_desc"),
}


# ---------------------------------------------------------------------------
# Toolbar state
# ---------------------------------------------------------------------------

# CF.4 design review Q6 risk #6 — a crafted `?page_size=9999999` would
# exhaust memory rendering the response. `_tree_fetcher._TABLE_PAGE_SIZE_MAX`
# is 10_000 for SQL row pages; the per-card render is ~50x heavier than
# a `<tr>`, so the editor cap is materially smaller.
PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 200


@dataclass(frozen=True, slots=True)
class ListToolbarState:
    """One toolbar's frozen state — kind / search / sort / page.

    Invariants enforced at construction (per
    `[feedback_invariants_in_types]`):

    - ``kind`` must be a list-kind (singletons rejected — no list view).
    - ``sort_axis`` must belong to ``SORT_AXES_BY_KIND[kind]``.
    - ``page_offset >= 0``.
    - ``1 <= page_size <= PAGE_SIZE_MAX``.
    - ``total_count >= 0``.

    ``q`` carries the operator's raw search term (whitespace-stripped at
    construction). An empty string means "no filter active"; the
    Q1A/B-namespaced URL keys distinguish absent from empty by not
    emitting the key at all when ``q == ""``.
    """

    kind: EntityKind
    q: str = ""
    sort_axis: SortAxis = "default"
    page_offset: int = 0
    page_size: int = PAGE_SIZE_DEFAULT
    total_count: int = 0
    # Kind-namespaced URL keys (CF.4.d). When None, the per-kind page
    # uses bare ``q``/``sort_column``/``page_offset``/``page_size``;
    # when populated, the home-page embed fragment uses prefixed
    # variants like ``rail_q`` / ``rail_page`` so the home URL can
    # carry every section's state at once.
    url_prefix: str = ""

    def __post_init__(self) -> None:
        # Reject singletons. They route differently (single edit form,
        # no list view) and have no toolbar.
        if self.kind in SINGLETON_KINDS:
            raise ValueError(
                f"ListToolbarState rejects singleton kind {self.kind!r} "
                "— no list view, no toolbar",
            )
        # Strip the search term so we treat "  " as empty.
        object.__setattr__(self, "q", self.q.strip())
        # Sort axis must be in this kind's universe.
        allowed = SORT_AXES_BY_KIND[self.kind]
        if self.sort_axis not in allowed:
            raise ValueError(
                f"sort_axis {self.sort_axis!r} not in "
                f"SORT_AXES_BY_KIND[{self.kind!r}]={allowed!r}",
            )
        if self.page_offset < 0:
            raise ValueError(f"page_offset must be >= 0, got {self.page_offset}")
        if self.page_size < 1 or self.page_size > PAGE_SIZE_MAX:
            raise ValueError(
                f"page_size must be in [1, {PAGE_SIZE_MAX}], "
                f"got {self.page_size}",
            )
        if self.total_count < 0:
            raise ValueError(
                f"total_count must be >= 0, got {self.total_count}",
            )

    # ---- URL key namespacing (CF.4 Q1A vs Q1B) -----------------------------

    def _key(self, base: str) -> str:
        """Apply the `<prefix>_` namespace when set; bare key otherwise."""
        return f"{self.url_prefix}_{base}" if self.url_prefix else base

    @property
    def key_q(self) -> str:
        return self._key("q")

    @property
    def key_sort(self) -> str:
        return self._key("sort_column")

    @property
    def key_offset(self) -> str:
        return self._key("page_offset")

    @property
    def key_size(self) -> str:
        return self._key("page_size")

    # ---- Pagination math --------------------------------------------------

    @property
    def page_end(self) -> int:
        """Index of the last item on this page (1-based, capped at total).
        Returns 0 when ``total_count`` is 0 so the chrome reads "0 of 0"
        cleanly."""
        if self.total_count == 0:
            return 0
        return min(self.page_offset + self.page_size, self.total_count)

    @property
    def page_start(self) -> int:
        """1-based index of the first item on this page; 0 when empty."""
        if self.total_count == 0:
            return 0
        return self.page_offset + 1

    @property
    def has_prev(self) -> bool:
        return self.page_offset > 0

    @property
    def has_next(self) -> bool:
        return self.page_end < self.total_count


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_list_toolbar(
    state: ListToolbarState,
    *,
    submit_url: str,
    swap_target_id: str,
    embed: bool = False,
) -> str:
    """Render the search / sort / paginate toolbar for one section.

    Args:
        state: The current toolbar state.
        submit_url: GET URL that receives ``state.key_*`` params on
            change. For the dedicated per-kind page this is
            ``/l2_shape/<kind>/``; for the home page embed this is
            also ``/l2_shape/<kind>/?embed=1``.
        swap_target_id: HTML id of the element htmx swaps the response
            into (typically the section body so the cards refresh
            without a full-page navigation).
        embed: When True, the form omits ``hx-push-url`` so the
            iframe / embed host page's URL stays unchanged.

    Output shape (top to bottom):
        - Row 1: search input + sort dropdown (debounce-on-input,
          submit-on-Enter for the search; immediate submit on sort
          change).
        - Row 2: ``Showing X-Y of Z`` indicator + Prev / Next buttons.

    Per CF.4 Q7A, the toolbar is NOT sticky in CF.4 — the markup is a
    plain `<form>` inside the section body. A sticky variant can land
    as a CF.4.j sub-cell if a cold-read calls for it.
    """
    # CF.4 Q9A — `hx-push-url` only on navigation events. Search input
    # debounces and pushes URL on Enter (`changed delay:300ms`); sort
    # dropdown pushes immediately; pager buttons (real links, not form
    # submits) push via the browser navigation.
    push_attr = ' hx-push-url="true"' if not embed else ""
    input_cls = (
        "flex-1 min-w-0 px-2 py-1 border border-surface-border "
        "rounded-sm text-sm focus:border-accent focus:outline-none"
    )
    select_cls = (
        "px-2 py-1 border border-surface-border rounded-sm text-sm "
        "bg-white focus:border-accent focus:outline-none"
    )
    btn_cls = chrome_button_classes()
    btn_disabled_cls = (
        f"{btn_cls} opacity-50 cursor-not-allowed pointer-events-none"
    )

    sort_options = "".join(
        f'<option value="{axis}"'
        + (' selected' if axis == state.sort_axis else "")
        + f">{escape(SORT_AXIS_LABELS[axis])}</option>"
        for axis in SORT_AXES_BY_KIND[state.kind]
    )

    # Range indicator reads "Showing 1-25 of 117" when populated, or
    # "No matches" when the filter excludes everything, or "0 entities"
    # when the kind itself is empty.
    if state.total_count == 0:
        range_html = (
            '<span class="text-xs text-secondary-fg">No matches</span>'
            if state.q
            else '<span class="text-xs text-secondary-fg">0 entities</span>'
        )
    else:
        range_html = (
            f'<span class="text-xs text-secondary-fg" '
            f'data-role="toolbar-range">'
            f"Showing {state.page_start}–{state.page_end} of "
            f"{state.total_count}</span>"
        )

    # Pager: prev / next buttons. Real anchors so browser back works
    # naturally; htmx intercepts via `hx-get` / `hx-boost`-style attrs.
    # Build the URL by serializing every key the toolbar tracks.
    def _page_url(new_offset: int) -> str:
        parts = [
            f"{escape(state.key_offset)}={new_offset}",
            f"{escape(state.key_size)}={state.page_size}",
        ]
        if state.q:
            parts.append(f"{escape(state.key_q)}={escape(state.q)}")
        if state.sort_axis != "default":
            parts.append(
                f"{escape(state.key_sort)}={escape(state.sort_axis)}",
            )
        if embed:
            parts.append("embed=1")
        sep = "&" if "?" in submit_url else "?"
        return f"{submit_url}{sep}{'&'.join(parts)}"

    prev_offset = max(0, state.page_offset - state.page_size)
    next_offset = state.page_offset + state.page_size
    prev_html = (
        f'<a class="{btn_cls}" '
        f'hx-get="{_page_url(prev_offset)}" '
        f'hx-target="#{swap_target_id}" hx-swap="innerHTML"{push_attr} '
        f'href="{_page_url(prev_offset)}">← Prev</a>'
        if state.has_prev
        else f'<span class="{btn_disabled_cls}">← Prev</span>'
    )
    next_html = (
        f'<a class="{btn_cls}" '
        f'hx-get="{_page_url(next_offset)}" '
        f'hx-target="#{swap_target_id}" hx-swap="innerHTML"{push_attr} '
        f'href="{_page_url(next_offset)}">Next →</a>'
        if state.has_next
        else f'<span class="{btn_disabled_cls}">Next →</span>'
    )

    return (
        f'<form class="flex flex-col gap-2 p-2 mb-2 bg-link-tint '
        f'border border-surface-border rounded-sm" '
        f'data-role="list-toolbar" data-kind="{state.kind}" '
        f'method="get" action="{escape(submit_url)}" '
        f'hx-get="{escape(submit_url)}" '
        f'hx-target="#{swap_target_id}" hx-swap="innerHTML" '
        f'hx-trigger="submit, change from:select"{push_attr}>'
        f'<div class="flex items-center gap-2">'
        f'<input type="search" name="{escape(state.key_q)}" '
        f'value="{escape(state.q)}" '
        f'placeholder="Search {escape(state.kind)}s…" '
        f'class="{input_cls}" '
        f'autocomplete="off">'
        f'<select name="{escape(state.key_sort)}" '
        f'class="{select_cls}" '
        f'aria-label="Sort {escape(state.kind)}s">'
        f"{sort_options}"
        f"</select>"
        f"</div>"
        f'<div class="flex items-center justify-between gap-2">'
        f"{range_html}"
        f'<div class="flex items-center gap-1">'
        f"{prev_html}{next_html}"
        f"</div>"
        f"</div>"
        f"</form>"
    )


# ---------------------------------------------------------------------------
# Query-param parsing (CF.4.b)
# ---------------------------------------------------------------------------


def parse_toolbar_state(
    query_params: Mapping[str, str],
    *,
    kind: EntityKind,
    total_count: int,
    url_prefix: str = "",
) -> ListToolbarState:
    """Pull toolbar state from a request's query params, clamping
    every input that ``ListToolbarState.__post_init__`` would reject.

    Callers pass ``request.query_params`` (Starlette's
    ``QueryParams``). Unknown / malformed values fall back to safe
    defaults — the rendered toolbar then reads as "no search, default
    sort, first page" rather than 500ing the request. Risk #6 (DoS via
    crafted ``?page_size=9999999``) is mitigated here: any value
    outside ``[1, PAGE_SIZE_MAX]`` clamps to the cap.

    ``url_prefix`` chooses Q1A vs Q1B URL keys (bare keys for the
    per-kind page; ``<prefix>_`` namespaced for the home page where
    every section's state co-exists in the URL).
    """
    def key(base: str) -> str:
        return f"{url_prefix}_{base}" if url_prefix else base

    raw_q = (query_params.get(key("q")) or "").strip()

    raw_sort = query_params.get(key("sort_column")) or "default"
    if raw_sort not in SORT_AXES_BY_KIND[kind]:
        raw_sort = "default"

    try:
        raw_offset = int(query_params.get(key("page_offset")) or "0")
    except ValueError:
        raw_offset = 0
    raw_offset = max(0, raw_offset)

    try:
        raw_size = int(query_params.get(key("page_size")) or PAGE_SIZE_DEFAULT)
    except ValueError:
        raw_size = PAGE_SIZE_DEFAULT
    raw_size = max(1, min(raw_size, PAGE_SIZE_MAX))

    return ListToolbarState(
        kind=kind,
        q=raw_q,
        sort_axis=raw_sort,  # type: ignore[arg-type]: post-validated above against SORT_AXES_BY_KIND[kind]
        page_offset=raw_offset,
        page_size=raw_size,
        total_count=max(0, total_count),
        url_prefix=url_prefix,
    )


__all__ = [
    "PAGE_SIZE_DEFAULT",
    "PAGE_SIZE_MAX",
    "SORT_AXES_BY_KIND",
    "SORT_AXIS_LABELS",
    "ListToolbarState",
    "SortAxis",
    "parse_toolbar_state",
    "render_list_toolbar",
]
