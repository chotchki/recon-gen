# BF.0 — Editor field completeness spike

*Landed 2026-05-25 as the gate on Phase BF locks L1-L5.*

## Why this doc

Phase BF closes three pre-existing UI gaps that all hit the same set of forms (rail + transfer_template create / edit + the BB.2 create-new reconciler sub-form): the AI.2.e part-2 subtype-requirements banner, AI.14(a) BB.2 sub-form completeness, and AI.14(b) textarea → structured picker migration. Before BF.1+ implementation fires, three things need to be true:

1. **The exact field-by-field gap is named** — not "BB.2 missing some fields" but a per-field table that proves which fields are absent, what shape they take today, and what shape they need post-BF.
2. **The picker domain choices are pinned** — each textarea-to-picker conversion needs an answer to "what's the option universe and where does it come from?" before the field-kind machinery can be extended.
3. **The L1-L5 locks survive contact with the table** — if the table reveals a field whose option universe genuinely can't be enumerated from L2 state, L2 ("free-form textareas anti-pattern for typed-identifier domains") needs a carve-out before BF.4 fires.

This doc carries the three.

## Source-of-truth pointers

- `_FIELD_SPECS_BY_KIND` in `src/recon_gen/common/html/_studio_editor_routes.py:706` — the per-kind `FieldSpec` tuples the main create / edit pages walk. Today: 17 Rail fields, 8 TransferTemplate fields.
- `_render_reconciler_section` in the same file (line ~2135) — the BB.2 create-new sub-form. Currently renders **validator-required minimums only** for both reconciler kinds (TT and aggregating-rail).
- `_render_field` (line 1151) — the per-field render dispatcher; switches on `FieldSpec.kind` (`text` / `select` / `money` / `textarea` / `yaml_block` / `multi_select` / `multi_select_groups` / `chain_children`).

## Rail field migration table

| FieldSpec.name | kind | Required | Main create | BB.2 sub-form today | Proposed post-BF |
|---|---|---|---|---|---|
| name | text | yes | rendered | rendered (as `reconciler_new_name`) | unchanged |
| source_role | multi_select(roles) | yes (two_leg) | rendered (subtype-gated) | rendered (two_leg only, `reconciler_new_source_role`) | unchanged |
| destination_role | multi_select(roles) | yes (two_leg) | rendered (subtype-gated) | rendered (two_leg only) | unchanged |
| leg_role | multi_select(roles) | yes (single_leg) | rendered (subtype-gated) | rendered (single_leg only) | unchanged |
| leg_direction | select(Debit/Credit/Variable) | yes (single_leg) | rendered (subtype-gated) | rendered (single_leg only) | unchanged |
| origin | text | yes (BB.2 wires it required) | rendered | rendered (`reconciler_new_origin`) | unchanged |
| source_origin | text | no | rendered (two_leg) | **MISSING** | **BF.2: render in aggregator-two_leg sub-block** |
| destination_origin | text | no | rendered (two_leg) | **MISSING** | **BF.2: render in aggregator-two_leg sub-block** |
| expected_net | money | no (yes on standalone two_leg) | rendered (two_leg) | rendered (two_leg only — AI.13) | unchanged |
| aggregating | select(true/false) | no | rendered | **N/A** — sub-form fixes `aggregating=true` for aggregator-kind | unchanged (forced=true by the create-new wrapper, no operator-visible toggle needed) |
| cadence | text | yes (aggregating) | rendered | rendered (aggregator-kind only) | unchanged |
| metadata_keys | textarea | no | rendered (free-form, one per line) | **MISSING** | **BF.2: render in both kinds. BF.4: textarea → picker (option universe = union of all metadata keys declared on any rail/template in this L2 + a fixed canonical-name list)** |
| posted_requirements | textarea | no | rendered (free-form, one per line) | **MISSING** | **BF.2: render in both kinds. BF.4: textarea → picker (enum-like option list TBD — see "open question P1" below)** |
| max_pending_age | text(ISO 8601 Duration) | no | rendered | **MISSING** | **BF.2: render in both kinds. No picker — Duration is a free-form scalar** |
| max_unbundled_age | text(ISO 8601 Duration) | no | rendered | **MISSING** | **BF.2: render in both kinds. No picker** |
| bundles_activity | multi_select(rails_or_templates) | no (yes if aggregating sweeps anything) | rendered | **MISSING (the worst gap — aggregating rails without `bundles_activity` are functionally inert)** | **BF.2: render in aggregator-kind sub-form. Already a structured multi_select — no picker work** |
| metadata_value_examples | yaml_block | no | rendered (free-form YAML map) | **MISSING** | **BF.2: render in both kinds. BF.4: yaml_block → nested picker (key dropdown drawn from metadata_keys field on the same entity; per-key value list = string entries — see "open question P2" below)** |
| amount_typical_range | text("min, max") | no | rendered (free-form composite) | **MISSING** | **BF.2: render in both kinds. No picker — soft bound is a free-form pair of money values; structured-picker carve-out (see locks discussion below)** |
| firings_typical_per_period | text("min, max" or "period: min, max") | no | rendered (free-form composite) | **MISSING** | **BF.2: render in both kinds. Same carve-out — composite scalar with optional discriminator** |
| description | textarea | no | rendered | **MISSING** | **BF.2: render in both kinds. No picker — free-form prose IS the surface (markdown)** |

