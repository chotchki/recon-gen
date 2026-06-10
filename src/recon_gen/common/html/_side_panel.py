"""Side-panel drawer infrastructure (BTa.1).

The right-edge slide-out drawer that hosts:

- glossary definitions for opaque vocabulary (L2, Rail, Hook,
  Matview, LimitSchedule, Chain, Slice, etc.) — opened from the
  top-nav ``[?]`` button OR from per-field inline ``[?]`` triggers
- per-page help text for specific fields (consumed by BX.12-15
  cells via the same drawer chrome)
- chain arrow diagrams for the Probe page (BTa.5)
- entity edit-page help (consumed by BX.13 cells)
- per-row metadata trees for ``Table.metadata_popup=True`` tables
  (CY.5) — the row's metadata JSON travels as a query param sourced
  from the already-rendered row payload (stateless; no per-click
  DB round-trip).

Single chrome, multiple content fragments. Triggers are
``<button>`` / ``<a>`` elements that ``hx-get`` an HTML fragment
into the drawer body; the drawer slides in via CSS transition.
Dismissable via the X button + Escape key + click outside the
drawer.

Per BTa.0 Lock 1 (slide-out drawer ~30-35% viewport, hx-get
fragments) and Lock §2.b (operator's drift concern): GLOSSARY is
a single ``dict[str, str]`` constant in this module — both the
top-nav full glossary fragment AND per-term ``[?]`` inline
triggers read from the same source. Adding a new term touches
one line in one place.

Per BTa.0.5 §7 Q2 lock (operator: "add progressively as we get
pushback, try to keep it to the first mention on a page") —
inline triggers are scattered conservatively; the top-nav button
is the always-on entry. BX.12 etc. add the per-field triggers
as the cold-read v3 surfaces specific pain points.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from html import escape
from typing import TYPE_CHECKING, Any, cast

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

if TYPE_CHECKING:
    from recon_gen.common.l2.cache import L2InstanceCache
    from recon_gen.common.tree.structure import Sheet


# -- Glossary content (single source of truth) --------------------------------


# BTa.0.5 §2.b operator concern: "How can we minimize duplication of
# content? it will drift otherwise" — answer: one dict, read by both
# the full-glossary fragment and the per-term deep-link route. Term
# keys are lowercase-slug; markdown-friendly definitions; cite SPEC
# section when relevant so the operator can pull more depth.
#
# Add terms one line at a time as cold-read passes flag new vocabulary
# friction. Don't pre-populate speculatively — operator's "add
# progressively" lock.
GLOSSARY: dict[str, str] = {
    "l2": (
        "**L2** is your institution's declared topology — accounts, "
        "account templates, rails, transfer templates, chains, limit "
        "schedules — encoded in one YAML file the system loads at "
        "deploy time. L1 is the persona-blind reconciliation invariants "
        "(SPEC §F1-§F5); L2 is your institution's specific shape; L3 "
        "is per-customer flavor (institution name, theme, persona). "
        "The `L2 Editor` tab edits the L2 YAML; the `L2 Flow Tracing` "
        "dashboard reads from L2-declared topology at runtime."
    ),
    "rail": (
        "**Rail** is one money-movement primitive — ACH credit, wire, "
        "cash deposit, internal sweep. Each rail produces one or two "
        "Transaction legs per firing (Two-leg vs Single-leg, picked at "
        "the new-rail subtype gate). Rails are the atom of L2; "
        "TransferTemplates wrap multi-leg patterns, Chains link "
        "parent-child rail firings, LimitSchedules cap per-(role, rail) "
        "flow."
    ),
    "transfer-template": (
        "**Transfer Template** is a multi-leg shared Transfer that "
        "bundles firings of multiple Rails into one event (e.g. a "
        "MerchantSettlement bundles Charge + Settlement legs). Each "
        "firing of a `leg_rails` rail with matching `transfer_key` "
        "metadata posts to the same shared Transfer. L1 Conservation "
        "flags the Transfer if its legs don't sum to `expected_net`."
    ),
    "chain": (
        "**Chain** is a firing rule: one parent rail/template + a list "
        "of candidate child rails/templates. Singleton-children = "
        "required (the child always fires); multi-children = XOR "
        "(exactly one fires per parent invocation). Children link back "
        "via `transfer_parent_id` on the child's leg."
    ),
    "limit-schedule": (
        "**LimitSchedule** is a daily cap on per-direction flow per "
        "(parent_role, rail, direction). Time-invariant in v1. L1 "
        "Limit Breach evaluates per child individually (cap is "
        "per-child, not summed across siblings)."
    ),
    "hook": (
        "**ETL Hook** is your shell command that reads your upstream "
        "data + writes rows to `<prefix>_transactions` + "
        "`<prefix>_daily_balances`. Studio's Refresh Data button wipes "
        "the two tables, invokes the hook, then refreshes matviews. "
        "Set `cfg.etl_hook` in the YAML or env."
    ),
    "matview": (
        "**Matview** = materialized view. Pre-computed query result "
        "stored on disk; refreshed after each ETL load. The L1 "
        "invariant violations + Investigation rolling anomaly + "
        "money-trail edges all live in matviews. Matviews don't "
        "auto-refresh — every ETL load runs `refresh_matviews_sql`."
    ),
    "slice": (
        "**Slice** is one L2 entity (a rail, a template, or a chain "
        "parent) plus the runtime rows that match. The ETL Probe page "
        "shows L2-declared expectations side-by-side with the observed "
        "rows for one slice."
    ),
    "singleton": (
        "**Singleton** account = exactly one instance exists in the "
        "institution (e.g. one GL control account per role). "
        "AccountTemplate, in contrast, materializes N instances at "
        "runtime (one customer DDA per customer)."
    ),
    "predicate": (
        "**Predicate** = one column-level expectation BT.5's contract "
        "derivation produces from your L2. E.g. for rail `ach_credit`: "
        "`account_role IN {CustomerLedger, ExtCounterparty}` + "
        "`amount_direction = Credit` + `metadata.trace_id NOT NULL`. "
        "The Probe page evaluates predicates against observed rows + "
        "shows per-cell ✓/✗."
    ),
}


# -- Render helpers -----------------------------------------------------------


def render_side_panel_drawer_container() -> str:
    """Single drawer chrome rendered once per page (right-edge, hidden
    by default). hx-target for trigger fragments; CSS handles the
    slide-in transition + the click-outside dismiss.

    Place at the bottom of every Studio page's `<body>` (after the
    main content) so its `position: fixed` doesn't reflow anything
    above it.
    """
    # Tailwind utilities for the drawer chrome:
    # - fixed right-0 top-0 h-screen w-full sm:w-1/3 → full-height
    #   slide-out from right edge; ~33% width on desktop, full on mobile
    # - translate-x-full transition-transform → off-screen by default
    # - bg-white border-l shadow-lg → visible card
    # - z-50 → above the rest
    # - The hidden overlay (also fixed inset-0) catches click-outside
    return """
