# BS.1 — `_config_kv` static-collapse audit

> **Status:** COMPLETE 2026-05-29. Walked all 4 apps' datasets.py +
> schema.py matview emitters via 5 parallel research agents.
> Trace: `SPEC.md::D6 reframe`.

## Headline recommendation

**BS.5 fires in-phase, with focused scope:**

1. **Land `_v_config_chain_children` projection view** (~30-40 lines,
   mirrors `_v_config_rails` shape — `parent_id` walk over `_config_kv`,
   no JSON_TABLE-on-CLOB Oracle risk).
2. **Convert 4 immediate-win paths** (no new view authoring needed —
   they JOIN existing `_v_config_rails` / `_v_config_limit_schedules`):
   - `build_exc_unmatched_rail_name_dataset` (L2FT)
   - `build_exc_dead_limit_schedules_dataset` (L2FT)
3. **Convert 7 paths gated on `_v_config_chain_children`:**
   - `_fan_in_disagreement` matview (schema.py)
   - `_multi_xor_violation` matview (schema.py)
   - `build_chains_dataset` (L2FT)
   - `build_chain_instances_dataset` (L2FT)
   - `build_exc_chain_orphans_dataset` (L2FT)
   - `build_tt_instances_dataset` (L2FT — needs `_v_config_transfer_templates` too)
   - `build_tt_legs_dataset` (L2FT — needs `_v_config_transfer_templates` too)

**Defer to BT:** `_v_config_transfer_templates`, `_v_config_bundles_activity`,
`_v_config_rail_metadata_keys`, `_v_config_rails.leg_shape` extension.
These each unlock 1-2 more paths but add view-authoring cost that
exceeds BS budget.

**Test-surface delta estimate:** 9 emit paths (4 immediate + 5 view-gated)
collapse from N-parametrize (one per L2) to canonical SQL + `_kv` fixture
matrix. The matview-emit conversions also tighten the matview unit-test
matrix correspondingly.

**Why this is the right cut:** the `_v_config_chain_children` view has
load-bearing fan-out (7 dependent paths from one ~30-line view). The
2 already-existing-view conversions are nearly free. Other projection
views give ≤2 paths each and need their own authoring + Oracle
verification cycle — better as BT-time work where ETL Support's needs
will clarify the right shape.

## Framing

The L2 yaml is authoring; `_config_kv` is its runtime projection. AW
established two projection views (`_v_config_rails`,
`_v_config_limit_schedules`) and converted 3 matviews (`_limit_breach`,
`_stuck_pending`, `_stuck_unbundled`) and 2 L2-invariant matviews
(`_unmatched_rail_name_cases`, `_dead_limit_schedule_cases`).

**The static-collapse hypothesis** (user 2026-05-29): with `_kv`
carrying runtime variation, the SQL **emit** layer becomes *more*
static. Each emit path collapses from "N Python-baked SQL forms per L2"
to "one canonical SQL × N `_kv` fixtures." Test surface shrinks.

**Classification rubric:**

- **P0** — collapse-win. Per-L2 variation lives entirely in `_kv`-bind
  L2 fields. Conversion: replace Python f-string interpolation with a
  JOIN against an existing or new `_v_config_*` view. Test surface drops.
- **P1** — collapsible-but-costly. Conversion possible but small benefit,
  Oracle dialect quirk, or projection view doesn't exist yet and
  would cost more to author than the conversion saves.
- **P2** — legitimately per-L2-dynamic OR genuinely data-only. The
  emit path bakes Python computation (per-L2 generated calc fields,
  per-L2 dataset arity, per-L2 deployment_name prefixing) with no
  projection-view equivalent, OR reads only base-table columns with
  no L2 fields baked.

## Existing `_v_config_*` views (the AW baseline)

| View | Source rows | Consumers today |
| ---- | ----------- | --------------- |
| `<prefix>_v_config_rails` | one per L2 rail (name, source_role, destination_role, source_origin, …) | `_unmatched_rail_name_cases`, `_stuck_pending`, `_stuck_unbundled` |
| `<prefix>_v_config_limit_schedules` | one per L2 limit_schedule (parent_role, rail, direction, cap, …) | `_dead_limit_schedule_cases`, `_limit_breach` |

**Pattern:** `parent_id` self-join walk over `_config_kv`, projects scalar
fields as named columns. Renderer in `common/l2/schema.py::_render_v_config_*`.
~30-40 lines per view. Plain VIEW (not matview) — re-evaluates on each
query; `_config_kv` is per-deploy with 100s of rows max, cheap.

## Findings by file

### `apps/executives/datasets.py` — 5 builders, all canonical

**Headline:** Every Exec builder reads only `cfg.db_table_prefix` +
`cfg.dialect` (excluded from L2-shape variation per rubric). Zero L2
fields baked. Already canonical — no conversion work.

