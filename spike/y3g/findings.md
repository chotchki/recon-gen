# Y.3.g spike findings — SQLAlchemy Core vs sqlglot for dataset SQL emission

**Date:** 2026-05-09
**Branch:** `y-3-g-sql-builder-spike`
**Premise:** Y.3.f.2 revert showed the case-bridging architecture is more
load-bearing than expected. Does adopting a SQL builder library
absorb the cross-dialect tax that's accumulating in
`common/sql/dialect.py`?

## Method

Ported the Account Network dataset SQL (CTE + display-string concats +
parameter-substituted WHERE clauses) via two libraries with different
mental models:

- **SQLAlchemy Core 2.0.49** — expression-tree-build. You construct a
  Python object graph; `.compile(dialect=...)` emits per-dialect SQL.
- **sqlglot 30.7.0** — parse + transpile. Two modes: (A) write SQL
  once for one dialect → parse to AST → transpile to other dialects;
  (B) construct via `sqlglot.exp.Select / With / ...` expression API
  similar to SA.

Also ran a "harder cases" probe on sqlglot transpile to test
dialect-edge coverage: SQL/JSON path extraction, recursive CTE, date
arithmetic — the constructs where our existing dialect helpers do the
real cross-dialect work.

Files:
- `capture_baseline.py` — runs the current emitter, dumps to `baseline/<dialect>.sql`
- `account_network_sa.py` — SA Core port, dumps to `sa-output/<dialect>.sql`
- `account_network_sg.py` — sqlglot transpile + build, dumps to `sg-output/{transpile,build}/<dialect>.sql`
- `json_path_sg.py` — sqlglot dialect-edge probes (output to stdout only)

## Results

### Account Network port — char counts per dialect

| | Postgres | Oracle | SQLite |
|---|---|---|---|
| Current emitter (baseline) | 412 / 14L | **1106** / 16L | 412 / 14L |
| SA Core | 390 / 6L | 387 / 6L | 321 / 6L |
| sqlglot transpile | 422 / 17L | 418 / 16L | 421 / 16L |
| sqlglot build API | 416 / 16L | 416 / 15L | 415 / 15L |

(Oracle baseline bloat is the `_oracle_lowercase_alias_wrapper`.)

### Library mental models

