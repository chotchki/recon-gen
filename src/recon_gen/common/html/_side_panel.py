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
        "runtime (one customer DDA per customer). Cross-reference: "
        "see **1-to-1** for the math notation used on Studio role "
        "cards + the **1:1 / 1:N** choice the `+ Add Role` modal "
        "exposes."
    ),
    # BX.6/11 — role-cardinality vocabulary. The reframe lifts
    # Accounts + Account templates into one **Roles** organizing
    # concept on the editor surface, distinguished by cardinality:
    # 1:1 = one ledger row (an Account); 1:N = pattern with N
    # runtime instances (an AccountTemplate). CPA-natural framing
    # per `[[project_design_north_stars]]`; math notation is the
    # one we use on the role-card badge + in the modal prose.
    "roles-cardinality": (
        "**Roles — 1:1 vs 1:N.** A *Role* is a name (e.g. "
        "`CashDueFRB`, `CustomerDDA`) that every rail / chain / "
        "limit references. Roles come in two cardinalities:\n\n"
        "- **1:1 — Singleton account** — the role IS the account. "
        "One row in the chart of accounts (e.g. cash, GL control "
        "lines, named subaccounts). Pick this when there's exactly "
        "one ledger row for the role.\n"
        "- **1:N — Templated role** — one declaration; ETL "
        "materializes N runtime rows (one per customer, one per "
        "merchant, etc.). Pick this when the role fans out at "
        "runtime (CustomerDDA, MerchantDDA, ZBA subaccounts).\n\n"
        "Cross-reference: **singleton** (legacy term) + the "
        "`+ Add Role` modal in the Studio editor's Roles section."
    ),
    "1-to-1": (
        "**1:1 — Singleton account.** One declared role → one "
        "ledger row. Example: `CashDueFRB`, `ACHOrigSettlement`, "
        "individual GL control accounts. Lives as an **Account** "
        "in the L2 YAML (`accounts:` list). Cross-reference: "
        "**roles-cardinality** for the side-by-side with 1:N + "
        "**singleton** for the legacy term."
    ),
    "1-to-n": (
        "**1:N — Templated role.** One declared pattern → many "
        "runtime instances at ETL time. Example: `CustomerDDA` "
        "materializes one ledger row per customer; `MerchantDDA` "
        "one per merchant. Lives as an **AccountTemplate** in the "
        "L2 YAML (`account_templates:` list). Cross-reference: "
        "**roles-cardinality** for the side-by-side with 1:1."
    ),
    "predicate": (
        "**Predicate** = one column-level expectation BT.5's contract "
        "derivation produces from your L2. E.g. for rail `ach_credit`: "
        "`account_role IN {CustomerLedger, ExtCounterparty}` + "
        "`amount_direction = Credit` + `metadata.trace_id NOT NULL`. "
        "The Probe page evaluates predicates against observed rows + "
        "shows per-cell ✓/✗."
    ),
    # ---- BX.12 — Rail / Chain / LimitSchedule per-field vocabulary -------
    # Cold-read v1a flagged these as the highest-friction terms a banker
    # hits when editing L2 entities in the Studio. Banker-readable prose
    # (CPA framing, not engineering jargon) per project_design_north_stars.
    "posted-requirements": (
        "**Posted requirements** are the rail-specific fields the L1 "
        "PostedRequirements view requires every posted Transaction to "
        "carry beyond the auto-derived TransferKey + chain-Required "
        "fields. Think of them as the line items your reconciler "
        "*must* be able to fill in before a transaction is considered "
        "complete (e.g., for an ACH credit: trace number, effective "
        "date). One per line. Anything missing surfaces on the L1 "
        "Today's Exceptions table as a Posted-Requirements violation."
    ),
    "bundles-activity": (
        "**Bundles activity** lists the rails or transfer templates "
        "whose Transactions this aggregating rail rolls up into one "
        "sweep / batch posting. Example: a daily ACH-batch sweep rail "
        "would bundle every customer-DDA ACH-debit firing from the day. "
        "L1 Conservation reconciles the sweep amount against the sum of "
        "the bundled activity — if they disagree, the breach surfaces on "
        "the Exceptions table. Applies only when ``aggregating = true``."
    ),
    "cadence": (
        "**Cadence** is the firing schedule for an aggregating rail — "
        "the frequency at which the sweep / batch posts. Common shapes: "
        "``daily-eod`` (one posting at end of day), ``intraday-2h`` "
        "(every two hours during business hours), ``weekly`` (one per "
        "week). Time-invariant in v1 (no per-quarter or per-month "
        "overrides). Required when ``aggregating = true``; ignored "
        "when the rail fires per-Transfer."
    ),
    "origin-overrides": (
        "**Origin overrides** let a Two-leg rail declare a different "
        "Origin class per leg — useful when the rail straddles your "
        "ledger and a counterparty's. Example: an ACH-credit rail has "
        "the customer's side as **ExternalForcePosted** (the bank "
        "receives the wire and must post it) and the internal "
        "settlement leg as **InternalInitiated** (your ops team books "
        "it). Leave blank to use the rail-level Origin for both legs. "
        "Origin drives whether L1 looks for an external-system "
        "reference (trace ID, IMAD) or an internal initiator."
    ),
    "xor": (
        "**XOR** (exclusive-or) means *exactly one* of the candidate "
        "children fires per parent firing. Cold-read framing: \"the "
        "parent firing has multiple ways to satisfy itself, and the "
        "demo / your ETL picks one per cycle.\" Example: a "
        "MerchantSettlement parent may settle as Charge-Settlement OR "
        "Charge-Reversal — never both, never neither. A chain with one "
        "child is **Required** (the child always fires); a chain with "
        "two-or-more children is XOR. L1 Chain Compliance flags any "
        "parent firing that produced ≠1 child."
    ),
    "fan-in": (
        "**Fan-in** is N-to-1 chaining: N parent firings collectively "
        "settle into ONE child firing. Example: 50 individual "
        "ACH-credit Transfers (each one a parent firing) all roll up "
        "into a single daily ACH-batch sweep (the one child firing). "
        "The opposite of the default 1:1 (or XOR 1-of-N) chain. Per "
        "validator C8a, fan-in is only allowed when the child is a "
        "**TransferTemplate** (not a bare Rail) — the template owns "
        "the bundling semantics."
    ),
    "expected-parent-count": (
        "**Expected parent count** (``epc``) is the typical or "
        "contractual number of parent firings that should fan-in to "
        "ONE child. Example: a daily ACH-batch child expects ~200 "
        "parent ACH-credit Transfers; setting ``epc=200`` tells L1 "
        "Limit Breach + Chain Compliance to flag any day where the "
        "fan-in count is materially off (suggests a missing batch or "
        "an unprocessed wave). Leave blank to skip the count check; "
        "the chain still validates as fan-in shape."
    ),
    "direction": (
        "**Direction** picks which side of a flow the LimitSchedule "
        "cap watches. **Outbound** = money leaving the parent role's "
        "children (the classic per-rail send cap — e.g., \"customers "
        "can't wire more than $X/day\"). **Inbound** = money arriving "
        "(the AML / structuring threshold — e.g., \"flag any customer "
        "receiving more than $Y/day in cash deposits\"). The same "
        "(parent_role, rail) pair may carry **both** an Outbound and "
        "an Inbound LimitSchedule; the duplicate-detection key is the "
        "(parent_role, rail, direction) triple."
    ),
}


