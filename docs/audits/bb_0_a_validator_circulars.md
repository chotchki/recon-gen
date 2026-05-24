# BB.0.a — Validator circular-tension catalog

**Date**: 2026-05-24
**Input to**: Phase BB (save-time / deploy-time validator split + invalid-state editor UX)
**Source**: walk of every `_check_*` in `src/recon_gen/common/l2/validate.py` (38 checks total) against the operator's incremental-construction workflow.

## Question being answered

> When the operator uses the Studio editor to build an L2 incrementally,
> which validator checks block save in ways the operator cannot work
> around with the create-without-then-edit-in pattern?

Answer determines what Phase BB classifies as:

- **save-time-structural** — runs on every save; blocks (can't be sidestepped, never produces an invalid in-flight state).
- **deploy-time-completeness** — runs at deploy gate only; surfaces in editor as a warning + per-entity invalid flag + filter, never blocks save.

The split is the editor's contract: structural errors mean the L2 is malformed (operator must fix to save); completeness errors mean the L2 is incomplete (deploy will reject, but save proceeds so the operator can keep building).

## Methodology

For each check:

1. **Reference shape** — which entity references which.
2. **Operator workflow** — what create order an operator would naturally try; where it hits.
3. **Fixable by topological order?** — does creating Y before X resolve it?
4. **Fixable by deferred-field pattern?** — can the operator create X with a field stripped, create Y, then edit the field back?
5. **Bilateral circular?** — does both creation orders fail (X needs Y; Y needs X)?

Categories used in the table:

- **STR — Structural.** Entity shape, types, uniqueness, required fields, internal coherence. Always safe at save time. → BB.1 keeps in `validate_structure`.
- **REF — Reference resolution.** Entity X references entity Y by name. Y must be declared. Topo order in `create_l2` puts Y before X. → BB.1 keeps in `validate_structure` (it IS a structural property — references must resolve).
- **DEFER — Deferrable circular.** X has an optional field F that triggers a completeness check needing Y to reference X. Operator creates X without F, creates Y, edits F into X. Pattern exists in the driver today. → BB.1 keeps in `validate_structure` (the driver pre-defers; if the operator misses it, the save fails honestly).
- **BI — Bilateral circular.** X's mere existence triggers a completeness check needing a Y to forward-reference X. No deferrable field on X exists. Operator cannot save X alone in a valid state. → BB.1 moves to `validate_completeness` — editor save permits in-flight; deploy gates.

## Catalog

| Check | Category | Reference shape | Notes |
|---|---|---|---|
| `_check_unique_account_ids` (U1) | STR | within `accounts` list | uniqueness — duplicate IDs invalid regardless of order |
| `_check_unique_account_template_roles` | STR | within `account_templates` | uniqueness |
| `_check_unique_rail_names` | STR | within `rails` | uniqueness |
| `_check_unique_transfer_template_names` | STR | within `transfer_templates` | uniqueness |
| `_check_unique_limit_schedule_combinations` | STR | within `limit_schedules` | uniqueness |
| `_check_no_template_id_collides_with_singleton` | STR | between `account_templates` materialized IDs + `accounts` IDs | naming collision |
| `_check_role_references` | REF | Rail.source/destination/leg_role → role | topo: roles (accounts/templates) before rails |
| `_check_account_parent_role_resolves` | REF | Account.parent_role → another Account.role | topo: parent accounts before children (`_topo_accounts_by_parent` in driver) |
| `_check_account_template_parent_role_is_singleton` | REF | AccountTemplate.parent_role → on some Account | topo: Accounts before AccountTemplates (driver order) |
| `_check_template_leg_rails_exist` | REF | TT.leg_rails → declared rail names | topo: Rails before TT |
| `_check_template_has_at_least_one_leg_rail` | STR | within TT | required-field shape |
| `_check_chain_endpoints_exist` | REF | Chain.parent + children → rail/TT names | topo: rails+TTs before chains |
| `_check_limit_schedule_parent_role_resolves` | REF | LimitSchedule.parent_role → on some Account | topo: Accounts before LimitSchedules |
| `_check_template_leg_rails_are_non_aggregating` (R7) | REF | TT.leg_rails → must be non-aggregating | both rails must exist; reference check |
| **`_check_max_unbundled_age_only_on_bundled_rails` (R8)** | **DEFER** | Rail.max_unbundled_age set → some aggregator's `bundles_activity` must contain Rail.name | **driver workaround**: strip `max_unbundled_age` on create, edit-in after aggregator lands (already implemented in `studio_editor.py`'s 2-wave + deferred edit pattern). |
| `_check_dotted_bundle_selectors_resolve` | REF | aggregator's `bundles_activity` dotted form → TT.leg_rail name | topo: TT before aggregator |
| `_check_limit_schedule_rail_resolves` (R10) | REF | LimitSchedule.rail → declared rail | topo: Rails before LimitSchedules |
| `_check_bare_bundles_activity_selectors_resolve` | REF | aggregator's `bundles_activity` bare form → declared Rail/TT | topo: bundled rails/TTs before aggregator |
| `_check_transfer_key_in_leg_rail_metadata_keys` | REF | TT.transfer_key → its leg rails' `metadata_keys` | within TT after rails; ref-resolution |
| `_check_metadata_value_example_keys_resolve` | REF | rail.metadata_value_examples keys → rail.metadata_keys | within rail (self-reference) |
| `_check_variable_leg_count_per_template` | STR | within TT | shape constraint |
| `_check_leg_rail_xor_group_shape` | STR | within TT | shape constraint |
| **`_check_variable_single_leg_in_some_template` (C3)** | **BI** | Variable-direction SingleLegRail → must appear in some TT.leg_rails | **bilateral**: the rail's mere existence triggers it; no deferrable field. Operator creates Variable SingleLegRail → save fails (no TT contains it yet). Operator creates the TT first → its `leg_rails` reference the not-yet-created rail (REF check fails). → BB classification: **completeness**. |
| `_check_chain_parent_has_non_empty_children` | STR | within Chain | required-non-empty |
| `_check_chain_no_duplicate_child_per_parent` | STR | within Chain | uniqueness |
| `_check_fan_in_shape` | STR | within Chain children | shape constraint |
| `_check_two_leg_expected_net_consistency` (S5) | STR | within rail | internal coherence |
| **`_check_single_leg_reconciliation` (S3)** | **BI** | non-aggregating SingleLegRail → must be in some TT.leg_rails OR some aggregator's `bundles_activity` | **bilateral**: same shape as C3, broader (covers all single-leg non-aggregating, not just Variable-direction). **THE BLOCKER for AI.2.d.1.a's `SubledgerCharge` case.** → BB classification: **completeness**. |
| `_check_chain_aggregating_not_child` (S4) | REF | Chain.children must not include aggregating rails | both must exist; reference + disjoint check |
| `_check_aggregating_rail_required_fields` | STR | within rail | required fields (cadence + bundles_activity) |
| `_check_amount_typical_range_shape` | STR | within rail | shape |
| `_check_firings_typical_per_period_shape` | STR | within rail | shape |
| `_check_non_aggregating_rail_no_cadence_or_bundles` | STR | within rail | shape — forbids cadence/bundles on non-aggregating |
| `_check_completion_vocabulary` | STR | within TT | enum check |
| `_check_cadence_vocabulary` | STR | within rail | enum check |
| `_check_per_leg_origin_resolution` | STR | within rail | shape |
| `_check_role_business_day_offsets_resolve` | REF | top-level `role_business_day_offsets` keys → declared roles | topo: rest first, then top-level settings (driver order) |

