# CQ.2 — Server-Side Typeahead Design

## Operator constraint (locked 2026-06-08)

> "truncating at 2000 rows SUCKS in production. That is NOT the right approach and we shouldn't even have it as a fallback. We must do server side querying."

`_OPTIONS_CAP=2000` is deleted outright. Server-side typeahead is THE answer, not a fallback. This design covers App2; the QS leg lights up for free once CQ.4 de-parameterizes `DS_L1_DS_ACCOUNTS` (see below).

## Headline approach

- **Seed page**: top-100 alphabetical via `preload: 'focus'` (Tom Select fires one `load('')` on first picker focus). The server returns the same shape as a typed search with empty `q`. No SSR pre-fill; render-time DISTINCT-per-spec disappears, which is itself a perf win on multi-picker sheets (Investigation has 4-5).
- **Search semantics**: substring (`'%' || :q || '%'`), not prefix. Operators search account-name middles more than starts ("find the merchant DDA with 'acme' in it"). The wrapped-subquery shape defeats btree pushdown either way, so the perf delta over prefix is small.
- **Transport**: parallel JSON endpoint at `dropdown-search/{dataset}/{column}` — keeps cascade HTML (`dropdown-options/...`) and typeahead JSON consumers separate. Tom Select's built-in `loadThrottle: 300` debounces; HTMX is not in the typeahead path.

## API contract

```
GET /dashboards/{dashboard_id}/sheets/{sheet_id}/dropdown-search/{dataset}/{column}?q=<typed>&<form-state>
→ 200 application/json: {"options": [{"value": "...", "label": "..."}, ...]}  // capped LIMIT 100
```

Empty `q` returns the seed page. `param_<name>` form state threads through for cascade narrowing. The HTML cascade route is unchanged.

## SQL templates (per dialect)

Two new helpers in `common/sql/dialect.py`:

```python
def case_insensitive_substring_match(col, bind, dialect):
    if dialect is Dialect.ORACLE:
        return f"UPPER({col}) LIKE '%' || UPPER(:{bind}) || '%' ESCAPE '\\\\'"
    return f"{col} ILIKE '%' || :{bind} || '%' ESCAPE '\\\\'"

def escape_like_pattern(s):  # escape `\` FIRST, then `%` and `_`
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

Builder in `_tree_fetcher.py`:

```sql
SELECT DISTINCT {col_ref} AS opt
FROM ({base_sql}) opt_src
WHERE {col_ref} IS NOT NULL
  AND {case_insensitive_substring_match(...)}
