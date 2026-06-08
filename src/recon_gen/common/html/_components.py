"""CF.4.a — shared list-toolbar primitive.

Net-new module that houses the typed contract for Studio's
search/sort/paginate toolbars. Lives at the address Q10A picked in
``docs/audits/_archive/cf_4_design_review.md``: Phase CG (the originally-
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
# Operator-readable kind labels (CG.13 — 2026-06-05)
# ---------------------------------------------------------------------------
#
# Underscored `EntityKind` enum values (`account_template`,
# `transfer_template`, `limit_schedule`) leaked into screen-reader
# announcements ("Search account_templates"), `<title>` bars
# ("Edit account_template: cust-001 — Studio"), and tooltip hovers
# ("Create a new transfer_template"). Map each enum value to its
# operator-readable form once + thread through every aria-label /
# tooltip / title / h1 site so the underscore is gone everywhere.

_KIND_LABEL_SINGULAR: Mapping[EntityKind, str] = {
    "account": "account",
    "account_template": "account template",
    "rail": "rail",
    "transfer_template": "transfer template",
    "chain": "chain",
    "limit_schedule": "limit schedule",
    "theme": "theme",
    "instance": "instance settings",
}

_KIND_LABEL_PLURAL: Mapping[EntityKind, str] = {
    "account": "Accounts",
    "account_template": "Account templates",
    "rail": "Rails",
    "transfer_template": "Transfer templates",
    "chain": "Chains",
    "limit_schedule": "Limit schedules",
    "theme": "Theme",
    "instance": "Instance settings",
}


def kind_label_singular(kind: EntityKind) -> str:
    """Operator-readable singular form ("account template", "rail").
    Lowercase — fits mid-sentence ("Create a new account template").
    Falls back to the raw enum value on an unknown kind so a new
    `EntityKind` variant fails loud rather than silent."""
    return _KIND_LABEL_SINGULAR.get(kind, kind)


def kind_label_plural(kind: EntityKind, *, lowercase: bool = False) -> str:
    """Operator-readable plural form. Title Case by default ("Account
    templates") for h1s + `<title>` bars; pass ``lowercase=True``
    for aria-labels / mid-sentence use ("Search account templates")."""
    label = _KIND_LABEL_PLURAL.get(kind, f"{kind}s")
    return label.lower() if lowercase else label


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


def _page_url(state: ListToolbarState, submit_url: str, embed: bool,
               new_offset: int) -> str:
    """Pager-link URL — serializes every state key the toolbar tracks
    so back/forward navigation reproduces the same view."""
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
    # CF.4.j cold-read P0 #3 (2026-06-05): submit_url already carries
    # `?embed=1` for the embed flow (see list_view), so appending
    # `embed=1` again produced `?embed=1&...&embed=1`. Query parsers
    # take the last value, so it doesn't break — but it's a smell
    # that bites the next consumer who adds a third param. Only
    # append when submit_url doesn't already have it.
    if embed and "embed=1" not in submit_url:
        parts.append("embed=1")
    sep = "&" if "?" in submit_url else "?"
    return f"{submit_url}{sep}{'&'.join(parts)}"


def render_list_pager(
    state: ListToolbarState,
    *,
    submit_url: str,
    swap_target_id: str,
    embed: bool = False,
) -> str:
    """Range indicator + Prev/Next pager bar — sits BELOW the cards
    grid to match the dashboard table-pager convention (`mt-3`
    margin-top on the dashboard pager — operator lock 2026-06-05 to
    use the same vertical placement here).

    Output: a flex row with the range on the left ("Showing 26–50 of
    117", "No matches", or "0 entities") and Prev / Next on the
    right. No form — both buttons are anchor-based GETs serializing
    state into the URL via `_page_url`. Renders nothing visible when
    there's only one page AND no filter is active (still emits the
    container so `data-role="list-pager"` is locatable by tests).
    """
    push_attr = ' hx-push-url="true"' if not embed else ""
    btn_cls = chrome_button_classes()
    btn_disabled_cls = (
        f"{btn_cls} opacity-50 cursor-not-allowed pointer-events-none"
    )

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

    prev_offset = max(0, state.page_offset - state.page_size)
    next_offset = state.page_offset + state.page_size
    prev_url = _page_url(state, submit_url, embed, prev_offset)
    next_url = _page_url(state, submit_url, embed, next_offset)
    prev_html = (
        f'<a class="{btn_cls}" '
        f'hx-get="{prev_url}" '
        f'hx-target="#{swap_target_id}" hx-swap="innerHTML"{push_attr} '
        f'href="{prev_url}">← Prev</a>'
        if state.has_prev
        else f'<span class="{btn_disabled_cls}">← Prev</span>'
    )
    next_html = (
        f'<a class="{btn_cls}" '
        f'hx-get="{next_url}" '
        f'hx-target="#{swap_target_id}" hx-swap="innerHTML"{push_attr} '
        f'href="{next_url}">Next →</a>'
        if state.has_next
        else f'<span class="{btn_disabled_cls}">Next →</span>'
    )
    # `mt-3` matches `bootstrap.js`'s table-pager class so the visual
    # rhythm is the same as dashboard tables.
    return (
        f'<div class="flex items-center justify-between gap-2 mt-3 '
        f'px-3 text-sm text-secondary-fg" '
        f'data-role="list-pager" data-kind="{state.kind}">'
        f"{range_html}"
        f'<div class="flex items-center gap-1">'
        f"{prev_html}{next_html}"
        f"</div>"
        f"</div>"
    )


def render_list_search(
    state: ListToolbarState,
    *,
    submit_url: str,
    swap_target_id: str,
    embed: bool = False,
) -> str:
    """Standalone-page search input form — sits ABOVE the cards.
    Used only when the upstream surface (e.g. the home page's
    `<details>` summary) doesn't already own the search input. In
    the home-page embed flow, return the empty string at the call
    site (see `list_view`)."""
    push_attr = ' hx-push-url="true"' if not embed else ""
    input_cls = (
        "flex-1 min-w-0 px-2 py-1 border border-surface-border "
        "rounded-sm text-sm focus:border-accent focus:outline-none"
    )
    return (
        f'<form class="flex items-center gap-2 p-2 mb-2 bg-link-tint '
        f'border border-surface-border rounded-sm" '
        f'data-role="list-search" data-kind="{state.kind}" '
        f'method="get" action="{escape(submit_url)}" '
        f'hx-get="{escape(submit_url)}" '
        f'hx-target="#{swap_target_id}" hx-swap="innerHTML" '
        f'hx-trigger="submit"{push_attr}>'
        f'<input type="search" name="{escape(state.key_q)}" '
        f'value="{escape(state.q)}" '
        f'placeholder="Search {escape(state.kind)}s…" '
        f'class="{input_cls}" '
        f'autocomplete="off">'
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

    # CG.17 (2026-06-05) — high-end clamp on page_offset.
    # Cold-read v4 P0 #2: ?page_offset=999 on a 16-row list rendered
    # `Showing 1000–16 of 16` (negative was already clamped to 0;
    # high end was missing). Clamp to the offset of the page that
    # CONTAINS the last item, so a stale URL on a shrunken list
    # lands the operator on the actual last page instead of the
    # first. Empty list → 0 (the only valid offset).
    safe_total = max(0, total_count)
    last_page_offset = (
        ((safe_total - 1) // raw_size) * raw_size if safe_total > 0 else 0
    )
    raw_offset = min(raw_offset, last_page_offset)

    return ListToolbarState(
        kind=kind,
        q=raw_q,
        sort_axis=raw_sort,  # type: ignore[arg-type]: post-validated above against SORT_AXES_BY_KIND[kind]
        page_offset=raw_offset,
        page_size=raw_size,
        total_count=max(0, total_count),
        url_prefix=url_prefix,
    )


def render_summary_search_input(
    *,
    kind: EntityKind,
    initial_q: str,
    section_url: str,
    body_id: str,
    url_prefix: str = "",
) -> str:
    """Render the per-section search input that lives in the
    `<details>` summary.

    Operator lock (CF.4 followup, 2026-06-05): search-in-summary +
    auto-open. Typing fires an htmx refetch of the section body and
    sets `details[open]` so results show up immediately.

    Args:
        kind: The section's entity kind. Drives the placeholder text
            + the kind-aware ``aria-label``.
        initial_q: Current value of the search input — comes from the
            home URL's ``<kind>_q`` param so the input reflects the
            active search after a refresh.
        section_url: The lazy-fetch URL for the section body. Already
            carries ``?embed=1`` and any other state baked by the home
            page (sort, page, etc.). htmx appends the search input's
            value to this URL on every keystroke (debounced) via
            `hx-include`.
        body_id: HTML id of the `<details>` body so htmx swaps the
            cards container, not the summary.
        url_prefix: Kept for API symmetry with `ListToolbarState`,
            but currently unused — the input's submission key is
            ALWAYS the bare `q` because the htmx GET targets the
            section endpoint (`/l2_shape/<kind>/?embed=1`) which
            parses the bare key (Q1A shape). The kind-prefix only
            matters for the home page URL state truth (Q1B —
            `/?rail_q=foo`), and that's read+rendered by the home
            page render code, not submitted by this input.
            Operator dogfood 2026-06-05: submitting `rail_q=foo` to
            the section endpoint silently dropped the filter — the
            endpoint parsed `q=""` and returned all rails.

    Output is one ``<input type="search">`` styled to fit in the
    summary row. ``onclick="event.stopPropagation()"`` so clicking the
    input doesn't toggle the parent ``<details>``; ``oninput`` opens
    the details so first keystroke surfaces results.
    """
    del url_prefix  # see note above — kept for API symmetry only
    input_cls = (
        "ml-2 px-2 py-0.5 border border-surface-border rounded-sm "
        "text-xs font-normal w-40 focus:border-accent focus:outline-none"
    )
    # Trigger: debounce 300ms on input changed; submit immediately on
    # Enter. `hx-trigger="search"` would also fire on the X-clear
    # button click in WebKit/Chrome — folded into the same trigger.
    return (
        f'<input type="search" name="q" '
        f'value="{escape(initial_q)}" '
        f'placeholder="Search…" '
        f'aria-label="Search {escape(kind_label_plural(kind, lowercase=True))}" '
        f'class="{input_cls}" '
        f'autocomplete="off" '
        f'hx-get="{escape(section_url)}" '
        f'hx-target="#{escape(body_id)}" hx-swap="innerHTML" '
        f'hx-trigger="input changed delay:300ms, search" '
        f'hx-include="this" '
        f"onclick=\"event.stopPropagation()\" "
        f"oninput=\"this.closest('details').open=true\">"
    )


__all__ = [
    "PAGE_SIZE_DEFAULT",
    "PAGE_SIZE_MAX",
    "SORT_AXES_BY_KIND",
    "SORT_AXIS_LABELS",
    "ListToolbarState",
    "SortAxis",
    "parse_toolbar_state",
    "render_list_pager",
    "render_list_search",
    "render_summary_search_input",
]
