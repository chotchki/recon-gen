# DQ.0 — Strongly-typed database-object model (design lock)

**Status:** design lock, awaiting operator confirm before DQ.1.
**Audit basis:** a four-lane read-only sweep (object graph / order lists / column-ref surfaces / existing typed patterns), 2026-07-02.

## Verdict

There is ONE root cause under both footguns the operator named: **no object owns a DB object's two facts — what it reads from, and what columns it emits.** The dependency DAG lives only in prose comments plus three (really four) hand-kept parallel orderings; the column set lives as ~297 free-floating `ColumnSpec` string literals plus bare identifiers inside f-string SQL. Give each database object ONE declaration — `depends_on` + `list[ColumnSpec]` — and both footguns close from the same node. That's why the operator's instinct that the dependency-graph model "would also solve our implicit column references" is exactly right, and why this is one phase, not two.

The audit also found this is not a hypothetical. **One of the four order lists is already wrong** (`_V_OVERLAY_MATVIEW_SUFFIXES` — a DS.3.2 regression that never propagated to the hand-copy), silent for the specific reason the whole phase exists: nothing derives it, nothing tests it. So DQ isn't "prevent a future divergence" — it's "we already have one, here's the machine that makes the class unrepresentable." That live bug is fixed on this branch with an interim guard (see §1); the structural fix is DQ.1.

Recommended shape: **DQ.1 closes the order-list footgun first** (needs only `depends_on`, cheap, high-certainty, the footgun the operator led with), **DQ.2 types columns incrementally** starting at the matview↔`DatasetContract` boundary the DP work already touched. Honest scope limit up front: this types object BOUNDARIES — declared columns, declared deps, cross-object refs — NOT the raw SQL SELECT-body text. Typed SQL bodies need a query-builder/AST and are a much bigger lift; DQ generates projections where cheap and leaves WHERE/JOIN body identifiers as strings until then. Decisions to confirm are in §7.

## 1. The live bug the audit surfaced (lead exhibit)

`_V_OVERLAY_MATVIEW_SUFFIXES` (`common/snapshotter.py:82`) orders `effective_balances` at index 4 — AFTER `computed_subledger_balance` (2) and `computed_ledger_balance` (3). Both `computed_*` matviews read `FROM {p}_effective_balances` (`schema.py:722`, `:3074`, `:3080`), so this is the reverse of the true dependency. The canonical `refresh_matviews_sql` order has it right (`effective_balances` at 2, then the two `computed_*`); the schema CREATE-order comment (`schema.py:3041-3044`) even warns in prose that inverting them breaks `schema apply` with a missing-relation error. The overlay hand-copy simply never got the DS.3.2 memo when `computed_*` were re-keyed onto `effective_balances`.

**Concrete break (Oracle overlay restore only):** `Snapshotter.restore()` loops the suffix list firing `DBMS_MVIEW.REFRESH(mv, 'C')` — a complete refresh per MV, no dependency cascade. At indexes 2-3 it recomputes both `computed_*` against the still-stale (pre-restore) `effective_balances`, which isn't refreshed until index 4 and never recomputes them again. Then `drift` (reads `computed_subledger_balance`), `ledger_drift` (reads `computed_ledger_balance`) and `drift_summary` (UNION of both) inherit the staleness. Net: after an Oracle trainer-snapshot restore, `drift`/`ledger_drift`/`drift_summary` do NOT match the take-time state — a direct violation of the Snapshotter contract (`snapshotter.py:155-160`), and those are exactly the invariant matviews the trainer dogfood reads back.

**Why it stayed silent — this IS the DQ footgun:** (1) no test references `_V_OVERLAY_MATVIEW_SUFFIXES` — grep of `tests/` is zero, so nothing asserts it equals the canonical order; (2) it's Oracle-only, and DuckDB (local/CI default) plus PG's overlay path (which routes through `refresh_matviews_sql`, the correct order) never touch it; (3) the list's own comment claims a dogfood-walk guard covers it, but that walk is Oracle-only and doesn't run in the standard DuckDB chain.

