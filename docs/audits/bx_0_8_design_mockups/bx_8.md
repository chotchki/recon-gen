# BX.8 — Diagram node link-to-edit + inline mini-diagram on edit pages

> **Status:** DRAFT mockup. Output of BX.0.8 for one of the five
> design-needed cells. Operator picks a direction (or hybrids) before
> BX.8 implementation fires (~4-5h after lock per BX.0.7).

## Current state

The L2 topology diagram (`/diagram`, screenshot `diagram_focus.png`) is
a graphviz-rendered SVG with three node-level affordances wired today
in `_studio_assets/diagram.js`:

1. **Left-click** a node → server-side `?focus=<node_id>` re-render
   (graphviz re-lays-out the focused node + 1-hop neighborhood). This
   is the affordance the operator wants to KEEP — they walked back
   from making click jump to edit because click-to-focus is too
   useful to lose.
2. **Right-click** a node → custom in-page context menu with a single
   "Edit this entity" item (`diagram.js::_showNodeContextMenu`,
   wired CF.3.m). Bundles + roles return `null` from
   `_editorUrlForNode` and defer to the native browser menu.
3. **Click empty canvas / Escape** → drop `?focus`, restore full
   picture.

The edit page (`edit_rail.png`, `topnav_edit_account.png`) today is a
single-column form with no spatial context. The operator who arrived
via a right-click "Edit this entity" loses sight of where this rail
sits in the topology the moment they leave the diagram — the back
link reads `← back to Rails` (list page), not `← back to diagram
focused here`.

Two implicit assumptions the current shape codifies:

- The diagram is the discovery surface; edit pages are the
  modification surface. They are linked one-way (right-click jumps
  in; back-link jumps to the list, not the diagram).
- The right-click menu is the canonical entry point to the edit
  page from the diagram. The hover state today does nothing
  beyond the cursor pointer change (`node.style.cursor = "pointer"`).

## Constraints from BX.0/0.7 locks

| Lock | Source | Implication for BX.8 |
|---|---|---|
| **Click stays as focus, NOT jump-to-edit** | BX.0.7 §BX.8 ("operator walked back to: 'link to edit page; the click-to-filter is in tension with this'") | Hover affordance must be VISIBLE but NOT replace the click handler. Right-click context menu (CF.3.m) is the canonical edit entry today; BX.8 adds a SECOND, more-discoverable path without breaking the existing one. |
| **Bundle / role nodes have no unique edit target** | `_editor_url_for_focus_node` (`_studio_routes.py:264`) — operator lock 2026-06-05 | Hover affordance suppressed entirely on those nodes; do NOT show a non-functional pointer. |
| **Edit page back-link points at list, not diagram** | `_studio_editor_routes.py:4391` — `back_link_html = "← back to {kind_label_plural}"` | Mini-diagram on the edit page is the visual "where am I" the back-link can't supply. Self-node highlighted; the rest of the focused subgraph in faded grey so the operator's mental model of "this node, these neighbors" survives the jump. |
| **No browser-based diagram authoring** | BX.0 "Out of scope" | Mini-diagram is read-only. No drag, no click-to-edit-neighbor. (Click-to-jump on the mini-diagram is an open question — see below.) |
| **Browser drivers locate by data-attribute / ARIA / visible text**, not Tailwind class | `feedback_browser_drivers_user_facing_locators` memory | Hover affordance gets a `data-role="diagram-edit-link"` anchor. Self-highlight on mini-diagram gets a `data-role="mini-diagram-self"` marker on the SVG `<g.node>`. |
| **CPA-readable banking terminology** | `project_design_north_stars` | Hover label is "Edit" or "→ Edit rail", not "Open in L2 editor". Mini-diagram tooltip on self-node is plain ("This is what you're editing"), not "Focused entity". |
| **App2/QS parity** | not applicable here — diagram + edit pages are App2-only | No parity concern. Mockup is App2-native. |

## Directions

Five directions, ordered cheapest → most ambitious.

### D1 — Hover-only badge + iframe mini-diagram (minimum-change)

**Thesis.** Reuse what already works. Hover puts a small floating
"Edit" badge in the node corner; the edit page embeds `/diagram?
focus=<node_id>&embed=1` in a small iframe. No new rendering pipeline;
the same focused-graphviz output drives both the full diagram and the
mini.

**Mockup — diagram hover state:**