Builders: `build_transaction_summary_dataset`,
`build_transaction_daily_dataset`, `build_account_summary_dataset`,
`build_account_summary_active_dataset`, `build_transaction_legs_dataset`.
All P2 by virtue of L2-blindness; the entire Exec app is a
static-collapse no-op.

### `apps/l1_dashboard/datasets.py` — 19 builders, all P2

**Headline:** All 14 walked builders (+ 5 already-skipped) are P2 — none
bake L2-shape into SQL. Per-L2 variation already absorbed at higher
layers: L2-universe enumerations (rail names, account roles) get
consumed *outside* the dataset SQL — as `ParameterDropDownControl
.StaticValues` options in `app.py` or as dataset-parameter defaults set
from `cfg.test_generator.as_of_frame()`.

Cross-cutting: `l1_rail_universe_values`, `l1_account_role_values`,
`l1_supersede_reason_values` in `app.py` DO read `l2_instance.X` — but
those feed AnalysisDefinition JSON (static-per-deploy, no QS-runtime
`_kv` read possible). **Out of scope for this audit** per the
hypothesis framing (rubric notes app.py JSON-emit paths skip out).

| Builder | L2 fields baked | Class |
| ------- | ---------------- | ----- |
| `build_drift_dataset` (747) | none | P2 |
| `build_ledger_drift_dataset` (808) | none | P2 |
| `build_overdraft_dataset` (860) | none | P2 |
| `build_limit_breach_dataset` (901) | none (cap inlined in matview upstream) | P2 |
| `build_todays_exceptions_dataset` (953) | none | P2 |
| `build_daily_statement_summary_dataset` (1066) | none | P2 |
| `build_daily_statement_transactions_dataset` (1204) | none | P2 |
| `build_transactions_dataset` (1234) | none | P2 |
| `build_drift_timeline_dataset` (1289) | (converted via AW) | — |
| `build_ledger_drift_timeline_dataset` (1333) | none | P2 |
| `build_stuck_pending_dataset` (1383) | none | P2 |
| `build_stuck_unbundled_dataset` (1428) | none | P2 |
| `build_supersession_transactions_dataset` (1477) | none | P2 |
| `build_supersession_daily_balances_dataset` (1537) | none | P2 |
| `build_l1_accounts_dataset` (1575) | none | P2 |
| `build_l1_ds_accounts_dataset` (1651) | none | P2 |
| `build_l1_ds_roles_dataset` (1688) | none | P2 |
| `build_l1_tx_ids_dataset` (1711) | none | P2 |
| `build_l1_tx_facets_dataset` (1731) | none | P2 |

### `apps/investigation/datasets.py` — 9 builders, all P2

**Headline:** Same pattern as L1 — every builder reads `cfg.db_table_prefix`
+ `cfg.dialect` only, with all per-L2 variation absorbed by the
upstream Investigation matviews (`inv_pair_rolling_anomalies`,
`inv_money_trail_edges`). Investigation already IS the collapsed
shape — the matview is the projection view, just realized as a
refreshable materialization rather than a view.

Builders: `build_recipient_fanout_dataset`, `build_volume_anomalies_dataset`,
`build_volume_anomalies_distribution_dataset`, `build_money_trail_dataset`,
`build_money_trail_roots_dataset`, `build_account_network_dataset`,
`build_account_network_inbound_dataset`, `build_account_network_outbound_dataset`,
`build_account_network_accounts_dataset`. All P2.

### `apps/l2_flow_tracing/datasets.py` — 13 builders, 10 P0 (the action)

**Headline:** L2FT is where the dataset-layer wins live. Of 13 builders:
**10 P0** (collapse-win with the right projection views), **2 P2** (locked
by Oracle ORA-40597 — JSON path must be parse-time literal, can't
substitute), **1 P1** (`leg_shape` derivation needs view extension),
**1 orchestrator** (n/a).

