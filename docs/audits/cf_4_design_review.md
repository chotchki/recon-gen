# CF.4 — Editor entity-list search/sort/filter — Design Review

Date: 2026-06-05. Scope: design review only, no implementation.

## 1. Goals & non-goals

**Goals.**
- Make the editor home (`/`) usable on the reviewer's L2 (100+ rails, 30+ templates) — today every entity loads in one fragment per kind via `hx-get="/l2_shape/<kind>/?embed=1"`, producing a single ~40,000px wall.
- Server-side search/sort/pagination per kind (operator lock: client-side JS filter is insufficient at this scale).
- Universal collapse-by-default per card (operator lock).
- Match the URL contract of the existing `_tree_fetcher.py` / Table-visual pager (`sort_column=<name>:<asc|desc>`, `page_offset`, `page_size`).
- Land the absorbed Studio Med followups:
  - (a) per-card Edit/Delete/+Add → ghost/outline buttons, Delete = danger.
  - (b) Editor home `<h1>` + purpose blurb + primary action above the accordion.
  - (c) Value-column widening + key lists as wrapping chips so underscored identifiers stop wrapping mid-token.

**Non-goals.**
- The cross-app shared list-toolbar primitive (`ListSurface` dataclass) — that's Phase CG. CF.4 ships its own typed toolbar contract (see §4) and CG generalizes it later when the second consumer (l2_shape browser) appears. Per `[no_future_cleanup_deferrals]`, CF.4 ships a real typed primitive at the location CG.0 will pick — not a stub.
- Singleton kinds (`theme`, `persona`, `instance`) — they have no list view; skipped per current `SINGLETON_KINDS` branch.
- Diagram surface — CF.3.l already promoted it to its own top-level surface; CF.4 only touches `/` and `/l2_shape/<kind>/`.
- A11y landmarks / `<main>` / `aria-allowed-role` cleanup beyond what CF.4 already touches — that's Phase CK.
- Color-token work (amber, accent-text) — Phase CJ.

## 2. Locks (already decided) — coherence check

Restated from PLAN.md line 370, verified against current state:

| Lock | Status | Notes |
|---|---|---|
| Server-side pagination/search REQUIRED | Coherent | URL contract already exists in `_tree_fetcher.py:111-141` (`_page_int`, `_parse_sort`, `_paginate_table_sql`). Reusing it gives free parity with the renderer pager. |
| Universal collapse-by-default per card | Coherent | Today `_render_read_card` is fully expanded with no `<details>` wrap. Need HTMX expand-on-demand so the collapsed row doesn't render the heavy `<dl>` body up-front (cheaper than rendering 100 hidden `<details>` and toggling). |
| Match pagination patterns from existing screens | Coherent | The Table-visual pager is the canonical pattern; reuse the URL keys verbatim. |
| (a) Edit/Delete/+Add as ghost/outline + danger | Coherent BUT depends on Phase CI (`Button`/`Badge` primitives, CI.3 explicitly notes "Per-card Edit/Delete promotion in CF.4 consumes this primitive once it's available"). Two paths: ship CI.1 first (typed `Button`), or land local Tailwind utility classes now and let CI.3 sweep them. **Recommend the latter** — don't block CF.4 on Phase CI; the per-card buttons are a known migration target. |
| (b) Editor home `<h1>` + purpose blurb + primary action | Coherent | `_render_home_page` already has `<h1>Studio</h1>` in the global header (line 461) but no page-local `<h1>` for the editor surface, no purpose blurb, no primary CTA above the accordion. Drop-in. |
| (c) Value-column widening + key-list-as-chips | Coherent | Today's `dl` grid is `grid-cols-[max-content_minmax(0,1fr)]` — key column gets `max-content`, value gets the rest. The wrap problem is that `break-words` + `break-all` (line 1878 `h3_base`, line 1843 `dd_cls`) splits underscore identifiers mid-token. Fix: render values that are lists-of-identifiers as a flex-wrap of `<span class="chip">` pills with `break-keep`; widen by trading max-content → min-content on the key column. |