# -- BX.13 — Surfaces-as content (where a field's value ends up) -------------


# Cold-read v1b P3.5 flagged that operators editing the L2 had no
# visibility into *where* a given field's value would end up — they'd
# type a hex into ``theme.accent`` with no signal that the same value
# would drive the L1 KPI bar AND the QS chart-series default AND the
# Studio top-nav. Same drift concern as BX.12 (single source of truth,
# rendered both as side-panel content + per-field chips); a separate
# dict because the *content* is fundamentally different — these are
# location pointers, not vocabulary definitions. The chip label is
# "where?" not "?" so the operator can tell the two surfaces apart at
# a glance.
#
# Bullet-list HTML on render — every entry's prose is markdown bullets
# (4+ items per `[feedback_terminology_over_churn]` style note).
# Keep entries terse + concrete: name the page/element, not the
# implementation file. CPA-readable framing, not engineering jargon.
SURFACES_AS: dict[str, str] = {
    # BXa-scope: institution-identity fields.
    "institution-name": (
        "**Institution name** appears on:\n\n"
        "- the **audit PDF cover page** (regulator-facing identifier)\n"
        "- the **L1 Dashboard header** + every dashboard's title bar\n"
        "- the **Investigation app** landing prose\n"
        "- the **handbook** intro paragraph (mkdocs substitution)\n"
        "- the **Studio top-nav** title chip\n\n"
        "Falls back to `cfg.deployment_name` when blank; regex-"
        "extracted from the Description below when both are blank."
    ),
    "institution-description": (
        "**Description** appears on:\n\n"
        "- the **handbook** intro / preface page rendered from `docs/handbook/`\n"
        "- the **audit PDF appendix** (\"About this institution\" section)\n"
        "- the **L2 Editor** read card for the Instance singleton\n"
        "- regex-extraction fallback source for **institution_name** when blank\n\n"
        "Free-form markdown; first paragraph carries most weight on "
        "the handbook + PDF render."
    ),
    # Theme — identity scalars.
    "theme-name": (
        "**Theme name** appears on:\n\n"
        "- the **QuickSight Theme resource** in your AWS account (visible in the QS console)\n"
        "- the **audit PDF cover** as the build-stamp identifier\n"
        "- the **Studio L2 Editor** Theme read card\n"
        "- `cfg.prefixed(theme_name)` for the deployed QS resource ID\n\n"
        "Short identifier — letters / digits / dashes only."
    ),
    "theme-version-description": (
        "**Version description** appears on:\n\n"
        "- the **audit PDF cover** under the theme name\n"
        "- the **QS Theme resource** Description metadata\n"
        "- the **Studio Theme read card** as the one-line summary\n\n"
        "One-line summary — operator-facing build-note (\"Q2-2026 "
        "rebrand\")."
    ),
    # Theme — brand colours.
    "theme-accent": (
        "**Accent** appears on:\n\n"
        "- **L1 KPI bars** — the primary measure colour across all four apps\n"
        "- **Chart accent** — default series colour in QS bars / lines / pies\n"
        "- **Sheet titles + section headers** across every dashboard\n"
        "- **Link text + primary buttons** (Studio + dashboards)\n"
        "- **Focus rings** + hover highlights\n"
        "- **PDF brand bar** at the top of the audit cover\n\n"
        "Aim for AA contrast against `primary_bg` — the pair-preview "
        "above shows the actual button rendering."
    ),
    "theme-accent-fg": (
        "**Accent foreground** appears on:\n\n"
        "- **Text on accent backgrounds** — primary buttons + accent KPI bars\n"
        "- **Active-tab text** in the Studio top-nav\n"
        "- **PDF brand-bar text** on the audit cover\n"
        "- **focus-ring text** for keyboard-navigated links\n\n"
        "Usually white (`#ffffff`) on a saturated accent; aim for AA "
        "contrast against `accent` — pair-preview shows the actual "
        "button text."
    ),
    "theme-link-tint": (
        "**Link tint** appears on:\n\n"
        "- **Right-click-drill cell backgrounds** — pale-accent wash on tables that carry a context-menu drill\n"
        "- **Pale hover states** on accent-text rows\n\n"
        "Very pale (10-15% saturation of `accent`); the wash signals "
        "\"this row is clickable\" without competing with the actual "
        "data colour."
    ),
    # Theme — state colours.
    "theme-success": (
        "**Success** appears on:\n\n"
        "- the **drift-zero status indicator** on the L1 Dashboard header\n"
        "- **positive-delta chips** (\"closed clean today\") on Today's Exceptions\n"
        "- **green-band heatmap cells** where invariant holds\n"
        "- the **audit PDF status block** when no violations\n\n"
        "Reserve for unambiguous \"all good\" — never use as a brand "
        "colour."
    ),
    "theme-success-fg": (
        "**Success foreground** appears on:\n\n"
        "- **Text on success backgrounds** — drift-zero status chips\n"
        "- **positive-delta chip text** on Today's Exceptions\n"
        "- **PDF status-block text** when no violations\n\n"
        "Aim for AA contrast against `success` — pair-preview shows "
        "the actual chip text."
    ),
    "theme-danger": (
        "**Danger** appears on:\n\n"
        "- **L1 Exceptions table** breach-row backgrounds\n"
        "- **invariant-violated KPI cards** (red border + value)\n"
        "- the **drift-non-zero status indicator** on the L1 header\n"
        "- the **audit PDF violations block** banner\n"
        "- **error toasts** in the Studio (form validation failures)\n\n"
        "Reserve for genuine errors — never for \"important.\""
    ),
    "theme-danger-fg": (
        "**Danger foreground** appears on:\n\n"
        "- **Text on danger backgrounds** — L1 Exceptions breach rows\n"
        "- **error-toast text** in Studio form validation\n"
        "- **PDF violations-block text** on the audit cover\n\n"
        "Aim for AA contrast against `danger` — pair-preview shows "
        "the actual chip text."
    ),
    "theme-warning": (
        "**Warning** appears on:\n\n"
        "- **L1 Exceptions** rows at the suspect/heads-up tier\n"
        "- **rolling-anomaly z-score** mid-band cells (yellow)\n"
        "- the **Studio L2-Editor unsaved-changes banner**\n"
        "- the **audit PDF caveats appendix** marker\n\n"
        "Between `success` and `danger` — \"look at this but don't "
        "freak out yet.\""
    ),
    "theme-warning-fg": (
        "**Warning foreground** appears on:\n\n"
        "- **Text on warning backgrounds** — yellow heatmap cells\n"
        "- the **Studio unsaved-changes banner** text\n"
        "- **PDF caveats-appendix** marker text\n\n"
        "Usually dark text on a yellow wash; aim for AA contrast "
        "against `warning` — pair-preview shows the actual chip text."
    ),
    # Theme — data-color palette + gradient.
    "theme-data-colors": (
        "**Data colour palette** appears on:\n\n"
        "- **QS chart series** — cycled in order across bars / lines / pie slices\n"
        "- the **L2 Flow Tracing** money-trail link colours\n"
        "- the **Investigation network graph** node-class palette\n"
        "- the **audit PDF chart inserts** when violations have a per-series breakdown\n\n"
        "First entry drives the most-common single-series visual; "
        "later entries fill in as the legend grows. At least one "
        "required."
    ),
    "theme-empty-fill-color": (
        "**Empty fill colour** appears on:\n\n"
        "- **QS chart cells** with no data (gap days, unpopulated buckets)\n"
        "- **heatmap zero-row** background\n"
        "- the **drift-no-data** state indicator\n\n"
        "Distinct from `primary_bg` — operators need to tell \"chart "
        "with empty cell\" from \"chart whose canvas is the page\"."
    ),
    "theme-gradient": (
        "**Gradient** (low / high) appears on:\n\n"
        "- **Heatmap cell fills** — interpolated between the two endpoints by value\n"
        "- **rolling-anomaly z-score** ramp on Investigation\n"
        "- **PDF heatmap inserts** when violations have a value-magnitude axis\n\n"
        "`low` = least-intense (often very pale); `high` = most-"
        "intense (often saturated accent). Same hue family across "
        "the pair reads as a single ramp."
    ),
    # Theme — chart-axis chips.
    "theme-dimension": (
        "**Dimension** appears on:\n\n"
        "- **QS field-well chips** for category axes (\"by Account\", \"by Date\")\n"
        "- **chart x-axis tick** background pills on App2 renders\n\n"
        "Chip background colour; pair with `dimension_fg` for the text."
    ),
    "theme-dimension-fg": (
        "**Dimension foreground** appears on:\n\n"
        "- **Text on dimension chips** in QS field-wells\n"
        "- **Axis-pill text** on App2-rendered category axes\n"
        "- **Chart legend text** for dimension-typed series labels\n\n"
        "Aim for AA contrast against `dimension`."
    ),
    "theme-measure": (
        "**Measure** appears on:\n\n"
        "- **QS field-well chips** for value axes (\"SUM(amount)\", \"COUNT(*)\")\n"
        "- **chart y-axis label** background pills on App2 renders\n\n"
        "Chip background colour; pair with `measure_fg` for the text."
    ),
    "theme-measure-fg": (
        "**Measure foreground** appears on:\n\n"
        "- **Text on measure chips** in QS field-wells\n"
        "- **Measure-pill text** on App2-rendered value axes\n"
        "- **KPI-card measure-label text** when not on the accent bar\n\n"
        "Aim for AA contrast against `measure`."
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


async def _surfaces_as_entry(request: Request) -> HTMLResponse:
    """BX.13 — return the surfaces-as pointer block for one anchor.

    Path param ``anchor`` MUST match a ``SURFACES_AS`` key (lowercase
    slug; same convention as glossary). Unknown anchor → 404 with a
    pointer back to the editor surface so the operator isn't stranded.
    """
    anchor = str(request.path_params.get("anchor", "")).lower()
    if anchor not in SURFACES_AS:
        return HTMLResponse(
            f'<p class="text-warning">'
            f'No surfaces-as entry for <code>{escape(anchor)}</code>. '
            f'This pointer was wired without a matching SURFACES_AS key — '
            f'check <code>common/html/_side_panel.py</code>.</p>',
            status_code=404,
        )
    display = anchor.replace("-", " ").title()
    return HTMLResponse(
        f'<h3 class="text-base font-semibold m-0 mb-2">'
        f'Where does this surface?'
        f'</h3>'
        f'<p class="text-xs text-secondary-fg m-0 mb-2">'
        f'<strong>{escape(display)}</strong>'
        f'</p>'
        f'<div class="text-sm text-primary-fg">'
        f'{_markdown_render(SURFACES_AS[anchor])}'
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
        # BX.13 — per-field surfaces-as pointer entries.
        Route(
            "/studio/side-panel/surfaces-as/{anchor}",
            _surfaces_as_entry, methods=["GET"],
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
        key_label = json.dumps(key, separators=(",", ":"))
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
    literal = json.dumps(value, default=str, separators=(",", ":"))
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
    # CY.6 — aria-live wrapper announces the Copy button's flashed
    # "Copied!" label to assistive tech. The polite live region sits
    # next to the button so the SR reads it after the focus action.
    copy_btn = (
        f'<button type="button" data-metadata-copy '
        f'class="{btn_class}" aria-label="Copy JSON">Copy</button>'
        f'<span data-metadata-copy-live class="sr-only" '
        f'aria-live="polite"></span>'
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