**Fixed on this branch:** `effective_balances` moved ahead of the two `computed_*` in the suffix list (now identical, modulo prefix, to the canonical refresh order), plus an interim guard test pinning `_V_OVERLAY_MATVIEW_SUFFIXES` against the refresh order so a future re-divergence fails loud. Born-red against the pre-fix list, green after. This guard is the cheap stopgap; DQ.1 deletes it by making the suffix list a derived slice of the graph (no hand-copy left to test).

## 2. The object graph (the topo source DQ.1 consumes)

Per L2 instance, `emit_schema` (`schema.py:88`) emits a single-instance graph, all objects sharing `cfg.db.table_prefix`. **Three roots**, one clean topological order. The often-missed root is `config_kv` — a whole second dependency ARM (the `v_config_*` typed views) feeds several L1 matviews; trace only transactions/balances and you miss half the graph.

```
ROOTS (underived):
  transactions          (ETL/seed)
  daily_balances        (ETL/seed)
  config_kv             (DELETE+INSERT of flattened cfg+L2 JSON — config_table.py)

LAYER 1 (Current* superseded views + config typed views):
  current_transactions        <- transactions
  current_daily_balances      <- daily_balances
  v_config_rails              <- config_kv
  v_config_limit_schedules    <- config_kv
  v_config_chain_children     <- config_kv
  v_config_transfer_templates <- config_kv   (no matview consumer; Studio/BT.4 read it Python-side)
  v_config_account_roles      <- config_kv   (picker source only)
  v_config_rail_metadata_keys <- config_kv   (picker source only)

LAYER 2 (spine + anchors):
  effective_balances          <- current_daily_balances            (CL.5 carry-forward spine — root of the drift/overdraft chain)
  data_anchor                 <- current_transactions, current_daily_balances

LAYER 3 (computed balances):
  computed_subledger_balance  <- effective_balances, current_transactions
  computed_ledger_balance     <- effective_balances, current_transactions, current_daily_balances

LAYER 4 (L1 invariant detectors):
  drift                       <- effective_balances, computed_subledger_balance
  ledger_drift                <- effective_balances, computed_ledger_balance
  overdraft                   <- effective_balances
  expected_eod_balance_breach <- current_daily_balances
  balance_cadence_gap         <- current_daily_balances, current_transactions
  limit_breach                <- current_transactions, v_config_limit_schedules
  stuck_pending               <- current_transactions, v_config_rails
  stuck_unbundled             <- current_transactions, v_config_rails
  chain_parent_disagreement   <- current_transactions
  xor_group_violation         <- current_transactions
  transfer_parents            <- current_transactions
  daily_statement_summary     <- effective_balances, current_transactions

LAYER 5 (detectors on detectors):
  drift_summary               <- drift, ledger_drift
  fan_in_disagreement         <- current_transactions, transfer_parents, v_config_chain_children
  multi_xor_violation         <- current_transactions, v_config_chain_children

SINK:
  l1_exceptions               <- drift, ledger_drift, overdraft, limit_breach, expected_eod_balance_breach,
                                 balance_cadence_gap, stuck_pending, stuck_unbundled, chain_parent_disagreement,
                                 xor_group_violation, fan_in_disagreement, multi_xor_violation   (12-way union)

INVESTIGATION matviews (parallel, off current_transactions):
  inv_pair_rolling_anomalies  <- current_transactions
  inv_money_trail_edges       <- current_transactions

V-OVERLAY: the Studio <base>_v_ clone mirrors the ENTIRE set above over a v-prefix (snapshotter trainer path).
```

This is the graph. Every one of the four order lists is a hand-serialization of it; DQ.1 makes the graph the source and the serializations derived.

## 3. Footgun A — the four hand-maintained order lists

| # | List | Location | Purpose |
|---|------|----------|---------|
| a | refresh order (`names`) | `schema.py::refresh_matviews_sql` (370-444) | `REFRESH MATERIALIZED VIEW` order on PG/Oracle after every load. **The canonical order.** |
| b | DuckDB refresh `names` | `schema.py::_emit_table_based_matview_refresh` | re-CREATE + ANALYZE order on DuckDB (low blast radius). |
| c | L1 drop order (+ inv drop) | `schema.py::_L1_INVARIANT_DROP_NAMES` (2348) + `_INV_MATVIEW_DROP_NAMES` (4125) | `DROP … IF EXISTS` order — must be reverse-dependency. |
| d | overlay suffixes | `snapshotter.py::_V_OVERLAY_MATVIEW_SUFFIXES` (82) | Oracle overlay-restore refresh order. **Was wrong (§1).** |

