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

## Implications for BB.1 — validator split

```python
# common/l2/validate.py — proposed shape

def validate_structure(instance: L2Instance) -> None:
    """Save-time gate: blocks if the L2 is malformed.

    Runs every check EXCEPT the bilateral-circular completeness ones
    that the operator can't satisfy mid-incremental-build.
    """
    # All 35 structural + reference + deferrable-circular checks here.

def validate_completeness(instance: L2Instance) -> list[L2ValidationError]:
    """Deploy-time gate: collects (does not raise) the completeness
    violations.

    Returns the list so the editor can surface them as warnings;
    callers that need fail-fast (CLI deploy) check the list and raise
    on non-empty.
    """
    errs: list[L2ValidationError] = []
    try:
        _check_single_leg_reconciliation(instance)
    except L2ValidationError as e:
        errs.append(e)
    try:
        _check_variable_single_leg_in_some_template(instance, rails_by_name(instance))
    except L2ValidationError as e:
        errs.append(e)
    return errs

def validate(instance: L2Instance) -> None:
    """Full gate: structure then completeness; raises on first error.
    Existing call sites keep this — deploy + lock + tests don't change.
    """
    validate_structure(instance)
    errs = validate_completeness(instance)
    if errs:
        raise errs[0]
```

## Implications for BB.2 — editor save path

`POST/PUT /l2_shape/<kind>/...` handlers replace `validate(new_inst)` with `validate_structure(new_inst)`. After successful save, compute `validate_completeness(new_inst)` and stash the warnings list onto the L2InstanceCache for surface by the BB.3 UX (per-entity flag + top-banner count) + BB.4 list filter.

## Implications for BB.3-4 — invalid-state UX

- **Per-entity invalid flag**: for each warning in the completeness list, parse the message to extract `(kind, entity_id)` — e.g., `"Rail 'SubledgerCharge': single-leg rail is not reconciled..."` → `("rail", "SubledgerCharge")`. Render a badge on that entity's read-card + list-view row.
- **Top-banner count**: `len(warnings)` across all kinds; clickable to `/l2/validation`.
- **`/l2/validation` page**: lists warnings with kind + entity + raw message + edit link.
- **List filter**: `GET /l2_shape/<kind>/?invalid=1` filters to entities whose ID is in the parsed-warnings set.

## Implications for AI.2.d.1.a (this unblocks)

The driver's existing 2-wave + deferred-edit pattern for `max_unbundled_age` stays useful for cleanliness. After BB.2 lands:

1. Driver creates all rails (including the unreconciled single-legs) — each save validates structurally, succeeds, accumulates a completeness warning.
2. Driver creates the TTs / aggregators that reconcile them — completeness warnings resolve.
3. Final `load_instance(dest)` uses full `validate()` which now passes (everything's reconciled).

The dogfood test's round-trip assertion gates on `load_instance` (full validate) so the editor's permissive save is invisible to the final assertion.

## Operator-visible contract

Before BB: "save fails on any incomplete state" → operator can't build L2 incrementally in the editor.
After BB: "save fails only on malformed structure; completeness warnings surface as flags + banner; deploy blocks on warnings". Operator builds incrementally; the editor highlights what's incomplete; deploy enforces.