**No incoherence found.** The locks are mutually consistent and the URL contract already exists.

## 3. Open design questions

### Q1: URL shape

- **Question:** What query keys does CF.4 use per kind?
- **Options:**
  - (A) Reuse the `_tree_fetcher.py` keys verbatim: `?q=<term>&sort_column=<name>:<asc|desc>&page_offset=<N>&page_size=<M>` per kind, namespaced by URL path (since each kind has its own `/l2_shape/<kind>/`).
  - (B) Per-kind URL namespacing: `?rail_q=…&rail_page=…&template_q=…` so the home page can carry all sections' state in one URL.
  - (C) New keys (`?page=1&sort=name:asc`) — clean but breaks pager-renderer parity.
- **Recommendation:** **(A)** for the dedicated per-kind page (`/l2_shape/<kind>/`), **(B)** for the home page embed fragments. Each section's `hx-get="/l2_shape/<kind>/?embed=1&..."` carries its own scoped params; the section URL stays its own state machine. The browser URL on `/` carries the unioned set with kind-prefixed keys. Reasoning: one URL params struct per section is impossible because htmx fragment URLs are independent; carrying all-kinds state at the page URL means the bookmark survives reload. The `_tree_fetcher` URL shape is the matching point.

- Comment: I'm good with A

### Q2: Pagination shape — offset/limit or cursor?

- **Question:** Offset/limit (renderer-pattern) or stable cursor (id-based)?
- **Options:**
  - (A) Offset/limit (`page_offset`, `page_size`) — matches the existing pager URL contract.
  - (B) Cursor by entity_id (`?after=<id>&limit=N`) — stable across in-place edits.
- **Recommendation:** **(A)** offset/limit. The whole L2 fits in memory (≤ a few thousand entities per kind); offset pagination is O(N) but N is small. Renderer-parity wins over theoretical cursor benefits. Default `page_size=25`, max 200 (smaller than `_TABLE_PAGE_SIZE_MAX = 10_000` since the per-card render is heavier than a `<tr>`).

- Comment: Agreed A

### Q3: Search scope — per-section or unified?

- **Question:** One global search box that filters all six kinds, or one per kind?
- **Options:**
  - (A) Per-kind search inside each section's `<summary>` toolbar.
  - (B) One global search at the top that filters every section's list simultaneously.
  - (C) Both — global search collapses to per-kind hits.
- **Recommendation:** **(A) per-kind**, because (i) the URL contract already namespaces per-kind (Q1's recommendation), (ii) search semantics differ per kind (rail name vs. chain's composite `parent::children` key vs. account id), (iii) Phase CG explicitly punts the "shared list-toolbar primitive" cross-kind to itself — CF.4 should not pre-generalize. The dedicated `/l2_shape/<kind>/` page reuses the same toolbar.

- Comment: I'm good with A for now

### Q4: Sort axes per kind

- **Question:** What sort orders does each kind expose?
- **Options:**
  - (A) Default (declaration order in YAML) / A-Z / Z-A only.
  - (B) Above + kind-specific axes: rail by subtype (two-leg vs single-leg), template by leg-count, chain by parent.
  - (C) Per-column sort by field — heavyweight; matches the Table visual exactly.
- **Recommendation:** **(B)** — three universal axes (Default / A-Z / Z-A) + per-kind sentinels declared in a `SORT_AXES_BY_KIND: Mapping[EntityKind, tuple[SortAxis, ...]]` typed map. Encodes the per-kind shape in types per `[feedback_invariants_in_types]`. **Not (C)** — per-column sort needs a column header strip that the read-card grid doesn't have, and the Phase CG primitive is the right home for that.

- Comment: I'm okay with b, will need to see how it turns out

### Q5: Collapse-by-default expand strategy