<div id="side-panel-overlay" class="fixed inset-0 bg-black/30 z-40 hidden" data-side-panel-overlay></div>
<aside id="side-panel" role="complementary" aria-label="Help"
       class="fixed right-0 top-0 h-screen w-full sm:w-1/3 bg-white border-l border-surface-border shadow-lg z-50 translate-x-full transition-transform duration-200 overflow-y-auto"
       data-side-panel
       aria-hidden="true">
  <header class="flex items-center justify-between px-4 py-2 border-b border-surface-border">
    <h2 class="text-sm font-semibold m-0" id="side-panel-title">Help</h2>
    <button type="button" data-side-panel-close
            class="text-xl text-secondary-fg hover:text-primary-fg leading-none"
            aria-label="Close help panel">&times;</button>
  </header>
  <div id="side-panel-body" class="px-4 py-3 text-sm">
    <p class="text-secondary-fg italic">Loading…</p>
  </div>
</aside>
<script>
(function() {
  const panel = document.getElementById('side-panel');
  const overlay = document.getElementById('side-panel-overlay');
  if (!panel) return;
  let lastTrigger = null;
  function open(trigger) {
    lastTrigger = trigger;
    panel.classList.remove('translate-x-full');
    panel.setAttribute('aria-hidden', 'false');
    overlay.classList.remove('hidden');
    // Focus the close button so Escape works immediately + the
    // focus trap starts inside the drawer.
    setTimeout(() => {
      const close = panel.querySelector('[data-side-panel-close]');
      if (close) close.focus();
    }, 50);
  }
  function close() {
    panel.classList.add('translate-x-full');
    panel.setAttribute('aria-hidden', 'true');
    overlay.classList.add('hidden');
    if (lastTrigger && document.body.contains(lastTrigger)) {
      lastTrigger.focus();
    }
    lastTrigger = null;
  }
  // Triggers: any element with [data-side-panel-trigger] is treated
  // as an opener. The htmx swap fires before we open; we listen to
  // afterSwap to flip the panel visible.
  document.addEventListener('click', function(evt) {
    const trigger = evt.target.closest('[data-side-panel-trigger]');
    if (trigger) {
      open(trigger);
      return;
    }
    if (evt.target === overlay) {
      close();
      return;
    }
    const closer = evt.target.closest('[data-side-panel-close]');
    if (closer) {
      close();
    }
  });
  document.addEventListener('keydown', function(evt) {
    if (evt.key === 'Escape' && !panel.classList.contains('translate-x-full')) {
      close();
    }
  });
  // CY.3 — expose a programmatic open hook so callers (e.g. the
  // CY.6 ctxmenu entry) can slide the drawer in without simulating
  // a click on a `[data-side-panel-trigger]` element. Optional
  // argument is the element to restore focus to on close.
  window.__sidePanelOpen = function(trigger) {
    open(trigger || null);
  };
})();
</script>
"""


def render_side_panel_trigger(
    target_url: str,
    *,
    label: str = "?",
    aria_label: str = "Open help",
    extra_classes: str = "",
) -> str:
    """Inline ``[?]`` button or top-nav ``[?]`` that triggers the side
    panel. Uses ``hx-get`` to fetch the fragment into
    ``#side-panel-body``; ``data-side-panel-trigger`` tells the panel JS
    to slide the drawer open on click.

    Pass ``label="?"`` for the inline form, ``label="Help"`` for the
    top-nav full-text form.
    """
    base = (
        "inline-flex items-center justify-center "
        "text-accent hover:underline cursor-pointer "
        "select-none"
    )
    cls = f"{base} {extra_classes}".strip()
    return (
        f'<button type="button" '
        f'class="{cls}" '
        f'data-side-panel-trigger '
        f'hx-get="{escape(target_url)}" '
        f'hx-target="#side-panel-body" '
        f'hx-swap="innerHTML" '
        f'aria-label="{escape(aria_label)}">{escape(label)}</button>'
    )


# -- Route handlers (fragments returned via hx-get) ---------------------------


async def _glossary_full(_request: Request) -> HTMLResponse:
    """Return the full glossary as a single fragment for the top-nav
    ``[?]`` button. Terms render as a definition list, sorted
    alphabetically by display name."""
    items: list[str] = []
    for key in sorted(GLOSSARY.keys()):
        display = key.replace("-", " ").title()
        items.append(
            f'<dt class="font-semibold text-primary-fg mt-3 first:mt-0">'
            f'{escape(display)}</dt>'
            f'<dd class="text-sm text-primary-fg mt-1 ml-0">'
            f'{_markdown_render(GLOSSARY[key])}</dd>'
        )
    return HTMLResponse(
        '<dl class="m-0">' + "".join(items) + '</dl>'
    )


async def _glossary_term(request: Request) -> HTMLResponse:
    """Return a single term's definition. Path param ``term`` MUST
    match a GLOSSARY key (lowercase slug). Unknown term → 404."""
    term = str(request.path_params.get("term", "")).lower()
    if term not in GLOSSARY:
        return HTMLResponse(
            f'<p class="text-warning">'
            f'No glossary entry for <code>{escape(term)}</code>. '
            f'Open the full glossary via the top-nav <strong>Help</strong> '
            f'button.</p>',
            status_code=404,
        )
    display = term.replace("-", " ").title()
    return HTMLResponse(
        f'<h3 class="text-base font-semibold m-0 mb-2">{escape(display)}</h3>'
        f'<div class="text-sm text-primary-fg">'
        f'{_markdown_render(GLOSSARY[term])}'
        f'</div>'
    )


def _markdown_render(text: str) -> str:
    """Tiny markdown → HTML for the glossary entries. Currently handles
    `**bold**`, `*italic*`, `` `code` ``, and §-prefixed cross-refs.
    Heavyweight markdown lib lazy-loaded only on first call.
    """
    import markdown as _md  # noqa: PLC0415 — lazy
    escaped = escape(text)
    # Re-inject the markdown syntax tokens after escape (escape
    # converts `*` etc to literal, but markdown's parser handles them
    # before HTML escape — we want markdown semantics, not HTML).
    # Workaround: escape only the content, run markdown on the
    # original; markdown's html-escape internal logic handles `&` /
    # `<` / `>` correctly.
    del escaped  # not used — kept above as documentation of the
                 # escape-then-markdown anti-pattern
    rendered = _md.markdown(text, extensions=["fenced_code"])
    if (
        rendered.startswith("<p>")
        and rendered.endswith("</p>")
        and rendered.count("<p>") == 1
    ):
        return rendered[len("<p>"):-len("</p>")]
    return rendered


def side_panel_routes(
    cache: L2InstanceCache | None = None,
) -> list[Route]:
    """Side-panel HTML fragment routes. Mount under ``/studio/`` so
    they don't collide with Dashboards / L2 Editor / ETL Support
    surfaces.

    BTa.5 — when ``cache`` is supplied, also mounts the chain-arrow
    fragment route that resolves chain parents/children from the
    in-memory L2 instance. Unit tests that don't need the chain
    arrow can omit the cache.
    """
    routes: list[Route] = [
        Route(
            "/studio/side-panel/glossary",
            _glossary_full, methods=["GET"],
        ),
        Route(
            "/studio/side-panel/glossary/{term}",
            _glossary_term, methods=["GET"],
        ),
    ]
    if cache is not None:
        routes.append(
            Route(
                "/studio/side-panel/chain/{parent}",
                _chain_arrow_route_factory(cache),
                methods=["GET"],
            ),
        )
    return routes


def _chain_arrow_route_factory(cache: L2InstanceCache):  # noqa: ANN202
    """Closure: builds the chain-arrow route handler with the cache
    in scope so the handler can resolve parent → children from the
    live L2 instance.
    """
    async def _chain_arrow(request: Request) -> HTMLResponse:
        """Render a small parent → child diagram for one chain parent.

        Each chain in the L2 has one parent + N children; render the
        parent at the top, an arrow down, then the children as a
        list. Singleton vs XOR sibling distinction surfaces as a
        per-child label. Unknown parent → 404 + pointer to /diagram.
        """
        parent = str(request.path_params.get("parent", ""))
        instance = cache.get()
        # Resolve every chain whose parent matches; one parent may
        # have multiple chains registered.
        matches = [c for c in instance.chains if str(c.parent) == parent]
        if not matches:
            return HTMLResponse(
                f'<p class="text-warning">'
                f'No chain found with parent <code>{escape(parent)}</code>. '
                f'Open <a class="text-accent hover:underline" '
                f'href="/diagram">the diagram</a> to browse the L2 topology.'
                f'</p>',
                status_code=404,
            )
        # Group children by their containing chain so the operator
        # sees the XOR sibling structure (one chain = one set of XOR
        # siblings; a singleton chain has 1 child).
        chain_blocks: list[str] = []
        for chain in matches:
            children = list(chain.children)
            is_singleton = len(children) == 1
            kind_label = (
                "Singleton (required child)"
                if is_singleton
                else f"XOR ({len(children)} candidate children)"
            )
            child_items = "".join(
                f'<li class="font-mono text-sm py-0.5">{escape(str(c))}</li>'
                for c in children
            )
            chain_blocks.append(
                '<div class="mb-4 last:mb-0">'
                f'<p class="text-xs text-secondary-fg m-0 mb-1">{escape(kind_label)}</p>'
                f'<ul class="list-none m-0 p-0 pl-4 border-l-2 border-accent">'
                f'{child_items}</ul></div>'
            )
        return HTMLResponse(
            '<h3 class="text-base font-semibold m-0 mb-2">Chain · '
            f'<span class="font-mono">{escape(parent)}</span></h3>'
            '<div class="bg-surface-bg rounded-sm p-3 mb-3">'
            f'<p class="font-mono text-sm m-0 mb-1">{escape(parent)}</p>'
            '<p class="text-accent text-xl m-0 leading-none" aria-hidden="true">↓</p>'
            '</div>'
            + "".join(chain_blocks)
            + '<p class="text-xs text-secondary-fg mt-3 m-0">'
            'Open the <a class="text-accent hover:underline" '
            f'href="/diagram?focus=chain__{escape(parent)}">'
            'full diagram</a> for the wider topology view.</p>'
        )

    return _chain_arrow


# -- CY.5 metadata-popup side-panel renderer + route -------------------------


# Exact copy operator-locked at PLAN.md CY.5: the empty-state fragment
# carries NO toolbar (no Copy, no expand-all, no collapse-all) — the
# panel body is just the one italic paragraph. Re-used by both the
# top-level "metadata is empty" branch (missing / null / {} / [])
# and the route handler's null-coalesce.
_EMPTY_METADATA_FRAGMENT = (
    '<p class="text-secondary-fg italic">No metadata for this row.</p>'
)


def _render_json_node(
    key: str | int, value: Any, *, depth: int,
) -> str:
    """Recursive worker for the metadata tree renderer.

    Object + array values render as ``<details data-json-node>`` with a
    summary line ("key: { N fields }" / "key: [ N items ]") and a
    nested column for the children. Primitive leaves render as
    ``<span data-json-leaf>{json.dumps(value)}</span>`` — JSON-literal
    notation so strings carry quotes, ``true`` / ``null`` / ``42`` stay
    bare. Per PLAN.md CY.5 operator lock 10: raw keys (no friendlier
    labels).

    ``<details open>`` for depth ≤ 2; closed for deeper levels. The
    operator flagged "awful nested JSON" — keep the top of the tree
    visible by default, fold the deep branches.
    """
    # The "key" rendering shape — top-level dict keys come in as ``str``
    # (already a JSON key), list indices as ``int``. Wrap the str in
    # JSON quotes (matches the user's mental model of "this is a JSON
    # key"); render the int as bare ``[N]`` style.
    if isinstance(key, int):
        key_label = f"[{key}]"
    else:
        key_label = json.dumps(key)
    open_attr = " open" if depth <= 2 else ""

    if isinstance(value, dict):
        # Narrow Any → dict[Any, Any] for pyright strict; ``Any`` values
        # are by-design (JSON is heterogeneous), so we cast explicitly
        # to silence ``reportUnknown*``.
        dict_value = cast(dict[Any, Any], value)
        n = len(dict_value)
        summary = (
            f'<summary class="cursor-pointer">'
            f'<span class="text-secondary-fg">{escape(key_label)}</span>'
            f'<span class="text-secondary-fg">: '
            f'{{ {n} field{"s" if n != 1 else ""} }}</span>'
            f'</summary>'
        )
        children = "".join(
            _render_json_node(str(k), v, depth=depth + 1)
            for k, v in dict_value.items()
        )
        return (
            f'<details data-json-node{open_attr}>'
            f'{summary}'
            f'<div class="pl-4 border-l border-surface-border">'
            f'{children}'
            f'</div>'
            f'</details>'
        )
    if isinstance(value, list):
        list_value = cast(list[Any], value)
        n = len(list_value)
        summary = (
            f'<summary class="cursor-pointer">'
            f'<span class="text-secondary-fg">{escape(key_label)}</span>'
            f'<span class="text-secondary-fg">: '
            f'[ {n} item{"s" if n != 1 else ""} ]</span>'
            f'</summary>'
        )
        children = "".join(
            _render_json_node(idx, v, depth=depth + 1)
            for idx, v in enumerate(list_value)
        )
        return (
            f'<details data-json-node{open_attr}>'
            f'{summary}'
            f'<div class="pl-4 border-l border-surface-border">'
            f'{children}'
            f'</div>'
            f'</details>'
        )
    # Primitive leaf — JSON literal notation. ``default=str`` so any
    # exotic type the loader smuggled through (e.g. ``Decimal``) still
    # serializes; the IS-JSON DB constraint upstream limits the
    # value universe to plain JSON, but defense-in-depth.
    literal = json.dumps(value, default=str)
    return (
        f'<div class="py-0.5">'
        f'<span class="text-secondary-fg">{escape(key_label)}</span>'
        f'<span class="text-secondary-fg">: </span>'
        f'<span data-json-leaf>{escape(literal)}</span>'
        f'</div>'
    )


def render_metadata_panel(
    metadata: Any, *, transaction_id: str,
) -> str:
    """Render the CY.5 row-metadata side-panel body.

    The structure (per PLAN.md CY.5 operator lock):

    - header with the transaction id + Copy / Expand all / Collapse all
      buttons (the JS hooks behind ``[data-metadata-copy]`` /
      ``[data-metadata-expand-all]`` / ``[data-metadata-collapse-all]``);
    - a hidden ``<textarea data-metadata-raw>`` carrying pretty-printed
      JSON the Copy button reads;
    - a ``<div class="metadata-tree">`` holding the recursive
      ``<details data-json-node>`` + ``<span data-json-leaf>`` tree.

    Empty / null / ``{}`` / ``[]`` metadata short-circuits to the
    operator-locked empty-state fragment (no toolbar — see
    ``_EMPTY_METADATA_FRAGMENT``).
    """
    # Empty-state branch — matches None, empty dict, empty list. An
    # empty *string* doesn't show up here (the route layer's
    # ``json.loads`` would have failed first); guard anyway.
    if metadata is None or metadata == {} or metadata == [] or metadata == "":
        return _EMPTY_METADATA_FRAGMENT

    # Toolbar buttons — small, accent-colored, keyboard-focusable.
    # Tailwind utility soup mirrors the rest of the side panel's
    # button vocabulary.
    btn_class = (
        "text-xs px-2 py-0.5 rounded-sm border border-surface-border "
        "text-secondary-fg hover:text-primary-fg hover:bg-surface-bg "
        "cursor-pointer"
    )
    copy_btn = (
        f'<button type="button" data-metadata-copy '
        f'class="{btn_class}" aria-label="Copy JSON">Copy</button>'
    )
    expand_btn = (
        f'<button type="button" data-metadata-expand-all '
        f'class="{btn_class}" aria-label="Expand all">Expand all</button>'
    )
    collapse_btn = (
        f'<button type="button" data-metadata-collapse-all '
        f'class="{btn_class}" aria-label="Collapse all">Collapse all</button>'
    )

    raw = json.dumps(metadata, indent=2, default=str)

    # Top-level rendering — wrap a non-dict / non-list primitive in a
    # synthetic "value" key so the tree always has a root. Dicts /
    # lists iterate at depth 0 (their entries render at depth 1, so
    # depth ≤ 2 = top two levels open by default).
    if isinstance(metadata, dict):
        # Same Any-narrowing pattern as ``_render_json_node`` —
        # heterogeneous JSON, ``Any`` is the right value type, but
        # cast explicitly for pyright strict.
        top_dict = cast(dict[Any, Any], metadata)
        body = "".join(
            _render_json_node(str(k), v, depth=1)
            for k, v in top_dict.items()
        )
    elif isinstance(metadata, list):
        top_list = cast(list[Any], metadata)
        body = "".join(
            _render_json_node(idx, v, depth=1)
            for idx, v in enumerate(top_list)
        )
    else:
        body = _render_json_node("value", metadata, depth=1)

    return (
        '<div class="metadata-panel" role="complementary">'
        '<header class="flex items-center justify-between mb-2">'
        f'<h3 class="text-sm font-semibold m-0">'
        f'Row metadata · {escape(str(transaction_id))}'
        f'</h3>'
        '<div class="flex gap-1">'
        f'{copy_btn}{expand_btn}{collapse_btn}'
        '</div>'
        '</header>'
        f'<textarea data-metadata-raw hidden aria-hidden="true">'
        f'{escape(raw)}'
        f'</textarea>'
        '<div class="metadata-tree font-mono text-sm">'
        f'{body}'
        '</div>'
        '</div>'
    )


def _sheet_has_metadata_popup_table(sheet: "Sheet") -> bool:
    """Return True iff ``sheet`` carries a ``Table`` visual with
    ``metadata_popup=True``. The route uses this as the 404 gate per
    PLAN.md CY.5 — accidental wiring elsewhere (a metadata=... URL
    aimed at a non-metadata-popup sheet) surfaces as a 404, not a
    silent 200.
    """
    for v in sheet.visuals:
        if type(v).__name__ != "Table":
            continue
        if getattr(v, "metadata_popup", False):
            return True
    return False


def metadata_panel_route_factory(
    dashboards: Mapping[str, Any],
    all_sheets: Mapping[str, Mapping[str, "Sheet"]],
) -> Callable[[Request], Awaitable[Response]]:
    """Build the CY.5 ``GET /dashboards/.../rows/metadata`` handler with
    the ``dashboards`` + ``all_sheets`` mappings in closure scope.

    Stateless by design: the metadata JSON travels as a query param
    sourced from the already-rendered row payload (CY.4). No per-click
    DB round-trip; the handler validates routing + parses the JSON +
    delegates to ``render_metadata_panel``.

    404 cases (match the dropdown_options shape used elsewhere):

    - unknown ``dashboard_id``
    - unknown ``sheet_id`` for that dashboard
    - known sheet whose resolved ``Table`` visual has
      ``metadata_popup=False`` (or no ``Table`` visual at all)

    500 case (per PLAN.md CY.5 operator lock 8):

    - the metadata query param fails ``json.loads`` — defense in depth
      behind the DB-side IS JSON constraint; no silent fallback.
    """
    async def metadata_panel_route(request: Request) -> Response:
        dash_id = str(request.path_params["dashboard_id"])
        if dash_id not in dashboards:
            raise HTTPException(status_code=404)
        sheet_id = str(request.path_params["sheet_id"])
        sheets_for_dash = all_sheets.get(dash_id, {})
        sheet = sheets_for_dash.get(sheet_id)
        if sheet is None:
            raise HTTPException(status_code=404)
        if not _sheet_has_metadata_popup_table(sheet):
            raise HTTPException(status_code=404)
        raw_metadata = request.query_params.get("metadata")
        transaction_id = str(request.query_params.get("transaction_id") or "")
        # Missing / empty → empty-state fragment.
        if not raw_metadata:
            return HTMLResponse(_EMPTY_METADATA_FRAGMENT)
        try:
            parsed = json.loads(raw_metadata)
        except json.JSONDecodeError as exc:
            # Per operator lock 8 — defense in depth behind the DB IS
            # JSON constraint. No silent fallback. Surface the
            # decoder's message so the operator sees WHY the upstream
            # row payload is malformed (e.g. unbalanced bracket from
            # a stealth column rename).
            return HTMLResponse(
                f'<p class="text-warning">metadata JSON parse failed: '
                f'{escape(str(exc))}</p>',
                status_code=500,
            )
        return HTMLResponse(
            render_metadata_panel(parsed, transaction_id=transaction_id),
        )

    return metadata_panel_route