ORDER BY 1 {LIMIT 100 | FETCH FIRST 100 ROWS ONLY}
```

`:q` binds through the existing `_sql_executor._prepare_sql_and_binds` pipeline — PG gets `%(q)s`, DuckDB gets `$q`, Oracle/SQLite keep `:q`. **NOT** `<<$pX>>` (that's QS-substitution machinery; it bypasses URL-input defenses). DuckDB collapses into the PG branch — both speak ILIKE. The user-typed string is pre-escaped via `escape_like_pattern` BEFORE binding so `5%` doesn't match every row containing `5`.

## Sticky selection is free

Tom Select's `load(query, callback)` is MERGE semantics, not replace. `loadCallback` calls `setupOptions` → `addOptions` → per-option `addOption`, whose docstring says *"If it already exists, nothing will happen."* So:

1. Server renders the `<select>` with the currently-bound value as `<option selected value="X">X</option>` (one `<option>` only — no eager DISTINCT).
2. Tom Select wires, picks up the pre-selected option into `this.items`.
3. User types "Y". `load('Y', cb)` fires. Server returns Y-matched options. Tom Select MERGES them in — "Account X" stays in `this.options` AND in `this.items`. The dropdown displays only Y-matches (sifter), but X remains the bound value.

`clearOptions()` is selection-safe by design via Tom Select's `clearFilter`: it keeps any option referenced in `this.items`. No explicit cleanup needed.

## File changes

| File | Change |
|---|---|
| `common/sql/dialect.py` | Add `case_insensitive_substring_match(col, bind, dialect)` + `escape_like_pattern(s)` |
| `common/html/_tree_fetcher.py` | Delete `_OPTIONS_CAP=2000`; add `account_picker_search_sql(...)` + `make_options_search_fetcher(cfg, pool)`; revise `make_options_fetcher` to return selected-only (no eager DISTINCT) |
| `common/html/server.py` | Add `OptionsSearchFetcher` type alias + `ServedDashboard.options_search_fetcher` field; register `dropdown-search/{dataset}/{column}` JSON route; `_resolve_linked_options` skips eager DISTINCT for LinkedValues |
| `common/html/render.py` | LinkedValues `_render_parameter_dropdown` / `_render_parameter_multiselect` emit only selected `<option>` plus `data-typeahead=1` + `data-typeahead-url=<search route>` |
| `common/html/assets/js/bootstrap.js::wireTomSelect` | Branch on `data-typeahead==='1'`: pass `load(query, callback)` fetching JSON; `preload: 'focus'`, `loadThrottle: 300`, `maxOptions: 100`, `shouldLoad: q => q.length === 0 || q.length >= 2`, `searchField: []` (server narrows; don't double-filter) |
| `cli/_html_serve.py` | Instantiate `search_fetcher = make_options_search_fetcher(...)`; thread into every `ServedDashboard` |
| `tests/e2e/_harness_html2.py` | Mirror — `make_live_db_fetchers_for_app` returns 3-tuple; update call sites |
| `tests/e2e/_drivers/app2.py` | Rewrite `filter_options` + `pick_filter` to drive typeahead (type, await `load` population, click matching item) |
| `common/browser/helpers.py` | App2 typeahead helper paralleling QS `set_dropdown_value` (CQ.2.f) |
| `docs/reference/quicksight-quirks.md` | Append entry: "App2 typeahead matches QS native (post-CQ.4)" — closes renderer-divergence note |

## QS leg — closed by CQ.4

The 7 wide L1 account pickers already get native server-side typeahead via `GetUniqueAttributeValuesSyncForAnalysis`. The endpoint 400s on parameterized datasets; CQ.4 drops `pL1DsRole` so `DS_L1_DS_ACCOUNTS` becomes unparameterized — exactly the precondition that unblocks it. Zero deploy-time config needed; QS auto-enables the MUI Autocomplete search variant based on (undocumented) cardinality threshold. App2 + QS converge on the same UX: type, server narrows, picker shows up to ~100 results.

## Test strategy

**Unit (`tests/unit/`):**
- `escape_like_pattern` round-trips `%`, `_`, `\` correctly (order: backslash first).
- `case_insensitive_substring_match` golden output per dialect (PG/DuckDB collapse to ILIKE branch, Oracle gets UPPER+LIKE).
- `account_picker_search_sql` produces dialect-correct SQL via `column_name`; LIMIT clause is `LIMIT 100` (PG/DuckDB) or `FETCH FIRST 100 ROWS ONLY` (Oracle).
- LIKE-injection regression: bind `5%` and assert the bound value is `5\%` (escaped), not raw.
- AST smell: forbid raw `f"... LIKE '%{user_input}%'"` patterns in `_tree_fetcher.py` / `server.py`.

**DB-layer (per dialect, `tests/data/`):**
- Substring match returns ≤100 rows against seed-densified fixture; empty `q` returns top-100 alphabetical.
- Cascade form-state narrows correctly when threaded through `param_<name>`.
- Perf gate: capture P95 in `runs/<run-id>/db-perf/top-queries.md` at `--seed-density=10` (~50k accounts); alert if P95 > 150ms PG/Oracle or > 50ms DuckDB.

**E2E browser (`tests/e2e/`):**
- App2Driver `filter_options` / `pick_filter` rewrites are themselves the gate — every existing browser test exercises the new wire shape.
- New `test_cq2_typeahead.py`: (1) seed page on first focus; (2) typing 'acme' narrows; (3) sticky selection — pick, type unrelated query, original selection still bound; (4) debounce — rapid typing fires one network request; (5) cascade swap → next focus fires fresh `load('')`.
- CQ.2.f parametrized [qs, app2] test of `set_picker_value(name, value)` proves both renderers narrow server-side. QS leg gates on CQ.4 having landed.

## Defaults picked (open questions from synthesis)

These were flagged as "open operator questions" in the synthesis output. Taking sane defaults (operator can redirect):

| Question | Default chosen | Rationale |
|---|---|---|
| Cascade vs typeahead URL merge | Keep separate (cascade HTML route + typeahead JSON route) | Different consumers (HTMX vs Tom Select), different LIMIT semantics, different transports. Revisit after CQ.2.f if duplication shows. |
| Multi-select picker UX | Merge-semantic re-pick (default Tom Select behavior) | Cheap re-pick of recently-seen values matches the existing UX; no `closeAfterSelect` flip. |
| Seed-page ordering | Top-100 alphabetical | Default; revisit if operator wants "recently used" / "most active". |
| Matview-direct fetcher variant | Defer until measured | Ship the wrap; only escalate if P95 > 150ms. |
| Min-length gate | 2 chars (or empty for seed) | Single-letter against 50k accounts returns the LIMIT cap on the first letter, wasting the round-trip. |

## Risks + mitigations

- **LIKE-pattern correctness**: user typing `5%` matches every row containing `5`. Mitigation: `escape_like_pattern` baked into the helper layer; `ESCAPE '\'` on every LIKE; cap input at 100 chars before binding.
- **Perf at 50k accounts**: wrapped-subquery defeats functional-index pushdown. Mitigation: `loadThrottle: 300` + `shouldLoad: q => q.length >= 2`; LIMIT inside the wrapped query; matview-direct path queued for follow-up if measured P95 too high.
- **Dialect divergence**: DuckDB rejects `:name`, Oracle has no ILIKE. Mitigation: route every bind through `_sql_executor._prepare_sql_and_binds` (handles paramstyle); per-dialect helper handles ILIKE vs UPPER+LIKE; `column_name(col, dialect)` for Oracle case-correctness.
- **Test-side surface area**: App2Driver `filter_options` / `pick_filter` read static `<option>` DOM today — incompatible with lazy load. Mitigation: rewrite both to drive typeahead. This IS CQ.2.f's deliverable.
- **QS dependency on CQ.4**: native typeahead requires unparameterized source. Mitigation: CQ.4 is the precondition; CQ.2 ships App2-side; QS lights up automatically post-CQ.4.

## Backlog spawned

- **Matview-direct picker fetcher** (`account_picker_search_sql_matview(matview_name, col)` — skip the dataset-wrap subquery) — only escalate if measured P95 > 150ms.
- **QS cardinality-threshold pinning** — post-CQ.4, verify whether sasquatch_pr's account count crosses the (undocumented) MUI Autocomplete threshold. Not a blocker; flag for CQ.5 verify.