- **Question:** Render the full card collapsed (heavy DOM, hidden), or render summary-only and HTMX-fetch the body on expand?
- **Options:**
  - (A) Server renders summary + full `<dl>` body inside `<details>`; CSS hides until open.
  - (B) Server renders summary + an `hx-get="/l2_shape/<kind>/<id>/card?body=1"` placeholder; first expand fetches the body fragment.
  - (C) Hybrid: render body for top-K (e.g. K=10) and lazy-fetch for the rest.
- **Recommendation:** **(B)** — HTMX expand-on-demand. Matches CG.1's locked dataclass shape (`expand_renderer`). 100 collapsed cards = 100 small `<details>` summaries (1-line each) vs. 100 fully-rendered `<dl>`s; the byte-count delta is 30-50x. The route `/l2_shape/<kind>/<id>/card` already exists (`read_card` handler at line 3878) — extend with a `body_only=1` flag instead of adding a new route

- Comment: agree on B

### Q6: Filter+search auto-expands a collapsed-by-default section?

- **Question:** When a search term matches an entity inside a section the operator left collapsed, does the section auto-open?
- **Options:**
  - (A) Yes — server renders the section's `<details>` with `open` attr when a search/filter is active on it (and only it).
  - (B) No — show the match count in the summary; operator opens explicitly.
- **Recommendation:** **(A)**. A collapsed section that quietly hides its own search hits is a footgun. Server-side: when `kind_q` is set OR `kind_page` ≠ 0, the section renders `<details open>`. Operator unsetting the search returns to the default-collapsed state.

- Comment: agree on a

### Q7: Toolbar placement — per-summary or sticky page-top?

- **Question:** Where do the per-kind search/sort/page controls live?
- **Options:**
  - (A) Inside each `<details>` body, above the cards (the natural HTMX target — refetches replace just the section body).
  - (B) Sticky at the page-top for whichever section the operator focused last.
  - (C) Sticky inside the section body (toolbar pinned to top of section while scrolling within it).
- **Recommendation:** **(A)** for CF.4. Sticky-toolbar is in CG's locks (line 384: "Sticky toolbar at viewport top within the list region — not page-level fixed"). CF.4 ships a non-sticky toolbar in the section body; CG.2 adds sticky on migration. Keeps CF.4 thin.

- Comment: agree on a

### Q8: CF audit followups (a)(b)(c) — in scope or out?

- **(a) Edit/Delete/+Add buttonization.** Cleanly in scope — local Tailwind utilities, CI.3 sweeps later.
  - Comment: that's fine
- **(b) Editor home `<h1>` + purpose blurb + primary action.** Cleanly in scope, ~10 lines in `_render_home_page`. Primary action recommendation: "+ Add" dropdown (six kinds) — but operator may prefer a single "+ New rail" since rail is the highest-volume entity. **Sub-question for operator:** single-CTA vs split-button. Recommend split-button (dropdown of six kinds).
  - Comment: I'd do the add button in each section
- **(c) Value-column widen + key-list-chips.** In scope, but requires identifying which `FieldSpec` types are "lists of identifiers" — touches `_FIELD_SPECS_BY_KIND` (line 718). Recommend extending `FieldSpec` with a `render_as: Literal["text", "chip_list", …]` tag — typed primitive, not ad-hoc per-field. **Sub-question:** ship the tag with CF.4 or extract first to CF.X-infra. Recommend ship with CF.4 (per `[no_future_cleanup_deferrals]`).
  - Comment: agree, typed and ship now

### Q9: Cascade-reload semantics under search

- **Question:** When an edit elsewhere fires `HX-Trigger: l2-cascade-reload`, the section refetches its `hx-get` URL. Does the refetch preserve the search/page state?
- **Options:**
  - (A) Section URL holds the params (`?embed=1&q=foo&page_offset=25`) — HTMX naturally re-issues that URL on cascade-reload.
  - (B) Section URL is bare; client JS reads params off the toolbar inputs and appends.