```
┌────────────────────────────┐
│  ExternalCounterparty      │
│                          [→ Edit] ← hover-only floating badge
│  (rest of the node body)   │
└────────────────────────────┘
                ▲
   default state: badge hidden;
   hover ⇒ badge appears top-right inside the node bbox
   click badge ⇒ /l2_shape/<kind>/<id>/edit
   click anywhere ELSE on node ⇒ existing ?focus= behavior
```

**Mockup — edit page mini-diagram:**

```
Edit rail · CustomerInboundACH (two-leg)
← back to Rails
┌────────────────────────────────────────┐
│ Where this rail sits                   │
│ ┌──────────────────────────────────┐   │
│ │  [iframe /diagram?focus=         │   │  ← ~280px tall
│ │   rail__CustomerInboundACH&      │   │     auto-shrunk
│ │   embed=1&mini=1]                │   │     iframe
│ │                                  │   │
│ │  ExternalCounterparty            │   │
│ │       ▼                          │   │
│ │  [● CustomerInboundACH ●]  ← self│   │
│ │       ▼                          │   │
│ │  CustomerDDA                     │   │
│ └──────────────────────────────────┘   │
│  [Open full diagram →]                 │
└────────────────────────────────────────┘
(edit form continues below)
```

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Low** (1.5-2h) | New `?mini=1` flag suppresses sidebar + zooms; new hover CSS + badge DOM injection in `diagram.js`. |
| Risk | Low | Iframe sandboxing is the well-trodden pattern (BS.3 home iframe). Hover badge is additive — current click + right-click survive untouched. |
| Mental-model fit | Medium | "Diagram-in-a-frame" is correct but slightly hokey — two scrollbars when the focused subgraph exceeds 280px. |
| Accessibility | Medium | Hover-only badge invisible to keyboard nav (need Tab/focus state too). Iframe inherits diagram a11y limitations (graphviz SVG has no semantic node ordering). |
| Cross-renderer parity | N/A | App2-only surface. |

### D2 — Hover-only badge + inline SVG mini-diagram (server-rendered)

**Thesis.** Same hover affordance as D1, but the mini-diagram is
inline SVG rendered server-side (re-using `topology.py` →
`graphviz` → SVG), not iframed. One DOM tree, one CSS scope, one
keyboard-focus order.

**Mockup — diagram hover state:** same as D1.

**Mockup — edit page mini-diagram:**

```
Edit rail · CustomerInboundACH (two-leg)
← back to Rails

┌─ Where this rail sits ───────────[Open full →]─┐
│                                                │
│   ExternalCounterparty                         │
│         │                                      │
│         ▼                                      │
│   ┏━━━━━━━━━━━━━━━━━━━━━┓  ← self, accent      │
│   ┃ CustomerInboundACH  ┃    border + glow     │
│   ┗━━━━━━━━━━━━━━━━━━━━━┛                      │
│         │                                      │
│         ▼                                      │
│   CustomerDDA                                  │
│                                                │
│   data-role="mini-diagram"                     │
│   data-self-id="rail__CustomerInboundACH"      │
└────────────────────────────────────────────────┘
```

**Self highlighting:** add `class="self"` to the `<g.node>`
matching the entity's node-id; CSS rule in `diagram-svg.css`:
`.studio-mini g.node.self > polygon { stroke: var(--accent);
stroke-width: 3px; filter: drop-shadow(0 0 4px var(--accent)); }`.

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Low-medium** (2-3h) | New server-side rendering path (factor out the SVG generation from `/diagram` route so the edit-page handler can call it directly). Self-class injection is a one-line post-process. |
| Risk | Low-medium | Reuses tested graphviz output. Layout for tiny canvases sometimes degrades; need a `size="6,4!"` or `ratio="compress"` graphviz flag in mini mode. |
| Mental-model fit | **High** | Inline SVG reads as "part of the page", not an embedded mini-app. Self-highlight is obvious. |
| Accessibility | Medium-high | Single DOM = single Tab order. Self-node gets `aria-label="This is the rail you're editing"`. Browser-driver gets `data-role="mini-diagram-self"`. |
| Cross-renderer parity | N/A | App2-only. |

### D3 — Hover affordance is a visible "Edit" link in node text (no badge)

**Thesis.** Drop the floating badge idea entirely. On hover, the node
text gets a underlined "→ Edit" appended INLINE — same place,
same baseline, no overlapping elements. Click that text → edit page.
Click anywhere else on the node → existing focus behavior. Mini-diagram
same as D2.