| | SA Core | sqlglot transpile | sqlglot build |
|---|---|---|---|
| Source of truth | Python expression tree | One SQL string (PG) | Python expression tree |
| Mental model | "Build query as objects" | "Write SQL once, transpile" | "Build query as AST nodes" |
| Lines of Python per dataset | ~50 | ~5 (preprocess + call + postprocess) | ~50 |
| Lines of source SQL | 0 (all Python) | ~14 (PG-style, our existing) | 0 (all Python) |
| Readability for SQL-fluent | Lower | **High** (it's just SQL) | Lower |
| Per-dialect bind syntax | Uniform `:name` (with paramstyle override) | PG `%(name)s`, others `:name` | Same as transpile |
| Dependency weight | ~5MB (SA + greenlet + typing-extensions) | ~3MB (sqlglot, pure Python) | Same |

### Dialect-edge probe results (sqlglot transpile only)

✅ **CTE + concat + WHERE + parameters** — works cleanly on all three dialects.

✅ **Oracle `AS` keyword** — sqlglot correctly omits `AS` between table
and alias for Oracle (preserves it for PG/SQLite).

✅ **PG date arithmetic** — `INTERVAL '7 day'` correctly transpiled to
Oracle's `INTERVAL '7' DAY`.

⚠️ **Oracle `WITH RECURSIVE`** — sqlglot keeps `RECURSIVE` keyword for
Oracle. Oracle 19c accepts it but earlier versions don't. Our current
helper drops the keyword for Oracle to maximize portability. Minor
issue; can post-process.

⚠️ **SQL/JSON path extraction — partial coverage, Oracle is broken.**
First probe used `JSON_VALUE` from PG source (PG doesn't have it
natively) — sqlglot left it verbatim. Re-probe with proper sources
(`json_recheck_sg.py`):
- `JSON_EXTRACT` (sqlglot canonical) source → PG correctly emits
  `JSON_EXTRACT_PATH(metadata, 'x')`; SQLite emits `metadata -> '$.x'`
  (correct).
- → Oracle emits `JSON_EXTRACT(metadata, '$.x')` — **Oracle has no
  `JSON_EXTRACT` function**; this SQL fails to parse. Oracle uses
  `JSON_VALUE` (SQL/JSON standard, also PG 12+).
- PG `->>` operator → Oracle emits `JSON_EXTRACT_SCALAR` (a BigQuery
  function, not Oracle). Wrong.
- Oracle `JSON_VALUE` source → not recognized as JSON-path extraction;
  passes through verbatim to all targets (incorrect for SQLite).

Net: sqlglot recognizes some JSON path forms but its Oracle output
is wrong for our use case. We'd need a custom transformer to fix
Oracle (basically reinventing our `json_value` helper inside sqlglot's
framework).

❌ **SQLite date arithmetic** — sqlglot kept `INTERVAL '7' DAY` for
SQLite output. **SQLite has no INTERVAL type**; our `date_minus_days`
helper produces `date(expr, '-7 days')`. The transpiled SQL would
fail at parse time on SQLite.

### Both libraries: do not solve the Y.3.f problem

❌ **Neither solves the case-bridging architecture.** Both libraries
emit column references in whatever case we declare them. The
`_oracle_lowercase_alias_wrapper` exists because QuickSight's
analysis-side validation is case-sensitive against Dataset.Columns.
That's an AnalysisDefinition concern, not a SQL-emit concern. Either
library leaves us with the same fold-everywhere-or-keep-the-wrapper
choice.

❌ **Calc field expressions live outside dataset SQL.** Both libraries
ignore them. The 30+ analysis-side ref sites that broke Oracle deploy
in Y.3.f.2 stay broken without a separate fix.

## Decision

**STAY on strings + helpers for now. Re-narrow Y.3.f to App2-only.**

Rationale (refined after both libraries evaluated):

1. **sqlglot transpile is the most attractive option** for the common
   case (CTE + concat + WHERE), with ~5 lines of Python overhead per
   dataset and SQL that stays as readable PG strings in source. **But
   the SQLite gaps are showstoppers** — SQL/JSON dialect bridging and
   date arithmetic both produce invalid SQL on SQLite. We'd still need
   helpers OR per-dialect manual overrides for those cases. That's
   "best of both worlds" only on paper; in practice it's "two systems
   to maintain, both partial."

2. **SA Core eliminates the per-construct branching** more
   thoroughly than sqlglot for the common case, but at higher
   per-dataset Python cost (~50 lines vs current ~20) and lower
   reviewer ergonomics (expression API vs SQL strings). Migration cost
   for 5500 LOC is significant.

3. **Neither library moves the needle on Y.3.f.** The case-bridging
   architecture is orthogonal to SQL emission. We'd be adopting a
   builder library for cosmetic improvements while leaving the actual
   blocker in place.

4. **The original m.5.d App2 bug** — the trigger for Y.3.f — remains
   unblocked. App2-only fix (quote App2's column refs in
   `wrap_for_visual`) is 1-2 file edits and unblocks the matrix.

5. **Both libraries stay in the toolbox.** sqlglot in particular is
   compelling enough that it's worth revisiting if/when the dialect-
   helper surface grows past ~60 helpers (currently ~40), or if we
   ever drop SQLite (which would eliminate the worst transpile gaps).

## Re-narrowed Y.3.f scope (post-spike, both libraries evaluated)

- **Y.3.f.alt.1** — Fix `common/html/_visual_sql.py::wrap_for_visual`
  to quote column refs (`"col"` instead of `col`). On Oracle the
  wrapper produces lowercase quoted aliases; quoted-lowercase refs
  preserve case → match. On PG, quoted lowercase refs match
  lowercase-stored DDL columns. Safe on all dialects.
- **Y.3.f.alt.2** — Same fix in `common/html/_tree_fetcher.py` if it
  also builds projections by string.
- **Y.3.f.alt.3** — Unit-test snapshot of `wrap_for_visual` output
  asserting quoted refs.
- **Y.3.f.alt.4** — Live verify locally: `--variants=sp_or_lo` goes
  green (the m.5.c-blocked or_lo cells unblock).
- **Y.3.f.alt.5** — Aurora verify: `--variants=sp_or_aw` (m.5.d AW
  chain unblock signal).
- **Y.3.f.alt.6** — Tick PLAN, commit, decide on release.

The wrapper stays. The `column_name(name, dialect)` helper from
Y.3.f.1 stays in `common/sql/dialect.py` — useful if/when we ever
revisit the case-folding architecture.

## When to revisit Y.3.g

Re-spike if:
- Dialect helper count grows past ~60 (currently ~40)
- We drop SQLite from the dialect matrix (eliminates the worst
  sqlglot transpile gaps)
- A future feature genuinely needs cross-dialect SQL transformation
  (e.g., a SQL-fixup pass for a new database backend)
- We adopt a write-heavy feature like a generic bulk-load SQL
  generator (SA Core's bulk-insert API would be a natural fit)

## Spike artifacts

- `capture_baseline.py` — runs current emitter, dumps SQL to `baseline/`
- `account_network_sa.py` — SA Core port, dumps to `sa-output/`
- `account_network_sg.py` — sqlglot transpile + build, dumps to `sg-output/`
- `json_path_sg.py` — dialect-edge probes (stdout only)
- `baseline/postgres.sql` (412 chars), `oracle.sql` (1106), `sqlite.sql` (412)
- `sa-output/postgres.sql` (390), `oracle.sql` (387), `sqlite.sql` (321)
- `sg-output/transpile/postgres.sql` (422), `oracle.sql` (418), `sqlite.sql` (421)
- `sg-output/build_via_api/postgres.sql` (416), `oracle.sql` (416), `sqlite.sql` (415)
- `findings.md` (this doc)

Throwaway. Delete `spike/y3g/` after Y.3.f.alt closes.