- **Recommendation:** **(A)**. The `hx-get` URL is the section's source of state truth. When the toolbar inputs change, an HTMX `hx-include` updates the section URL via `hx-push-url=false` (we don't want every keystroke in the browser URL — only on submit/page-change). On cascade-reload, htmx re-fetches the current URL → state preserved automatically. Mirrors the diagram's URL-is-state-truth pattern (CF.3.h, CF.3.m).
  - Comment: A

### Q10: Module location for the toolbar primitive

- **Question:** New `common/html/_components.py` (CF.X-infra deferred this), or extend `common/html/render.py`?
- **Options:**
  - (A) `_components.py` — clean separation, CG.0 will land here anyway.
  - (B) `render.py` — already imports from `_studio_editor_routes`.
- **Recommendation:** **(A)** — create the module in CF.4. Even if just one helper (`render_list_toolbar(kind, count, q, sort, page_offset, page_size)`) and one dataclass (`ListToolbarState`) live there, the module's first occupant matters less than getting the import structure right. CG generalizes the contents; CF.4 owns the address. Per `[no_future_cleanup_deferrals]`.
  - Comment: Agree on new modules

## 4. Phase CG dependency — blocking or hypothetical?

**CG is currently `[ ]` at lines 389-393 — not started.** CG.0 is a REPLAN cell; CG.1 is a typed primitive that hasn't been built. PLAN.md line 391 says explicitly "CG.2 — First consumer: CF.4 editor rail-list. Migrate the existing rail list to the primitive; **Adds the server-side search/paginate from CF.4's lock.**" — i.e. CG.2 expects to *be* CF.4's server-side paginate work, not the other way around.

The CF.4 lock line says "the shared list-toolbar primitive is moved to Phase CG since it's cross-app — CF.4 consumes the primitive once CG ships." This is **inverted with respect to CG.2's text** — CG.2 says CF.4 is its first consumer; the CF.4 lock says CF.4 waits for CG. The honest reading: **CF.4 and CG.2 are the same body of work** (server-side search/sort/page on the editor rail list), structured under CG to capture the typed primitive.

