# Oracle 19c constraints (and the layers we built to work around them)

This page captures Oracle 19c-specific limitations that shaped the
schema codegen, plus the trigger conditions for deleting the
workarounds when the LTS floor moves forward. Sibling to
`docs/reference/quicksight-quirks.md`; same "ever-growing log" stance
per the `feedback_quirks_log_ever_growing` memory.

## Why we support Oracle 19c

Midsize financial institutions in the U.S. run Oracle 19c LTS — it's
the oldest supported LTS line as of this writing (long-term premier
support through April 2027; extended through April 2032). Dropping it
forces customer-side upgrade conversations; per the
`project_oracle_19c_compat` memory the demo SQL must port to the more
conservative engine that midsize ops teams actually deploy. Future
moves to 21c+ (native JSON column type, JSON_TABLE matview-friendly)
unlock simpler shapes — track each affected layer below for the
deletion path.

## Active workarounds (BC.12, 2026-05-24)

### `oracledb` bind scanner trips on `:MI` / `:SS` inside TO_DATE format literals (BM.5, 2026-05-28)

**Constraint:** the `oracledb` driver scans pre-execution SQL for
`:name` tokens to identify bind variables. Its scanner does NOT
respect SQL string-literal quoting — `TO_DATE(:p, 'YYYY-MM-DD"T"
HH24:MI:SS')` makes the driver see `:MI` and `:SS` inside the
single-quoted format literal as undeclared binds and rejects the
query with `DPY-4008: no bind placeholder named ":MI" was found`.

**Workaround layer:** the BM `universal_date_range_clause` helper
sidesteps the issue with `TO_DATE(SUBSTR(<param>, 1, 10),
'YYYY-MM-DD')` — chops the `YYYY-MM-DD` prefix off either input
shape (`'2026-05-20'` or `'2026-05-20T00:00:00'`) and parses with
a colon-free format string. The picker is day-aligned
(`TimeGranularity="DAY"`) so the sub-day precision the SUBSTR drops
carries no meaning for narrowing; day-inclusive `+ 1` (Oracle DATE
arithmetic) lands the upper bound on day-after-end's midnight.

**Cost:** the SUBSTR + colon-free format adds a small functional
overhead per row evaluation vs a direct format match. Negligible on
the matview row counts the date filter narrows (≤6 figures); would
need re-evaluation if the same shape ever runs against
hundreds-of-millions-of-rows tables.

### `<prefix>_config_kv` flattened JSON tree (BC.12)

**Constraint:** **ORA-32368: cannot create JSON materialized view
without relational table.** Oracle 19c+ refuses to build a
materialized view whose source contains `JSON_TABLE(<CLOB>, ...)`. The
matview engine demands a "relational" source — JSON_TABLE-of-CLOB
counts as non-relational even though the projection columns are
relational. Reproduces on Oracle 23ai (the test container at port
60643, image `gvenzl/oracle-free:23-faststart`); the constraint is
older than 19c and isn't going to be relaxed on the 19c branch.

**Workaround layer:** the `<prefix>_config` table's pre-BC.12 shape
`(as_of TIMESTAMP, cfg_yaml TEXT/CLOB, l2_yaml TEXT/CLOB)` is replaced
by a flattened EAV table `<prefix>_config_kv(node_id BIGINT,
parent_id BIGINT, key VARCHAR(255), value TEXT/CLOB)` populated by a
Python tree-walker at deploy time. Typed projection views
(`<prefix>_v_config_rails`, `<prefix>_v_config_limit_schedules`) emit
alongside the matviews and project the kv leaves into typed VARCHAR /
BIGINT / NUMERIC columns via self-joins on `parent_id`. Matviews
JOIN the typed views — Oracle sees a fully relational source and
builds without ORA-32368.

**Cost:** every dialect pays the EAV tax (more rows, more JOINs in
the view body) even though PG 17+ and SQLite 3.38+ would happily
JSON_TABLE the CLOB directly. The cost is paid for portability — one
generator output runs on every supported dialect.

**Trigger to delete:** Oracle 19c falls off our support floor AND
every supported Oracle is 21c+. Then:

1. Replace `<prefix>_config_kv` with the original 3-column
   `<prefix>_config(as_of, cfg_yaml, l2_yaml)`.
2. Delete `_emit_typed_config_view_creates` /
   `_render_v_config_rails` / `_render_v_config_limit_schedules`
   from `common/l2/schema.py`.
3. Restore the AW.3/AW.4-era `json_array_iterate` + `json_field_extract`
   patterns in `_emit_l1_invariant_views`. The helpers are still
   present (`common/sql/dialect.py`) — they just lost their callers
   in BC.12.