## Summary

| Category | Count | BB classification |
|---|---|---|
| STR — Structural | 21 | `validate_structure` |
| REF — Reference resolution | 12 | `validate_structure` (refs must resolve; topo order in driver makes this trivially safe in incremental construction) |
| DEFER — Deferrable circular | 1 | `validate_structure` (driver pre-defers; if not, fails honestly) |
| **BI — Bilateral circular** | **2** | **`validate_completeness`** |

**Total bilateral-circular checks: 2** — both single-leg-rail reconciliation variants.

- **S3** `_check_single_leg_reconciliation` — every non-aggregating single-leg rail must be reconciled by either a TT or an aggregator.
- **C3** `_check_variable_single_leg_in_some_template` — every Variable-direction single-leg rail must appear in some TT.leg_rails (a narrower subset of S3).

The split is small and well-bounded: 35 of 38 checks stay structural; only these 2 move to deploy-time-completeness. The editor permits in-flight invalid states ONLY for the bilateral-circular case (a rail exists without its reconciler — fix is to add the reconciler).

## Direction LOCKED — surgical form-pairing (NOT validator split)

**Decision 2026-05-24**: the original validator-split + invalid-state UX plan was over-scoped for 2 bilateral cases. Better fix: **at the 2 actual circular points, the editor's create form pairs the rail's creation with the reconciler choice. Server commits both atomically; validator stays untouched; no invalid in-flight state ever exists.**

