"""Studio side-panel drawer infrastructure (BTa.1).

The right-edge slide-out drawer that hosts:

- glossary definitions for opaque vocabulary (L2, Rail, Hook,
  Matview, LimitSchedule, Chain, Slice, etc.) — opened from the
  top-nav ``[?]`` button OR from per-field inline ``[?]`` triggers
- per-page help text for specific fields (consumed by BX.12-15
  cells via the same drawer chrome)
- chain arrow diagrams for the Probe page (BTa.5)
- entity edit-page help (consumed by BX.13 cells)

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

from html import escape

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route


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


def side_panel_routes() -> list[Route]:
    """Side-panel HTML fragment routes. Mount under ``/studio/`` so
    they don't collide with Dashboards / L2 Editor / ETL Support
    surfaces.
    """
    return [
        Route(
            "/studio/side-panel/glossary",
            _glossary_full, methods=["GET"],
        ),
        Route(
            "/studio/side-panel/glossary/{term}",
            _glossary_term, methods=["GET"],
        ),
    ]