4. Delete `kv_as_of_as_timestamp_sql` / `kv_as_of_subquery` /
   `kv_root_id_for` / `kv_rows_for` from `common/l2/config_table.py`.
5. Re-run the spike against the new Oracle floor (`docs/audits/bc_12_
   config_kv_spike.md`) to confirm.

The BC.12 brief was the locked design lock-in for the EAV approach;
the spike doc lives alongside as the architectural rationale.

### `DBMS_LOB.SUBSTR` CLOB coercion (BC.12)

**Constraint:** **ORA-22849: Type CLOB is not supported for this
function or operator.** Oracle's aggregate functions (`MAX`, `MIN`,
`GROUP BY`, etc.) reject CLOB-typed expressions. The typed projection
views aggregate by `parent_id` to pivot multi-row "fields of one
element" patterns into single rows; with CLOB values, the
straightforward `MAX(CASE WHEN k='name' THEN value END)` fails.

**Workaround:** `lob_substr(expr, n, dialect)` in `common/sql/dialect.py`
wraps the CLOB read in `DBMS_LOB.SUBSTR(value, n, 1)` on Oracle (PG
gets `SUBSTRING(expr FROM 1 FOR n)`, SQLite gets `SUBSTR(expr, 1, n)`).
The matview-consumed leaf fields all fit in 100 chars or less, so the
truncation never bites in practice; the cap is just whatever number
keeps the result inside Oracle's VARCHAR2 cap (4000).

**Trigger to delete:** Oracle's MAX accepts CLOB natively (it
doesn't, last we checked, on 19c through 23c). Independent of the
BC.12 deletion trigger above — the helper would still be useful any
time you aggregate a TEXT/CLOB column on Oracle.

### `TO_TIMESTAMP(DBMS_LOB.SUBSTR(value, 100, 1), 'YYYY-MM-DD HH24:MI:SS')` (BC.12)

**Constraint:** **ORA-00932: expression is of data type CLOB, which
is incompatible with expected data type TIMESTAMP.** `CAST(<clob>
AS TIMESTAMP)` is rejected on Oracle 19c+ — CAST coerces VARCHAR2 to
TIMESTAMP (with an implicit `TO_TIMESTAMP`), but doesn't accept CLOB.

**Workaround:** `kv_as_of_as_timestamp_sql(prefix, dialect)` in
`common/l2/config_table.py` projects the kv `as_of` row's value as
TIMESTAMP via `TO_TIMESTAMP(DBMS_LOB.SUBSTR(value, 100, 1),
'YYYY-MM-DD HH24:MI:SS')` on Oracle, plain `CAST(... AS TIMESTAMP)`
on PG, and bare text passthrough on SQLite (where `julianday(text)`
accepts ISO-format strings natively).

**Trigger to delete:** same as BC.12's main trigger.

### Long-literal chunking via `TO_CLOB(c1) || TO_CLOB(c2) || ...` (BC.12)

**Constraint:** **ORA-01704: string literal too long.** Oracle SQL
literals max out at 4000 bytes per quoted segment. PG + SQLite accept
multi-megabyte literals directly.

**Workaround:** `_sql_quote_long` in `common/l2/config_table.py`
splits values above 4000 chars into 4000-byte chunks joined by
`TO_CLOB(chunk) || TO_CLOB(chunk) || ...` (the CLOB-concat result
has no length cap). Inert below the threshold — the path is only
exercised by the deferred `l2_yaml_raw` opaque-provenance row (which
BC.12 deferred-out; see backlog).

**Trigger to delete:** Oracle relaxes the literal cap (it hasn't
through 23c). The chunking is dialect-blind in callers, so removing
it is mechanical.

### Quote-aware SQL splitter (BC.12)

**Constraint:** the per-statement Oracle driver (`oracledb`) requires
single-statement input; the SQL script splitter splits on `;`. Pre-
BC.12 the splitter was line-by-line and didn't track string state,
so a `;` embedded inside a multi-line literal false-split the
statement. Most generator code never produced multi-line literals;
BC.12's kv populate (with full L2 descriptions) does.

**Workaround:** `_split_oracle_script_impl` in `common/db.py` now
tracks single-quote toggle state across lines; inside a string,
`;` is not a terminator, `--` is not a comment, and `BEGIN`/`END`
are not PL/SQL keywords. SQL's doubled-quote escape (`''`) handles
naturally because two toggles cancel.

**Trigger to delete:** never — this is a correctness fix, not a
workaround for an Oracle constraint per se. Keep the quote-aware
splitter even after Oracle 19c falls off.

## Closed workarounds (parked + documented for archeology)

(None yet — first BC.12 round.)