Why: 5% of validator checks need this; the combinatorial UX explosion of "every save can be incomplete" (per-entity flags + warnings + filter + invalid-state page) is a big tax to pay for solving 2 cases. Form-pairing also matches the operator's mental model: a non-aggregating single-leg rail without a reconciler IS conceptually incomplete; the editor should require the reconciler at the same moment.

### Form-pairing shape

For both S3 and C3 (same shape, different applicability):

```
Editor "Create Rail" form (subtype=single_leg, aggregating=false):

  Name:        [SubledgerCharge]
  leg_role:    [CustomerSubledger]
  leg_direction: [Variable | Credit | Debit]
  ...

  ┌─ Reconciler (required) ────────────────────────────────────┐
  │ This rail won't reconcile its own drift; pick a            │
  │ TransferTemplate (closes the TT's expected_net) OR an      │
  │ aggregating Rail (gets swept into a bundle).               │
  │                                                            │
  │ ◯ Attach to existing reconciler                            │
  │   Kind: [TransferTemplate ▾]                               │
  │   Name: [CustomerFeeAccrual ▾]                             │
  │                                                            │
  │ ◯ Create new reconciler inline                             │
  │   Kind: [TransferTemplate ▾]                               │
  │   Name: [_______________]                                  │
  │   ...minimum required fields for that kind...              │
  └────────────────────────────────────────────────────────────┘

  [Save] → server atomic: { add rail + edit/create reconciler }
            → full validate() → cache.save()
```

For the C3 Variable-direction sub-case: the Kind dropdown shows only `TransferTemplate` (aggregators don't reconcile Variable-direction per the validator).

### Server-side atomic commit

`POST /l2_shape/rail/` extends to accept `reconciler_kind` + `reconciler_name` + (if new) the nested-create fields. The handler:

1. Apply the rail-create mutation to a copy of the cache state.
2. Apply the reconciler mutation (either edit existing — append rail to `leg_rails` / `bundles_activity` — or create new with the rail in its list).
3. Run full `validate()` on the result.
4. On success: `cache.save(new_inst)`. On failure: discard; return the form re-rendered with the validator error inline.

The composite mutation is the unit of atomicity; nothing partial ever persists.

### Driver wiring (BB.3)

`StudioHttpEditorDriver.create_rail` for non-aggregating single-leg rails computes the reconciler-choice from the reference L2 (find which TT or aggregator in the reference contains this rail name) and passes it in the POST. The `create_l2` walk's 2-wave + deferred-edit pattern for `max_unbundled_age` STAYS — that's still a real DEFER case (different shape from the BI cases addressed here).

### Operator-visible contract

Before BB: "save fails when you try to build single-leg rail X before its reconciler exists; sometimes the only fix is to abandon the editor and edit YAML directly".

After BB: "Create Rail form for non-aggregating single-leg rails has a required Reconciler picker; you can't accidentally save an unreconciled rail. The Reconciler picker offers existing TTs/aggregators OR an inline-create. The save is atomic — both the rail and the reconciler land together (or neither lands)."

### Out of scope (consequence of NOT doing the validator split)

- No invalid-state UX in the editor. Per-entity invalid flag, top-banner count, `?invalid=1` filter, `/l2/validation` page — all deferred. If a future bilateral validator surfaces that can't be solved with form-pairing, revisit.
- No `validate_structure` / `validate_completeness` API split. `validate()` stays monolithic.
- The "operator saved an incomplete L2" scenario doesn't exist — every save is fully valid.