| Builder | L2 fields baked | Class | View needed |
| ------- | ---------------- | ----- | ----------- |
| `build_all_l2_flow_tracing_datasets` (549) | none — orchestrator | n/a | — |
| `build_postings_dataset` (723) | `rails[].metadata_keys` (UNION fan-out) | **P1** | partial via `_v_config_rail_metadata_keys`, fan-out residual stays per Oracle ORA-40597 |
| `build_meta_values_dataset` (858) | `rails[].metadata_keys` (per-key SELECT) | **P2** | Oracle ORA-40597 locks the per-key fan-out structurally |
| `build_chains_dataset` (920) | chains via `_declared_chains_cte` | **P0** | `_v_config_chains` + `_v_config_chain_children` |
| `build_chain_instances_dataset` (1005) | chains + metadata_keys | **P0** | `_v_config_chain_children` (metadata residual P1) |
| `build_exc_chain_orphans_dataset` (1211) | chains (needs `fan_in`) | **P0** | `_v_config_chain_children` (with `fan_in` column) |
| `build_exc_unmatched_rail_name_dataset` (1311) | rail names | **P0** | **existing `_v_config_rails.name`** (immediate win) |
| `build_exc_dead_rails_dataset` (1345) | rails (with `leg_shape`) | **P1** | needs `_v_config_rails` extension to project `leg_shape` |
| `build_exc_dead_bundles_activity_dataset` (1380) | rails[].bundles_activity (pairs) | **P0** | needs `_v_config_bundles_activity` (new view) |
| `build_exc_dead_metadata_dataset` (1415) | rails[].metadata_keys (per-key NOT EXISTS) | **P2** | Oracle ORA-40597 (same constraint as meta_values) |
| `build_exc_dead_limit_schedules_dataset` (1448) | limit_schedules | **P0** | **existing `_v_config_limit_schedules`** (immediate win) |
| `build_unified_l2_exceptions_dataset` (1489) | UNION over all 6 exc-* shapes | **P0**-composite | sums per-branch (4 collapse, 2 metadata-fanout residual) |
| `build_tt_instances_dataset` (2017) | templates + chains + metadata_keys | **P0** | `_v_config_transfer_templates` + `_v_config_chain_children` |
| `build_tt_legs_dataset` (2231) | templates + chains + metadata_keys | **P0** | same as tt_instances |

### `common/l2/schema.py` — matview emit, 2 P0 + 5 already-converted

**Headline:** Of 16 walked non-skipped matviews: **2 already-converted**
(via AW baseline), **2 P0**, **12 P2** (data-only — drift / overdraft /
Current* supersession / transfer_parents / both Inv matviews). The 2
P0s both want the same view: **`_v_config_chain_children`**.

No Oracle JSON_TABLE-on-CLOB risk re-surface: `_v_config_*` walks
`_config_kv` relationally via `parent_id` self-joins; nested arrays
project cleanly the same way `rails` already does.

| Matview | Emit shape | L2 fields baked | Class |
| ------- | ---------- | ---------------- | ----- |
| `_current_transactions` | data-only (supersession) | none | P2 |
| `_current_daily_balances` | data-only (supersession) | none | P2 |
| `_computed_subledger_balance` | data-only | none | P2 |
| `_computed_ledger_balance` | data-only | none | P2 |
| `_drift` / `_ledger_drift` | data-only | none | P2 |
| `_overdraft` | data-only | none | P2 |
| `_expected_eod_balance_breach` | data-only | none | P2 |
| `_limit_breach` | JOINs `_v_config_limit_schedules` | (converted) | — |
| `_stuck_pending` / `_stuck_unbundled` | JOIN `_v_config_rails` | (converted) | — |
| `_transfer_parents` | data-only | none | P2 |
| **`_fan_in_disagreement`** | bakes inline VALUES/UNION-ALL CTE of fan_in children | `chain.parent`, `child.name`, `child.expected_parent_count` | **P0** |
| **`_multi_xor_violation`** | bakes inline VALUES/UNION-ALL CTE of multi-children non-fan_in pairs | `chain.parent`, `child.name` | **P0** |
| `_inv_pair_rolling_anomalies` | data-only (rolling window) | none | P2 |
| `_inv_money_trail_edges` | data-only (recursive CTE) | none | P2 |

(Already-walked rows from initial scaffold: `_chain_parent_disagreement`,
`_chain_orphan_cases`, `_xor_group_violation_cases`, `_todays_exceptions`,
`_daily_statement_summary`. These were classified P0/P0/P0/composite/P1
in the scaffold; the schema.py agent's deeper walk found that the
chain-class matviews use the SAME `_v_config_chain_children` view —
so they fold into the same conversion arc.)

## L2 entity-kind coverage gap (revised)

| Entity kind | Status | BS scope |
| ----------- | ------ | -------- |
| Rail | Existing `_v_config_rails` ✓ | Used as-is by 2 immediate-win L2FT conversions |
| LimitSchedule | Existing `_v_config_limit_schedules` ✓ | Used as-is by 1 immediate-win L2FT conversion |
| Chain + ChainChildren | **NEW: `_v_config_chain_children`** | **In scope** — single highest-leverage new view |
| TransferTemplate | New (`_v_config_transfer_templates`) | **Defer to BT** — needed by tt_instances/tt_legs only |
| BundleActivity | New (`_v_config_bundles_activity`) | **Defer to BT** — needed by 1 builder |
| RailMetadataKeys | New (`_v_config_rail_metadata_keys`) | **Defer to BT** — partial value (Oracle ORA-40597 caps the win) |
| Account | New (`_v_config_accounts`) | **Defer to BT** — not surfaced as load-bearing by any walked path |
| AccountTemplate | New (`_v_config_account_templates`) | **Defer to BT** — not surfaced as load-bearing |