**Mockup — diagram hover state:**

```
┌────────────────────────────┐
│                            │
│  CustomerInboundACH        │   ← default
│                            │
└────────────────────────────┘

┌────────────────────────────┐
│                            │
│  CustomerInboundACH        │   ← hover
│  → Edit                    │      (inline accent text)
└────────────────────────────┘
```

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Medium** (3-4h) | Text injection into a graphviz-rendered SVG node bbox is awkward — either reshape the DOT (graphviz output then re-rendered) or overlay a `<text>` element via JS. Either way, click-zone disambiguation between "edit text" and "node body" is finicky. |
| Risk | Medium-high | Easy to mis-tap (the click target moves on hover); fat-finger lands on the wrong handler. |
| Mental-model fit | Medium | Reads as a hyperlink (CPA-friendly). But hover-revealed-then-tap is a known antipattern on touch devices (no hover state). |
| Accessibility | Low-medium | Same hover-only problem as D1 + the click-target ambiguity. Worse than D1 here. |
| Cross-renderer parity | N/A | App2-only. |

### D4 — Persistent edit pencil icon + inline SVG mini-diagram

**Thesis.** Stop hiding the affordance behind hover. Every editable
node gets a small persistent pencil icon in the top-right corner
(unobtrusive, accent-colored on hover). Bundles + roles have no
icon. Mini-diagram same as D2.

**Mockup — diagram default state (no hover):**

```
┌────────────────────────────┐
│                       [✎]  │  ← always-visible pencil
│  CustomerInboundACH        │     accent-color on hover
│                            │     click → /edit
└────────────────────────────┘
       ▲
       Hover the node body anywhere ELSE → focus behavior.
       Hover the pencil → tooltip "Edit this rail"
```

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Medium** (2.5-3h) | Pencil icon is an inline SVG (small `<g>` injected by `diagram.js` after graphviz). Same click-zone disambiguation as D3 but with a clear visual target. |
| Risk | Low-medium | Always-visible affordance is harder to miss but adds chrome to a diagram the operator may want clean. Possible visual fatigue at L3 layer with ~30 nodes all carrying pencils. |
| Mental-model fit | **High** | Pencil = edit is a universal idiom. CPA-readable as "edit the underlying rail definition". |
| Accessibility | **High** | Persistent affordance = keyboard-discoverable. Pencil gets `<a tabindex="0" aria-label="Edit CustomerInboundACH">`. |
| Cross-renderer parity | N/A | App2-only. |

### D5 — Dedicated "Editor mode" toggle in sidebar + inline SVG mini-diagram

**Thesis.** Decouple the two click semantics by a chrome toggle.
Diagram sidebar gets a new toggle: "Click action: [Focus | Edit]"
(radio pair, persisted to URL `?click=edit`). When `click=edit`,
LEFT-click on a node navigates to `/edit`; right-click still opens
focus. When `click=focus` (default), today's behavior. Hover
shows a small tooltip naming the active click action.

**Mockup — diagram sidebar additions:**

```
┌─ Sidebar ────────────────┐
│ ▼ Focus                  │
│ rail__CustomerInboundACH │
│ [ clear focus ]          │
│                          │
│ ── Click action ──       │
│ (●) Focus on node        │  ← default
│ ( ) Edit entity          │
│                          │
│ Layer                    │
│ ...                      │
└──────────────────────────┘
```

Mini-diagram on edit page: same as D2.

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Medium-high** (4-5h) | New persistent URL state, new sidebar control, JS handler dispatch on click. More code; more test surface. |
| Risk | Medium | Modal UI ("which mode am I in?") is a known footgun. Operator who left it on `edit` last session is confused when click-jumps-to-edit instead of focusing. |
| Mental-model fit | Medium-low | Walks BACK the very design decision the operator made (no click-jump). Hidden in a toggle, but still violates the spirit. |
| Accessibility | High | Toggle is explicit + keyboard-reachable. |
| Cross-renderer parity | N/A | App2-only. |

## Recommendation

**Pick D2** (hover badge + inline SVG mini-diagram). Final call stays
with the operator.

- Comment: I'm good with D2.

Reasoning:

