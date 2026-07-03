# DQ.4 — the emitted-column map (DbObject.columns foundation)

**Status:** verified map + metadata decisions, feeding the DbObject.columns declarations.
**Basis:** a 41-agent recon (7 extract slices → 33 adversarial per-object verifies → synthesis), 2026-07-03. Every object's emitted column list was read from the REAL CREATE body in `schema.py` (NOT the downstream DatasetContract, NOT the comments — the DQ.1 review's error class). All 33 verified CONFIRMED; the `drift` map matches a hand-check (schema.py:3017-3029).

`DbObject.columns` is the **emitted** projection — what the object's CREATE body actually outputs — NOT the DatasetContract (which is a downstream *dataset* projection that adds computed columns, drops synthetic ones, and re-types money after `/100`). The full per-object map lives in the workflow journal (`subagents/workflows/wf_26999780-1dc/journal.jsonl`); the declarations in `db_objects.py` are the authoritative encoding.

## Metadata decisions (the 6 recon judgment-flags)

1. **Storage normalization (systemic) — DECIDED.** 5 detector/rollup matviews tagged NON-money columns `storage=CENTS` as a raw default, inconsistent with the ~28 objects using `DOLLARS`. Rule: **`storage=CENTS` iff the column is money emitted as raw BIGINT cents (`currency=True`); every non-money column is `DOLLARS`.** This isn't cosmetic — the renderer's `/100` divide is gated on `Storage.CENTS`, so a `CENTS`-tagged non-money column risks a spurious divide. Normalize non-money → `DOLLARS`.
2. **`limit_breach.cap` — CONFIRMED `CENTS`.** Emitted as `ls.cap * 100` (dollars lifted to cents at schema.py:928/1034) so `outbound_total > cap` compares cents; it IS money-in-cents at the matview.
3. **`inv_pair_rolling_anomalies.pop_mean` / `pop_stddev` — OPERATOR CALL (defaulted).** AVG/STDDEV of `window_sum` (cents-scale). A sum is an amount; a mean/stddev is a statistical moment. `VOLUME_ANOMALIES_CONTRACT` types both non-currency `DECIMAL`. **Default: follow the contract — `currency=False`, `DOLLARS`** (dimensionless stats, not `$`-formatted). Flagged for chotchki: flip `pop_mean` to currency if a mean-of-cents should read as money.
4. **`window_sum` / `hop_amount` — DECIDED `CENTS`/`currency=True`.** These are money AMOUNTS (a windowed sum / an edge's moved money), unlike the #3 stats. `DbObject.columns` records the **emitted truth** (raw cents); the reading contracts type them `DECIMAL` non-currency because the dataset SELECT `/100`s them — that transform is reconciled at DQ.4.2, not by weakening the emitted declaration.
5. **`balance_cadence_gap.business_day_start` — CONFIRMED `DATETIME`/`DATETIME_DAY`.** It's the `calendar_day_spine` DATE value (join-keyed via `to_date`, DS.5.4), not the stored TIMESTAMP; the coarse `DATETIME` type doesn't distinguish DATE from TIMESTAMP, which is fine.
6. **`stuck_pending`/`stuck_unbundled.age_seconds` — CONFIRMED `DECIMAL`.** `EXTRACT(EPOCH …)` numeric; no dedicated seconds type in the coarse set.

## Contract ↔ matview reconciliation rules (feed DQ.4.2)

The DatasetContract is NOT equal to the emitted set — DQ.4.2's registry-derivation reconciles each with an explicit ± rule, so a rename fails at construction against the emitted truth:

- **`limit_breach`** — contract = emitted 9 **+** `account_display` (computed in `build_limit_breach_dataset`); money cols re-typed cents→dollars.
- **`stuck_pending` / `stuck_unbundled`** — contract = emitted 12 **+** `*_aging_bucket` (CASE over `age_seconds` at the dataset SELECT).
- **`daily_statement_summary`** — emitted 18 is a **superset**; the contract reads only the first 15 (drops `closing_balance_source`, `closing_carried_from_date`, `opening_balance_source`).
- **`l1_exceptions`** — three deltas: emitted `seq` (ROW_NUMBER disambiguator) is unread by the contract; contract's `account_display` is dataset-computed (not emitted); contract lacks `account_scope`. Neither is a clean subset — needs add AND drop. `magnitude_amount` emitted raw CENTS vs contract DECIMAL.
- **`inv_money_trail_edges`** — emitted 13 is a **superset** by `edge_seq` (synthetic ROW_NUMBER for the UNIQUE index / PG REFRESH CONCURRENTLY). `hop_amount` CENTS vs contract DECIMAL.
- **`inv_pair_rolling_anomalies`** — names/order match `VOLUME_ANOMALIES_CONTRACT` 1:1 but storage/currency diverge (see #3/#4). Separately, `RECIPIENT_FANOUT_CONTRACT` reads a DIFFERENT subset query over the same matview — a second reader.
- **`v_config_transfer_templates`** — emitted (`name`, `expected_net`, `completion`) is INTENTIONALLY narrower than the full `TransferTemplate` (array fields `leg_rails` etc. deliberately not projected — ORA-40597 path-length cap, BS.1). Record the narrowing so it doesn't read as a regression.

## Shape notes

Drill-eligible columns carry a `ColumnShape` per the reading contract: `account_id`/`*_account_id` → `ACCOUNT_ID`; `business_day*` → `DATETIME_DAY`; `rail_name` → `RAIL_NAME`; `transfer_id`/`root_transfer_id` → `TRANSFER_ID`; `account_display` → `ACCOUNT_DISPLAY`. Non-drill columns stay `shape=None`.

## Validation

The declarations are guarded by a runtime test (DQ.4.1): apply the schema to an in-memory DuckDB, introspect each object's ACTUAL emitted columns (name + order + coarse type), and assert `DbObject.columns` matches — catching any recon/transcription error against the real emitted schema, not against a re-read of the SQL text. The shape/currency/storage annotations cross-check against the DatasetContracts where columns overlap.

## DQ.4.2 — contract ↔ matview reconciliation (design + the landed gate)

From the 43-contract recon (workflow `wadhrl1op`): every `DatasetContract` is a DOWNSTREAM dataset projection over one or more matviews. The reconciliation RULE for a column shared (by name) between a contract and a source matview's `DbObject.columns`:

- **STRICT (fails the build):** STORAGE — a contract money column must be `DOLLARS` (the dataset SQL pre-divides via `cents_to_dollars_sql`); a `CENTS` leak is the BG.7 100x render bug. Money source (`CENTS`) → contract `DOLLARS` is the allowed, documented widen. Coarse TYPE — equal, modulo the money widen `INTEGER/CENTS → DECIMAL/DOLLARS`.
- **ADVISORY (enrichment-compatible, non-gating):** SHAPE — a contract MAY enrich a drill shape onto a `None`-source (Current*/base tables carry no drill shape; the reading contract adds `ACCOUNT_ID` / `RAIL_NAME` / `DATETIME_DAY`). Allowed iff `source.shape is None`, `contract.shape is None`, or `source.shape.can_assign_to(contract.shape)`. CURRENCY — the house convention is `currency=False` on every contract money column (SQL owns `$`-formatting), so it's non-gating.

This is why the recon's 16 flagged "inconsistencies" are NOT `_COLUMNS` bugs — 15 are legitimate shape-enrichment over `None`-sources, 1 is the currency house-pattern. **Zero annotation fixes.**

**Mechanism (two parts):**
- **B — reconciliation gate (LANDED, `test_dq4_2_contract_reconciliation.py`).** Builds every dataset via the apps' `build_all_*`, DERIVES each source matview from the dataset's actual SQL (`FROM`/`JOIN` of a graph object — no parallel list), and asserts the rule for all 263 shared columns across 57 sourced datasets. A planted CENTS-leak fixture proves the storage gate fires. Validates the shape/currency/storage annotations the DuckDB-introspection test (DQ.4.1) can't see.
- **A — the rename GATE (follow-on, folds with the DQ.4.4 literal-collapse).** A `contract_from(*sources, keep=[...], dollars=[...], add=[...], reshape={...})` builder (new `common/contracts.py`, imports both `dataset_contract` + `db_objects` — NOT the reverse, no cycle) resolves `keep`/`dollars` via `SCHEMA_GRAPH[obj][name]` → **KeyError at import on a matview rename** (the invariants-in-types gate). Migrate the ~18 clean single-source contracts to it, one per commit, byte-identity asserted. B stays the universal backstop for the JOIN/compute contracts.

**Operator flag (non-gating):** `EXC_DEAD_LIMIT_SCHEDULES.cap` flips `currency` True(source)→False(contract) with NO `/100` (same DOLLARS storage). Either the blanket contract-money `currency=False` convention is a latent formatting smell across ALL money columns, or `cap` specifically should render as `$`. Surfaced for chotchki; the automated gate treats currency as advisory so it blocks neither.

## DQ.4.1b — the `emit_create` carve (per-object bodies, graph-ordered)

The two monolithic matview templates (`_L1_INVARIANT_VIEWS_TEMPLATE` ~1300 lines / 20 objects, `_INV_MATVIEWS_TEMPLATE` ~320 lines / 2 objects) each rendered via ONE `.format()` — a second CREATE-order surface that could drift from the graph's refresh/drop lists. DQ.4.1b carves them into per-object bodies so `emit_schema` (and the DuckDB table-refresh, which re-emits every matview-as-table) walk `SCHEMA_GRAPH.create_order(group)` and emit each object's slice via `DbObject.emit_create`. Create is now the **fifth derived order** — the forward graph walk, exact reverse of drop.

**Carve mechanism — byte-safe by construction.** `_split_matview_template` cuts each template at the line that starts each object's CREATE (the `{matview_create_kw} {p}_<obj>{matview_options}` header, or the rendered-section placeholder for the 5 helper-built bodies), pulling each object's leading `--` comment block into its own reorderable chunk. A load-time assertion proves `header + "".join(chunks) == template` — so the carve itself changes zero bytes; the ONLY observable delta is whatever the graph create-walk reorders. The per-chunk `.format(**fmt)` is equivalent to the old whole-template format because no `{...}` placeholder spans a line boundary.

**The one intentional reorder — `expected_eod_balance_breach` before `balance_cadence_gap`.** These two matviews are mutually independent (neither reads the other; both read only `current_daily_balances` / `current_transactions`), so their relative CREATE slot was always free — and the template happened to fix it OPPOSITE to the graph's refresh/drop order. Routing CREATE through the graph canonicalizes it: create now matches refresh and is the exact reverse of drop. Verified against the byte-golden as a **pure reorder** — identical sorted line-multisets across all 3 dialects' `emit_schema` + the DuckDB refresh (the 4 body-emitting outputs); PG/Oracle `REFRESH` and all 3 `emit_schema_drop_sql` stay byte-identical. Semantic-lock (violation sets) unchanged — a CREATE-order swap of independent matviews is data-invariant (confirmed by `test_spine_at4_l2_semantic_lock`). Pinned permanently by `test_dq4_1b_matview_create_order_equals_the_graph` (matview CREATE subsequence == `SCHEMA_GRAPH.matviews()` order, all 3 dialects).

Base tables + Current* + `v_config_*` views stay template-authored (`_SCHEMA_TEMPLATE` / `_emit_typed_config_view_creates`) — DQ.4.1b scoped to the L1 + Inv matview bodies (the two big `.format()` blocks). Their CREATE order is still graph-topo-VALID (guarded by `test_dq2_emit_schema_create_order_respects_the_graph`), just not carved per-object.