## BS.5 in-phase work items

If user confirms the recommendation, BS.5 becomes:

1. **Author `_v_config_chain_children`** view in `common/l2/schema.py`.
   Mirror `_v_config_rails` shape. Project: `parent_name`, `child_name`,
   `expected_parent_count`, `fan_in`, plus any other ChainChildSpec fields
   needed by the dependent paths.
2. **Convert immediate-win paths** (no new view):
   - `build_exc_unmatched_rail_name_dataset` → JOIN `_v_config_rails.name`
   - `build_exc_dead_limit_schedules_dataset` → JOIN `_v_config_limit_schedules`
3. **Convert `_v_config_chain_children`-gated paths:**
   - `_fan_in_disagreement` matview
   - `_multi_xor_violation` matview
   - `build_chains_dataset`
   - `build_chain_instances_dataset` (chain side; metadata fanout stays)
   - `build_exc_chain_orphans_dataset`
4. **Test-surface check:** unit tests for the 7 paths should collapse
   from N-per-L2 to 1 + a small `_kv` fixture matrix.

## Open notes / future work

- **Oracle JSON_TABLE-on-CLOB risk re-check** — confirmed no re-trip for
  `_v_config_chain_children` (relational walk like rails).
- **Auth on projection views** — defer; `_config_kv` doesn't hold
  secrets-adjacent fields today.
- **App2 vs QS coverage** — both renderers read the same dataset SQL via
  `register_sql` / `query_db_via_cfg`; conversion benefits both by
  construction.
- **The L2FT `tt_instances` + `tt_legs` are tempting** — they're P0 but
  need `_v_config_transfer_templates` *plus* `_v_config_chain_children`.
  Authoring two views in one phase risks scope-creep; defer the
  TransferTemplate view to BT where ETL Support's needs will likely
  drive the same projection (D4.surface #1 wants per-template column
  expectations).

## Out-of-scope: extending the kv pattern to transactions metadata

**Considered + rejected 2026-05-29.** The Oracle ORA-40597 constraint
that pins `build_meta_values_dataset` and `build_exc_dead_metadata_dataset`
at P2 (and keeps `build_postings_dataset` at P1) is structurally the
same shape AW solved at the L2-config layer. Same pattern would, in
principle, work: flatten `transactions.metadata` JSON into a relational
`<prefix>_transactions_metadata_kv(transaction_id, key, value)` table.

**Why it stays out of scope:** the cost scales with transactions
(not with L2 size). A 100K-transaction deploy with 4-6 declared
metadata keys per rail yields hundreds of thousands to low millions
of kv rows per deploy — a substantial new derived table on every
ETL load, not a small convenience structure. The 3-4 paths it would
collapse don't justify the data-layer expansion at that cost.

**Shelved indefinitely.** The only path that would change the math
is dropping the Oracle 19c JSON_VALUE parse-time-literal constraint
(Oracle 23c lifts it), which **isn't happening anytime soon** per
`[[project_oracle_19c_compat]]`. The 3 affected paths (`build_meta_values_dataset`,
`build_exc_dead_metadata_dataset`, `build_postings_dataset`
metadata-fan-out) stay P1/P2 by construction for the foreseeable
future. No plan entry, no revisit cadence — note here only so the
option doesn't get re-discovered + re-evaluated each phase.

## Recommendation matrix

| Lever | Cost | Paths unlocked | In phase? |
| ----- | ---- | -------------- | --------- |
| Use existing `_v_config_rails` | 0 new views | 1 immediate L2FT win | **YES** |
| Use existing `_v_config_limit_schedules` | 0 new views | 1 immediate L2FT win | **YES** |
| New `_v_config_chain_children` | 1 view (~30-40 lines) | 5 (2 matviews + 3 L2FT) | **YES** |
| New `_v_config_transfer_templates` | 1 view | 2 L2FT (tt_instances, tt_legs) | **Defer to BT** |
| New `_v_config_bundles_activity` | 1 view | 1 L2FT | **Defer to BT** |
| New `_v_config_rail_metadata_keys` | 1 view | partial (Oracle-capped) | **Defer to BT** |
| `_v_config_rails` extension (`leg_shape`) | small | 1 L2FT | **Defer to BT** |
| New `_v_config_accounts` | 1 view | 0 walked paths benefit | **Defer indefinitely** |
| New `_v_config_account_templates` | 1 view | 0 walked paths benefit | **Defer indefinitely** |