**12 missing fields on the aggregator-kind sub-form, 11 on the TT-kind sub-form** (`source_origin` / `destination_origin` / `expected_net` are two_leg-only and don't apply to TT-kind). The `bundles_activity` absence is the worst — an aggregating rail without it is functionally inert (rail sweeps on cadence but bundles nothing), so the create-new'd aggregator-rail today can't ever be a functional reconciler for the new rail without a post-create edit.

## TransferTemplate field migration table

The TT-kind BB.2 sub-form is symmetric: it owns the `reconciler_new_*` prefix for TT-only fields.

| FieldSpec.name | kind | Required | Main create | BB.2 sub-form today | Proposed post-BF |
|---|---|---|---|---|---|
| name | text | yes | rendered | rendered (`reconciler_new_name`) | unchanged |
| expected_net | money | yes | rendered | rendered (`reconciler_new_expected_net`) | unchanged |
| completion | text | yes | rendered | rendered (`reconciler_new_completion`) | unchanged |
| leg_rails | multi_select(rails) | yes | rendered | **N/A** — create-new TT auto-appends the new rail to its leg_rails per `_create_new_reconciler_with_rail`; operator doesn't pick | unchanged (auto-handled) |
| transfer_key | textarea | no | rendered (free-form, one per line) | rendered (`reconciler_new_transfer_key`) | **BF.4: textarea → picker (option universe = union of metadata_keys across this template's leg_rails — chicken-egg note: only the new rail's metadata_keys are knowable at create-new time; render as text fallback when sibling rails haven't been picked yet, OR defer the picker to the post-create edit screen)** |
| leg_rail_xor_groups | multi_select_groups(self_leg_rails) | no | rendered as `edit_only=True` — hidden on create | **N/A** — same `edit_only` gate; create-new TT can't author groups before its leg_rails exist | **BF.3: same edit_only constraint applies; the picker shape lands on the rail-edit screen where leg_rails are set, not on the rail-create or BB.2 create-new sub-form** |
| firings_typical_per_period | text("min, max" or "period: min, max") | no | rendered (free-form composite) | **MISSING** | **BF.2: render in TT-kind sub-form. Same carve-out as Rail.firings_typical_per_period** |
| description | textarea | no | rendered | **MISSING** | **BF.2: render in TT-kind sub-form. No picker** |

**2 missing fields on the TT-kind sub-form** (firings_typical_per_period, description). Plus the AI.10 MVP `leg_rail_xor_groups_text` textarea on the TT main create-page that BF.3 retires in favor of the existing `multi_select_groups` field-kind (already structured — the migration is just routing the create-page through the same field-kind the edit page uses, once the staged-edit chicken-egg is handled).

## Free-form textarea inventory

Five `kind="textarea"` fields + one `kind="yaml_block"` across rail + TT. Audit:

| Field | Entity | Current shape | Domain | Proposed post-BF | Step |
|---|---|---|---|---|---|
| metadata_keys | Rail | `<textarea>`, one per line | `tuple[Identifier, ...]`. Identifiers are typed (newtype over str) but values are operator-chosen at first declaration — no fixed enum. **Option universe**: union of `metadata_keys` declared on any rail/template in this L2 + a fixed list of canonical names (`ach_trace_number`, `wire_imad`, `card_arn`, `merchant_settlement_id`, etc.). Operator can still type a fresh name as an option. | **Structured picker** with autocomplete from the L2-wide union + canonical list + free-text-add affordance for new names | BF.4 |
| posted_requirements | Rail | `<textarea>`, one per line | `tuple[Identifier, ...]`. Each value names a rail-leg field (e.g., `metadata.foo`, `posted_at`, `account_role`). **Option universe**: needs a registry of valid field names — TBD whether the L1 PostedRequirements view declares one, or whether it accepts arbitrary dotted paths. **Open question P1** below. | **Structured picker** if option universe enumerable, otherwise stays as textarea with the BF L2 carve-out documented | BF.4 |
| transfer_key | TransferTemplate | `<textarea>`, one per line | `tuple[Identifier, ...]`. Per validator R12, each key MUST also appear in every leg_rail's `metadata_keys`. **Option universe**: intersection of `metadata_keys` across this template's `leg_rails`. | **Structured picker** with options from the intersection. Chicken-egg note: empty when no leg_rails picked yet — falls back to text input until leg_rails are saved (same staged-edit pattern `leg_rail_xor_groups` uses) | BF.4 |
| description | Rail / TT / Chain / LimitSchedule | `<textarea>`, free-form | prose, markdown OK | **Stays as textarea** — free-form prose IS the surface; no picker needed. Carve-out documented under L2 below | (no BF step — explicit no-op) |
| leg_rail_xor_groups_text | Rail BB.2 sub-form (AI.10 MVP) | `<textarea>`, one group per line, comma-separated rail names | `tuple[tuple[Identifier, ...], ...]`. **Option universe**: this template's `leg_rails`. Already a structured `multi_select_groups` field-kind on the TT edit page; the textarea is the MVP shortcut for the BB.2 case. | **Structured picker** — reuse `multi_select_groups` field-kind. Chicken-egg note: only knowable after the rail's TT reconciler has leg_rails ≥ 2 — staged-edit pattern, banner explaining "save the TT with leg_rails first, then come back to author XOR groups" | BF.3 |
| metadata_value_examples | Rail | `<textarea>` rendered as yaml_block, free-form YAML map | `tuple[(Identifier, tuple[str, ...]), ...]`. **Option universe**: keys come from this rail's own `metadata_keys` (so already a structured set on the same entity); per-key values are operator-chosen example strings. | **Nested picker** — key dropdown drawn from sibling `metadata_keys` field, per-key value entries as a list of strings (with add/remove). **Open question P2** below — the value-list UI shape | BF.4 |

## Open questions for BF.0 sign-off

**P1 — posted_requirements option universe.** Is there a registry of valid PostedRequirements field names somewhere in `common/dataset_contract.py` / `common/l2/derived.py`, or does the L1 view accept arbitrary dotted paths (e.g. `metadata.<any-key>`, `posted_at`, `account_role`, `chain.<child-name>`)? If arbitrary, posted_requirements gets the L2 textarea carve-out alongside `description`; if enumerable, BF.4 widens to include the picker. Action: read `common/l2/derived.py::posted_requirements_for` before BF.4 fires.
  - Comment: Please confirm my intutition is correct, this is asking if the metadata fields have a limited corpus. The answer is no. If I missed something, tell me.

**P2 — metadata_value_examples nested-picker UI.** The shape is `tuple[(key, tuple[values, ...]), ...]`. Two reasonable surfaces: (a) one row per key with a comma-separated string of values (inline-edit); (b) one row per key with an expandable sub-list of value rows (each with add/remove). (a) is faster to build, (b) is more discoverable but the BB.2 sub-form already has heavy visual density. Action: prototype both during BF.4 and decide based on the BB.2 visual budget.
  - Comment: I think this needs some collaboration on what a reasonable shape of this is.

**P3 — chicken-egg staged-edit banner copy.** Three fields (`transfer_key`, `leg_rail_xor_groups`, `metadata_value_examples`) all depend on sibling fields that must exist before the picker is meaningful. The existing `_render_multi_select_groups_field` empty-state already handles this for `leg_rail_xor_groups` ("Save the template with at least 2 leg rails first; then open it for editing to add XOR groups."). Same template scales — but BF needs a consistent banner shape across all three. Action: extract the empty-state banner into a `_render_staged_edit_banner(prereq_field, prereq_action)` helper during BF.3.
  - Comment: Agreed this needs some collaboration / experimentation on what works.

## L1-L5 lock check against the table

- **L1 — Form-pairing scales to fields, not just entities.** ✅ Table confirms: 12 of 17 Rail fields missing on the BB.2 aggregator sub-form, 2 of 8 TT fields missing on the BB.2 TT sub-form. The fix shape (widen the sub-form to walk the full `_FIELD_SPECS_BY_KIND` slice with the `reconciler_new_` prefix) is uniform — no per-field special-casing needed for BF.2.
- **L2 — Free-form textareas anti-pattern for typed-identifier domains.** ⚠️ Carve-out needed. Two fields stay textarea: `description` (free-form prose has no enumerable domain), and `posted_requirements` if P1 lands on "arbitrary dotted paths." Three composite-scalar fields (`amount_typical_range`, `firings_typical_per_period`, `max_pending_age` / `max_unbundled_age` Durations) are also free-form scalars — they're not textareas (`kind="text"`) but they're free-form-validated. Propose: L2 reads as "free-form anti-pattern for `kind="textarea"` whose value space IS enumerable from L2 state." Composite-scalar text inputs are out-of-scope; explicit text-input → text-input no-op for `description` is fine.
  - Comment: description is fine to keep free form, it would be useful to have a preview of its rendered content, maybe a edit vs preview?
  - Comment: so the composite-scalar fields are okay to keep if we provide field level validation and help text
- **L3 — Theme-driven surface vocabulary.** ✅ No conflict. New picker empty-states use `bg-surface-alt` (the AM.0 decision-1 token); new picker active surfaces inherit from the existing `field_input_classes()` helper which is already theme-driven.
- **L4 — Banner is additive, not a replacement.** ✅ The BF.1 banner is a new prose surface above the existing per-kind intro card + the per-field `*` markers; nothing existing gets retired.
- **L5 — Coverage scope: rail + transfer_template only.** ✅ Audit confirms: account / account_template / chain / limit_schedule already expose every field via the main `_FIELD_SPECS_BY_KIND` walk (chain's `children` is already a `chain_children` structured picker; account_template's `_ACCOUNT_TEMPLATE_FIELDS` is text-only with no missing-field gap). No BF work on those kinds.

## Sequencing reaffirmed

BF.1 (banner) lands first — it's the smallest piece, doesn't touch field-shape machinery, and the prose-only surface proves the AM-landed helper conventions work for a new render path before BF.2 widens the sub-form. BF.2 (sub-form completeness) is the mechanical bulk — adds the 12+2 missing fields by walking `_FIELD_SPECS_BY_KIND`. BF.3 + BF.4 (structured pickers) are the per-field-shape work; BF.3's `leg_rail_xor_groups` is the simplest (reuses existing `multi_select_groups` field-kind), BF.4 needs new field-kinds for `metadata_keys` / `posted_requirements` (pending P1) / `metadata_value_examples` (pending P2). BF.5 (driver alignment) lands after BF.2 alone (BF.3 / BF.4 are picker-shape improvements; BB.2 completeness is what unblocks the driver). BF.6 verifies + closes.

## Decisions wanted before BF.1 fires

1. **L1-L5 ratified as written above?** L2 needs the textarea-specific carve-out language (description stays textarea; composite-scalar text inputs out of scope). Other locks unchanged.
2. **P1 disposition.** Read `derived.posted_requirements_for` first OR defer the P1 decision to BF.4 implementation time?
3. **P2 disposition.** Same — prototype both UIs during BF.4, or pick now?
4. **P3 helper extraction lands in BF.3 (first staged-edit consumer) or BF.0 follow-up?** Punch-list item either way; just a sequencing call.