1. **D1 is close** but the iframe is unnecessary complexity for a
   surface we already render server-side. Two scroll containers on a
   small viewport is bad UX; one DOM tree is strictly easier to test
   (e2e driver doesn't have to switch frames).
2. **D2 honors the BX.0.7 lock cleanly** — click still focuses, hover
   adds the affordance, the mini-diagram doesn't add a new click
   surface where the click-vs-edit tension reappears.
3. **D3's inline-text-link** trades real estate inside the node for
   a click-zone disambiguation problem. Punt unless a future
   touch-device requirement forces a no-hover affordance.
4. **D4's persistent pencil** is the second-best option and the one
   I'd recommend if accessibility is a hard requirement. The
   tradeoff: more visual chrome on every node, at every zoom level.
   At L3 (30+ nodes visible at once) the pencils risk becoming
   noise. If the operator wants D4 instead, that's a defensible
   pick — flag the "is the L3 noise-floor too high?" question for
   cold-read v2 (BX.18).
5. **D5 is the wrong shape.** Modal-UI dispatch on click action
   walks back the lock-rule the operator just made. Skip unless
   D1-D4 all fail user testing.

**On the hover-only-vs-keyboard a11y gap:** D2's hover badge ALSO
appears when the node has keyboard focus (`g.node:focus-within .edit-
badge`). That preserves the keyboard-driven flow without the chrome
cost of D4's always-visible pencil. Implementation note: graphviz SVG
nodes don't get `tabindex` by default; `diagram.js` already iterates
`svg.querySelectorAll('g.node')`, add `node.setAttribute('tabindex',
'0')` in the same loop. One-line a11y win.

**Composite key entities** (chain, limit_schedule) the inline
mini-diagram uses the same `_editor_url_for_focus_node` arms that
already exist — and bundles/roles suppress the badge entirely, same
rule the right-click menu uses today. No new "edit URL for diagram
node" code path.

## Open questions

1. **Mini-diagram radius:** 1-hop neighborhood (what `/diagram?focus=`
   shows) vs. only direct parents+children (smaller). 1-hop matches
   the diagram's own focus rendering = no new server-side filter; but
   a rail with 5 reconcilers + 8 chains gets a busy mini. Lean: keep
   1-hop, add `?radius=0` for the future "tightest neighborhood
   only" option.
  - Comment: This get VERY challenging with heavy transfer template flow. I'd keep the exactly same rendering we do today.
2. **Click-on-mini-diagram-node behavior:** open that entity's edit
   page (cross-navigation) vs. no-op (read-only display) vs. navigate
   to the FULL `/diagram?focus=<that-node>`. My pick: navigate to
   the full diagram focused on that node (consistent with "click
   focuses, doesn't edit" on the main diagram). Operator may disagree
   — could argue mini-diagram is a "see neighbors → edit a neighbor"
   surface and clicks should go to edit. Either choice needs an
   explicit lock; default behavior here is a footgun.
  - Comment: I think if we're in the edit section an you click a node, jumping to its edit page makes sense, we just need a clear way back to the main diagram too.
3. **Mini-diagram on composite-key entity pages (chain, limit_schedule):**
   The chain `CustomerInboundACH::NSF,StopPay` (per `url_chain_child.png`)
   has 2 children + 1 parent in scope. Tiny mini-diagram works. The
   limit_schedule composite-key (parent_role::rail::direction) doesn't
   have a clean topology projection — it's a constraint on a rail
   triple, not a node. Question: do those edit pages get a mini-
   diagram pointing at the rail being constrained? Or skip mini-
   diagram entirely on limit_schedule edit?
  - Comment: I'd skip on limit schedule for now.
4. **Edit-page back-link target:** should the back-link change from
   `← back to Rails` (list) to `← back to diagram focused here`
   when the operator arrived via the diagram right-click menu? This is
   a small `?from=diagram` parameter on the edit URL, mirroring the
   existing `?from=triage` BTa.2 pattern. Outside BX.8's stated scope
   but a natural pairing — flag for operator.
  - Comment: I like the switch to back to focused diagram for the solution to 2 above.
5. **Hover badge on mobile / touch (no hover state):** Studio is
   single-user desktop in design. Skip touch support for v1?
  - Comment: Skip touch
6. **Layer-mismatch on mini-diagram:** if the operator's diagram
   sidebar has `?layer=1` (no rails shown), and they jump to an edit
   page for a rail, the mini-diagram nonetheless renders the rail
   (forcing layer=2 internally). Confirms or contradicts the "full
   diagram" they were just looking at. Lean: force a layer that
   shows the self-node; document the asymmetry. Open for operator.
 - Comment: I agree, show more