**Recommendation:** Surface this to the operator. Two paths:
- (P1) Mark CF.4 blocked by CG. Land CG.0 → CG.1 → CG.2 as the implementation of CF.4. Sub-cells (a)(b)(c) ship under CF.4 separately (they don't need the primitive).
- (P2) Ship the typed primitive (one `ListToolbarState` dataclass + one helper) inside CF.4 at the address CG will use (`common/html/_components.py`), then CG.0 = REPLAN that confirms the shape, CG.1/CG.2 collapse to "extend the existing primitive + migrate l2_shape browser + Training plant cards."

**Strongly recommend (P2)**. CG without CF.4 has no concrete consumer; the spec-first design "design the primitive before the consumer" risks the typed dataclass not fitting the real call site. Per the operator's `[typed_primitives_gt_ad_hoc]` and `[no_future_cleanup_deferrals]`: the primitive is real, it lives at the CG-chosen address from day one, and the typed contract is exercised by CF.4 immediately. CG then expands the consumer set in CG.3 (templates, chains, limit_schedules) and the cold-read in CG.4.

- Comment: I'd just completely fold CG in to CF as extra tasks and mark the phase as won't do so it archives

## 5. Critical files

| File | Lines (approx) | Invasiveness | Change |
|---|---|---|---|
| `src/recon_gen/common/html/_studio_editor_routes.py` | 3714-3780 (`_render_list_page`); 1825-1939 (`_render_read_card`); 718 (`_FIELD_SPECS_BY_KIND`); 3849-3876 (`list_view` handler); 3878-3889 (`read_card` handler) | HIGH | Add toolbar render, parse search/sort/page query params, paginate the entities tuple before card render, split `_render_read_card` into `summary` + `body` halves, extend `FieldSpec` with `render_as` tag, add `card_body` route for HTMX expand-on-demand. |
| `src/recon_gen/common/html/_studio_routes.py` | 286-413 (`_render_home_page`); 238-246 (`_HOME_SECTIONS`) | MED | Add `<h1>` + purpose blurb + primary CTA above the section accordion. Section `hx-get` URLs preserve toolbar state from the browser URL. The lazy-loaded fragment URL now carries per-kind query params. |
| `src/recon_gen/common/html/_components.py` (new) | n/a (new module) | NEW | `ListToolbarState` frozen dataclass (kind, q, sort_axis, page_offset, page_size, total_count, page_size_default=25, page_size_max=200). `render_list_toolbar(state)` helper. `SortAxis` Literal + `SORT_AXES_BY_KIND` typed map. Becomes CG.1's home. |
| `src/recon_gen/common/html/_tree_fetcher.py` | 89-173 (`_page_int`, `_parse_sort`, `_paginate_table_sql`) | LOW | No edits — reuse the URL-key contract verbatim. Optionally extract `_page_int` / `_parse_sort` to `_components.py` if the editor route needs them at the Python layer (not a SQL layer). |
| `tests/unit/test_studio_editor_routes.py` + `tests/unit/test_studio_home_route.py` | various | MED | New tests pin: paginated `<details>` renders the right 25; search filter drops non-matches; cascade-reload preserves search state; expand-on-demand returns the body fragment; per-kind toolbar URL params; `<h1>` + primary CTA present on `/`. |

## 6. Recommended phasing (sub-cells, shippable order)

Each sub-cell is one commit. Operator can dogfood between.

**CF.4.a — Typed toolbar primitive + module landing.**
Create `common/html/_components.py` with `ListToolbarState` dataclass, `SortAxis` Literal, `SORT_AXES_BY_KIND`, and `render_list_toolbar()` helper. No consumers yet. Unit-test the dataclass invariants (page_size_max bound, sort_axis literal). This is the typed contract CG inherits.

**CF.4.b — Server-side pagination + sort on `/l2_shape/<kind>/` (non-home).**
`list_view` parses `_page_int(..."page_offset")` + `_parse_sort(...)` + a new `_parse_query("q")`. Slices the entities tuple after sort/filter. `_render_list_page` consumes `render_list_toolbar()`. Default page_size=25. No collapse-by-default yet — just the toolbar + pager. Operator can dogfood server-side paginated `/l2_shape/rail/` immediately.

**CF.4.c — Universal collapse-by-default per card + HTMX expand-on-demand.**
Split `_render_read_card` into `_render_read_card_summary` (entity_id + actions + 1-line key fields) and `_render_read_card_body` (the heavy `<dl>`). Wrap in `<details>` with summary as the trigger. New route `/l2_shape/<kind>/<id>/card/body?embed=1` returns the body fragment. Section auto-opens when search/page params are set (Q6's lock).

**CF.4.d — Home-page section URL state + cascade-reload preservation.**
`_render_home_page` reads per-kind query params off the home URL (`?rail_q=…&template_page=2`) and bakes them into each section's `hx-get`. On cascade-reload the section refetches the same URL → state preserved. URL-push on toolbar submit (`hx-push-url=true` scoped to navigation events, not keystrokes).

**CF.4.e — Editor home `<h1>` + purpose blurb + primary CTA (followup b).**
Drop-in chrome above the accordion. CTA = split-button "+ Add" (six kinds).

**CF.4.f — Per-card Edit/Delete/+Add button vocabulary + Delete danger (followup a).**
Local Tailwind utility classes (ghost-outline button, danger-solid for Delete). Annotate the change site so Phase CI.3 sweeps it. Phase CI's `Button` primitive will replace these classes — call out in a `# CI.3 followup` comment.

**CF.4.g — Value-column widen + key-list-as-chips (followup c).**
Extend `FieldSpec` with `render_as: Literal["text", "chip_list", "yaml_block", "monospace"]`. Update `_FIELD_SPECS_BY_KIND` to tag the right fields. `_render_read_value` branches on the tag. Widen the value column on the `<dl>` grid by tightening the key column (`max-content` → `min-content` with a sensible `min-w-32`).

**CF.4.h — Cold-read v3 confirmation.**
Run the heavy_density_v1 fixture through the same reviewer persona; confirm the rail page-height drops below the audit's 5000px target. Lock the regression test.

This phasing maps cleanly onto CG.0 (= CF.4.a's REPLAN), CG.1 (= CF.4.a's primitive), CG.2 (= CF.4.b + CF.4.c + CF.4.d), CG.3 (post-CF.4: l2_shape browser + Training plant cards), CG.4 (= CF.4.h).

## 7. Risk callouts

1. **Cascade-reload state loss** (mitigated by Q9 / CF.4.d). If the section `hx-get` URL doesn't carry the toolbar params, every save in another section silently resets the operator's search. The fix is the section URL is the state truth (matches CF.3.m's diagram-URL pattern). Test pins this explicitly.

2. **`?focus=` from diagram + editor `?page=`/`?q=` URL collision.** Per-card focus links (post-CF.3.l) point at `/diagram?focus=<node>`, not at the editor list URL. The editor's pagination URL params don't collide with diagram's. BUT: the `_editor_url_for_focus_node` (CF.3.m) returns `/l2_shape/<kind>/<id>/edit` — the edit page, not the list. So an operator landing on `/l2_shape/rail/foo/edit` from a diagram right-click never sees the list pagination URL. **No collision** — but write a test asserting the diagram→editor jump still works after CF.4.

3. **HTMX expand-on-demand cost on small L2s.** sasquatch_pr (7 rails) doesn't need expand-on-demand; the round-trip per expand is heavier than rendering all 7 cards expanded. **Mitigation:** when `len(entities) ≤ 10`, render all bodies inline (no `<details>` wrap). Crossover threshold tunable.

4. **HTML-id slug collision under composite keys** (chain / limit_schedule use `::` separators). `_html_id_slug` (line 3810) replaces with `__`. If page_offset URL parameters get baked into IDs (they shouldn't), watch for collisions. Test pins the slug.

5. **Browser back-button surprise.** With `hx-push-url=true` on the toolbar, browser back will pop search/page state. Operator expectation: back-button goes to the previously-edited entity, not the previous filter. **Mitigation:** scope `hx-push-url` to navigation events (page-change, sort-change), not keystrokes. Search-on-input debounces and only pushes URL on Enter.

6. **Page-size DoS.** A crafted `?page_size=999999` could exhaust memory rendering 999k cards. `_tree_fetcher._TABLE_PAGE_SIZE_MAX=10_000` is the precedent; CF.4 should use a smaller cap (recommend 200 — card render is ~50x heavier than `<tr>`).

7. **Sort tiebreak under server-side pagination.** Two rails with the same name would shuffle across pages. **Mitigation:** tiebreak on entity_id always (matches `_paginate_table_sql`'s trailing `, 1`).

8. **The CG inversion** (§4). Real operator decision: ship CG.2 as CF.4, or keep them separate. Whichever path, surface the inversion in PLAN.md.

9. **Phase CI followup risk.** CF.4.f ships local Tailwind utility classes for Edit/Delete buttons. If CI.3 doesn't land for a while, those classes drift from whatever `Button` primitive CI.1 standardizes. **Mitigation:** put the local classes in `_components.py` next to the toolbar primitive — one address to migrate.

10. **`FieldSpec.render_as` shape stability.** The Literal type ships in CF.4.a; CF.4.g consumes it. If a kind needs a render_as variant nobody anticipated (e.g. YAML block for `role_business_day_offsets`), extending the Literal is a typed change — pyright catches every miss. Good failure mode.
