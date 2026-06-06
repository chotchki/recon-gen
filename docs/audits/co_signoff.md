# CO — Honest L2 dogfood: un-skip the full create_l2 browser gate

**Status:** sign-off — gate green against fuzz seed 12345.
**Branch:** `co-honest-dogfood`.
**Filed:** 2026-06-06.

## What was broken

`tests/e2e/test_studio_dogfood_browser.py::test_browser_full_create_l2_structural_equality` was `@pytest.mark.skip` with the deferral note:

> "BB.2 create-new sub-form misses optional rail / TT fields (metadata_keys, amount_typical_range, transfer_key, completion, description, posted_requirements). AI.14 + AI.15 consolidated as 'BB.2 form completeness + structured pickers' work — defer until post-AM per user 2026-05-25."

Each prior phase had added a cold-read / lock / audit step in front of the work; every audit found new gaps; every gap got deferred again. Circular — see PLAN's Phase CO callout.

## What this phase did

**CO.1 — un-skipped the test.** Deleted the `@pytest.mark.skip` decorator. Ran the test. Treated the failure trace as the work list. No upfront audit.

**CO.2 — fixed each surfaced failure top-down**:

1. **Driver bug**: `StudioBrowserEditorDriver._submit_create_form` clicked `form.create-form button[type=submit]` only. The dogfood walk's wave-5 `_edit` calls hit the EDIT form (class="edit-form"). Broadened the selector to match either.

2. **Wave-5 BB.4 reorder bug**: the helper sent only `leg_rails` / `bundles_activity` order on the PUT body. For reconcilers created via BB.2's inline-JS sub-form (which intentionally surfaces only structural minima), the rest of the optional fields never got backfilled. Replaced the two narrow `_reorder_*_form_data` helpers with `_post_reconciler_backfill_form_data` that reuses `create_form_data` — covers rail-list order AND every thin-default field in one PUT.

3. **AI.10 ordering bug** (the real "honest" issue): multi_select fields render as checkbox grids; browsers submit checkboxes in DOM (alphabetical) order, not check-order. leg_rails / bundles_activity order is semantic (xor_groups reference rails by name; YAML declaration order is canonical), so structural equality broke.

**The fix — Sortable.js draggable chip widget** (per chotchki: hidden field alone is half-honest):

- Vendor Sortable.js 1.15.6 locally (CDN broke Playwright's navigation-wait race).
- Render a draggable "Selected (in order)" chip list above the existing checkbox grid. Each chip = `<li>` with a drag handle (⋮⋮) + value.
- Hidden `<name>__order` input pre-populated with the entity's tuple order.
- JS bootstrap: Sortable hooks `onEnd` to sync `__order`; sibling checkboxes toggle chip add/remove.
- Server-side coercer reads `__order` (comma-separated). When set-equal to the checkbox values, overrides DOM order; mismatched values silently rejected.
- Driver fills the hidden input directly (`_apply_form_data` evaluate path).

**CO.5 — annotated the field-isolation probe**. `tests/unit/test_studio_editor_routes.py:591` PUTs only `name=` on rail rename to prove the cascade behavior. Not a cheat; deliberate partial PUT. Added comment for future-CO.6 lint to whitelist.

## What's NOT in this phase

- **CO.3 (spec_example coverage)**: `spec_example.yaml` uses `leg_rail_xor_groups` which is `kind="multi_select_groups"` (separate from `multi_select`). My CO.2 fix only addressed `multi_select`. Spec_example extension would need the same chip pattern applied to `multi_select_groups` — nested order (groups + order within groups). Deferred to CO follow-up phase if needed.
- **CO.4 (structured offsets form)**: `role_business_day_offsets` is still a YAML textarea on `/l2_shape/instance/`. Dogfood test passes on fuzz 12345 without needing this — fuzz seed handles offsets via the textarea correctly. Spec_example may surface this; deferred.
- **CO.6 (`no-partial-form-PUT` lint)**: deferred until the spec_example extension lands, when we have a clear definition of what "field-isolation probe" looks like.

## Verification

- `test_browser_full_create_l2_structural_equality` runs un-skipped against fuzz seed 12345 — passes (~12s).
- 3 new CO.2 unit tests pin chip-list rendering + order-override coercer + set-mismatch silent reject.
- 3340 unit pass post-merge.
- Cold-read agent v2 confirmed the chip widget is "honest and usable" via Playwright + screenshots. Empty-state hint "Check a box below; drag chips here to reorder." carries first-time discovery.

## Polish backlog (P2/P3 — not blocking)

From cold-read v2:

- No hover state on chips — `cursor-grab` is the only affordance hint; a subtle bg-tint on hover would reinforce "this is draggable."
- `⋮⋮` glyph small + same color as secondary text; low contrast on pale chip background.
- Populated chip list shows no help text — the empty-state hint disappears once you have ≥1 chip. Operator landing on a populated form sees pills + drag affordance implicit. Consider persistent micro-label ("Drag to reorder") or `title=` on each chip.
- Chips don't visually link to their checkbox row — no shared color/border accent. First instinct on "remove this chip" might be to click the chip itself rather than scan down for the checkbox. Optional `×` button on each chip would close the gap.
- Drag animation works (Sortable.js `animation: 150`) but no end-of-drag flash/pulse to confirm "saved to order."

## Files touched

- `src/recon_gen/common/html/_studio_editor_routes.py` — chip-list render + hidden `__order` + Sortable.js script tag + JS bootstrap + server-side `__order` coercer
- `src/recon_gen/common/html/assets/js/sortable.min.js` — vendored (45 KB)
- `src/recon_gen/common/html/assets/output.css` — Tailwind utility rebuild for new classes (`cursor-grab`, `min-h-10`, `border-dashed`)
- `tests/e2e/_drivers/studio_browser_editor.py` — form-class selector + `__order` hidden-input fill
- `tests/e2e/_drivers/studio_editor.py` — `_post_reconciler_backfill_form_data` + `__order` emit in `_encode_spec`
- `tests/e2e/test_studio_dogfood_browser.py` — un-skipped
- `tests/unit/test_studio_editor_routes.py` — 3 new CO.2 unit tests + CO.5 field-isolation comment
- `biome.jsonc` — exclude vendored sortable.min.js
- `PLAN.md` — Phase CO entry