(a) and (c) are mutually consistent today — (c) is a valid reverse of (a). (d) carried the stale model (§1). A matview add/rename/reorder must be hand-applied to all four PLUS the CREATE order in the emit templates. Miss (a): a matview refreshed before its parent yields empty/stale rows straight into the dashboards. Miss (c): "dependent objects still exist" on PG schema clean/re-run. Miss (d): the Oracle restore staleness above. The lists encode the SAME ~24-node DAG four times; nothing forces agreement.

**DQ.1 target:** one ordered source of truth (the graph's topo sort). refresh = topo; drop = reverse-topo; DuckDB refresh = topo; overlay suffixes = topo (or the appropriate slice). All four become derived views; the divergence class stops being representable.

## 4. Footgun B — string column references

Four surfaces, sharply split by how far typing already reached:

- **dashboard-field refs — essentially SOLVED.** ~262 `ds["col"]` refs validated against `DatasetContract` at construction; a rename fails loud at wiring time. The residual tail is ~25 `options_column="…"` picker strings (`app.py`) — reroute through the existing `LinkedValues.from_column(ds["col"])` and they're typed too. Lowest yield, do last.
- **matview DDL — RAW STRINGS** (`schema.py`, ~4500 lines of f-string DDL). Every projected column (`AS computed_balance`, `AS account_class`) is a bare string.
- **dataset SQL — RAW STRINGS** (`apps/*/datasets.py`, ~6300 lines of f-string CustomSql). SELECT-list names are partly covered by the contract test; **WHERE/JOIN/GROUP-BY columns are not** — `account_class`, `required`, `root_transfer_id` appear in no contract and no test, so a rename is invisible until a live query returns zero rows or errors.
- **`ColumnSpec` literals — a free-floating parallel list.** ~297 declarations (161 l1 / 76 l2ft / 43 inv / 17 exec) hand-kept next to, but not derived from, the matview that actually emits those columns.

**The exposed seam is the matview↔dataset boundary the DP work touched:** the matview projects `AS computed_balance` as a string, the dataset reads `computed_balance` back as a string, and the only guard — `test_dataset_sql_contract_projection` — is a one-directional text-presence regex (contract → SELECT) that is blind to a matview-side rename AND to every non-SELECT column. It's the "walk the generated output" anti-pattern the operator wants gone, and it was band-aiding exactly the CR.3/CR.16 "added the column in 2 of 3 places" bug class.

**Highest-value target:** a typed per-matview column object that BOTH the `schema.py` DDL emitter builds its projection FROM and the `datasets.py` SQL builders reference — a rename then fails at construction on both ends, and the contract becomes a projection of matview truth rather than a hand-kept parallel list.

## 5. The typed model — `DbObject`

**Reuse (do not reinvent):**

1. **`ColumnSpec`** (`dataset_contract.py:148-243`) — near-verbatim as the per-object column declaration; its (name, coarse-type, optional `ColumnShape`, currency, `Storage`) tuple is exactly a matview/table column. Promote `DatasetContract` from "dataset projection interface" to "column set an object emits"; a dataset's contract then DERIVES-FROM (or is construction-checked against) its source `DbObject`'s columns.
2. **`ColumnShape` + `can_assign_to` lattice** (`dataset_contract.py:24-116`) — wholesale, for typed cross-object refs. When object B's SELECT references object A's `account_id`, resolve it against A's declared `ColumnSpec` and shape-check it — the same mechanism that makes drill wiring fail at construction makes a renamed upstream column fail at construction.
3. **The tree's object-ref architecture** (`tree/datasets.py:48-212`) — `Dataset` is already a frozen hashable dependency-graph KEY with `ds["col"]` validated against its contract (KeyError at the wiring site), and `App._validate_dataset_references` is the exact precedent for the construction-time cycle/missing-dep raise. `DbObject` is the identical shape one layer DOWN: `depends_on: tuple[DbObject, ...]` as object refs, `obj["col"]` validated against its own `ColumnSpec` list. The schema layer simply never got the Phase-L treatment the analysis layer did.
4. **A typed-ID NewType** for object names (`MatviewName` / `DbObjectId`, `ids.py`) so `f"{p}_drift"` string-building becomes typed and centrally prefixed, mirroring `cfg.aws.prefixed`.

**Genuinely new:**

- **The `DbObject` node** (Table / View / Matview variants) — nothing today declares "reads-from" as data or owns its column list. This is the missing node unifying the two scattered halves.
- **A topological-order helper** over the `DbObject` graph deriving all four lists (refresh = topo, drop = reverse-topo, DuckDB refresh = topo, overlay suffixes = topo/slice). The direct analog of the existing `App` tree walk.
- **The `Dataset` → `DbObject` bridge** (`Dataset.source → Matview → Matview → base Table`) so the analysis dependency graph and the DB dependency graph become ONE graph.

**Honest limit (flag at lock):** this types object BOUNDARIES — declared columns, declared deps, cross-object refs. The SQL SELECT-body text stays a raw string UNLESS the model also GENERATES the projection list from the typed columns (cheap where the projection is a straight column passthrough; not where it's an expression). Full SQL-body typing needs a query-builder/AST and is a much bigger lift — out of DQ scope. We type boundaries, generate projections where cheap, and do NOT promise typed SQL bodies.

## 6. Sequencing

- **DQ.1 — dependency graph + derived order (do first).** Declare each `DbObject`'s `depends_on` (columns not required yet). Derive refresh / drop / DuckDB-refresh / overlay-suffix orders from the topo sort. Construction-time validation: a cycle or missing dep raises loudly. Gate: derived order == today's (correct) order for all four lists; an inverted dep raises at construction. Cheap, high-certainty, and it's the footgun the operator led with — plus it retires the §1 interim guard by deleting its subject (the hand-copy).
- **DQ.2 — typed columns (incremental).** Start at the L1-invariant-matview ↔ `DatasetContract` boundary the DP work already touched (those objects already HAVE contracts, so wiring contract-derived-from-object-columns is the smallest first bite against the highest-value rename footgun). Then the typed per-matview column object consumed by both the DDL emitter and the SQL builders (§4). WHERE/JOIN body columns come along only as projection-generation reaches them (per the §5 limit).
- **DQ.3 — phase exit + sweep.**

## 7. Decisions for the operator (confirm to unlock DQ.1)

1. **Sequencing:** DQ.1 (order graph, deps-only) → DQ.2 (typed columns, incremental). Confirm, or reprioritize?
2. **Scope limit:** accept that DQ types object boundaries + generates projections where cheap, and that raw WHERE/JOIN body identifiers stay strings until projection-generation reaches them (no typed SQL bodies this phase). Confirm the limit?
3. **DQ.2 first bite:** incremental starting at the matview↔`DatasetContract` boundary (recommended), vs a bigger-bang typed-column pass. Confirm incremental?
4. **The §1 overlay-order fix (already on this branch):** merge it independently as a standalone hotfix (it's an unambiguous correctness fix, Oracle-restore-only, not blocking the DuckDB/CI chain), or let it ride in with DQ.1? Recommend: merge independently — it's a real bug, and DQ.1 supersedes only its interim guard, not the fix.
5. **Node shape:** `DbObject` as a frozen hashable node with `depends_on: tuple[DbObject, ...]` and `obj["col"]` validated against its own `ColumnSpec` list — i.e. `Dataset` one layer down. Confirm the shape before I build it?

## Appendix — audit provenance

Four-lane parallel read-only sweep, 2026-07-02: object inventory (every object + deps + emitted columns + defined_at), order-list extraction + divergence analysis (surfaced the §1 live bug), column-ref surface survey (the four surfaces + the one-directional-guard blind spot), existing-typed-pattern survey (the reuse map in §5). Full per-lane returns in the phase's workflow journal.
