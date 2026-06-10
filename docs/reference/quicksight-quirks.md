# QuickSight Quirks Log

Running log of QuickSight rendering / wire / behavior surprises this
codebase has bumped into. Per `feedback_quirks_log_ever_growing`: when
we discover a new QS bug or quirk, append an entry HERE as part of the
same fix branch — not as a follow-up. The log is append-only;
historical entries stay even after the underlying bug is fixed on
AWS's side so we can recognize the shape on the next regression.

## `CategoricalMeasureField(COUNT)` silently renders DISTINCT on a string column also used as a Dim — BL.1, 2026-05-27

### Symptom

A KPI bound to `ds["account_id"].count()` (which emitted
`CategoricalMeasureField(AggregationFunction="COUNT")`) returns the
distinct-value count of `account_id`, not the row count, whenever the
same `account_id` column is also used as a dimension elsewhere on the
same visual or sheet (e.g. a table below the KPI binds `account_id`
as a column).

Observed ratios on the bundled spec_example deploy: KPI shows ~138
when the matview has ~811 day-rows across ~138 distinct accounts;
209 actual rows vs 26 distinct accounts. Off by exactly the
distinct-vs-rows ratio.

### Confirmed-via

Five honest-gate KPI tests xfail'd citing BL.1:

- `tests/e2e/test_l1_filters.py::test_bg3_drift_sheet_kpis_match_matview_counts`
- `tests/e2e/test_l1_filters.py::test_bg6_todays_exceptions_kpi_matches_dataset_count`
- `tests/e2e/test_l1_filters.py::test_bg3_overdraft_kpi_matches_matview_count`
- `tests/e2e/test_l2ft_exceptions.py::test_bg6_l2ft_exceptions_kpi_matches_dataset_row_count`
- `tests/e2e/test_inv_filters.py::test_bg4_recipient_fanout_kpis_match_inflows_only_truth`

All had "App2 leg passes (raw `COUNT(column)`)" + "QS leg renders
distinct count." Same shape across all five — single root.

### Workaround

Don't emit `CategoricalMeasureField(COUNT)` for row-count semantics.
Two safe alternatives — both implemented in `Measure.kind == "count"`
as of BL.1:

1. **`NumericalMeasureField(SUM)` over a literal-1 `CalcField`**. QS
   wire is pure numerical aggregation; no string column appears in
   the COUNT slot so the distinct-quirk has nothing to trigger on.
   The CalcField is auto-registered on the Analysis (one per
   `Dataset` referenced by a count Measure) by
   `App.resolve_auto_ids`; the convention name is
   `_row_one_<sanitized-dataset-id>`. `App2`'s `_visual_sql` emits
   `SUM(1)` for `kind == "count"` to stay symmetric with the QS
   wire.
2. **Use `.distinct_count()` if DISTINCT is what you actually want.**
   The original `.count()` semantic became ambiguous; if a callsite
   wants distinct-of-column behavior, the explicit method makes
   the intent visible.

Reference implementations: `common/tree/fields.py::Measure.emit`
(QS-side wire) and `common/html/_visual_sql.py::_measure_sql`
(App2-side SQL). `App.resolve_auto_ids` auto-registration:
`common/tree/structure.py`.

### Notes

The DISTINCT-rendering behavior is undocumented. The same wire shape
on a *numeric* column appears to render row count correctly — the
quirk seems specific to string columns that double as Dims. We did
not search for a config flag to opt out; the CalcField-1 fix is the
operational answer regardless of what the underlying flag would be.

## `TimeRangeFilter(time_granularity="DAY")` excludes upper-edge late-day rows — 2026-05-27 (DISSOLVED by Phase BM, 2026-05-27)

### Symptom (historical)

An analysis-level `TimeRangeFilter` with `time_granularity="DAY"` and
`IncludeMaximum=True` over a `TIMESTAMP` column was claimed (per the
codebase's `app2_date_filter` docstring) to truncate the column at
the day boundary during filter evaluation and INCLUDE the full upper-
edge day. App2's mirror clause uses `column < CAST(:date_to AS DATE)
+ 1 day` for the same effect.

Observation on bg5 Exec KPI parity:

- App2: `Total Transactions = 2392`, `Gross Money Moved = $4,779,478.81`
- QS:   `Total Transactions = 2390`, `Gross Money Moved = $4,771,878.81`

The 2 missing transactions and $7,600 delta were 2030-01-01 rows with
`posting` > 00:00:00 — late-day timestamps that fall on the window's
upper edge. App2 included them; QS excluded them. So QS's behavior
was effectively `column <= '2030-01-01 00:00:00'` even with
`time_granularity="DAY"` + `IncludeMaximum=True`.

### Resolution — Phase BM (date-picker pushdown unification)

The second "operational fix candidate" below shipped. The pre-BM
dual-SQL form (analysis-level `TimeRangeFilter` on QS + `{date_filter}`
template on App2) dissolved in favor of unified `<<$pXxxDateStart>>`
/ `<<$pXxxDateEnd>>` dataset-SQL parameter pushdown via
`common/sql/app2_filters.py::universal_date_range_clause`. Both
renderers now run the same `column >= start AND column < end + 1 day`
shape against the same parameter values; the day-edge asymmetry
dissolves by construction. The two bg5 [qs] xfails (`test_bg5_*` in
`tests/e2e/test_exec_sheet_visuals.py`) were dropped at the same time.

### Notes (kept for historical context)

The asymmetry only mattered when the source column was a `TIMESTAMP`
with non-midnight values. Matview columns that are date-truncated at
create time (`business_day_start`, `posted_date`) didn't trip the
quirk — both renderers saw the same coarse-granularity values.
The transaction_summary case uses `t.posting` directly (raw
timestamp), which was the failure shape. Post-BM the same column
is narrowed via `TO_DATE(<<$pExecDateEnd>>, ...) + 1` on Oracle /
the equivalent on PG + SQLite, so late-day rows on the upper-edge
day are included on both renderers.

## Unmapped `DatasetParameter` is invisible to API + dashboard viewers, errors only in the analysis editor — BR.x, 2026-05-29

### Symptom

A dataset declares `DatasetParameters: [{Name: "pSomething", …}]` so
its CustomSql can substitute `<<$pSomething>>`. No analysis-side
`ParameterDeclaration.MappedDataSetParameters` bridges any analysis
parameter to it. The analysis deploys cleanly. `describe_analysis`
returns `Status: CREATION_SUCCESSFUL, Errors: []`. Same for
`describe_analysis_definition`. The published dashboard renders
cleanly for end users — a DOM dump of the deployed dashboard has
zero hits for any error keyword.

But open the **analysis editor URL** (`/sn/analyses/<id>` in the QS
console) and a banner appears: **"You have an unmapped dataset
parameter."** Downstream of that — if any `ParameterControl` with
`CascadingControlConfiguration` cascades on a control whose source
dataset has the unmapped param — the cascade target's dropdown
renders with a red error icon, tooltip: **"A calculated field
contains invalid syntax. Correct the syntax and try again."** The
"calculated field" wording is misleading: nothing in the analysis's
`CalculatedFields` list has invalid syntax. The error originates
from QS's internal expression evaluator failing on the unresolvable
substitution.

### Where the error does + does not surface

| Surface                                          | Banner?  | Cascade error? |
|--------------------------------------------------|----------|----------------|
| `describe_analysis` (boto3)                      | NO       | NO             |
| `describe_analysis_definition` (boto3)           | NO       | NO             |
| Published dashboard URL — end-user view          | NO       | NO             |
| Published dashboard URL — DOM dump               | NO       | NO             |
| Analysis editor URL (`/sn/analyses/<id>`)        | **YES**  | **YES**        |

Implication: every defensive layer except an analysis-editor browser
probe is blind to this class of bug. The deploy succeeds, the API
reports CREATION_SUCCESSFUL, the dashboard renders to viewers — and
the analyst who opens the analysis to make changes is the first to
hit the wall.

### Confirmed-via

- `tests/json/test_emit_cross_reference_consistency.py::test_dataset_parameters_are_bridged_from_analysis`
  walks every emitted dataset, collects declared `DatasetParameters`
  names, and asserts each one is bridged from at least one
  `ParameterDeclaration.MappedDataSetParameters` via the analysis's
  short-id (`DataSetIdentifierDeclarations[].Identifier`). Cheap,
  JSON-only, runs in the standard unit-tier matrix.
- A **browser-layer analysis-editor probe** that opens the analysis
  editor URL and asserts no "You have an unmapped dataset parameter"
  banner is on the backlog (need to wire the
  `generate_embed_url_for_registered_user` ExperienceConfiguration
  for QuickSightConsole + editor mode auth).

### Workaround / fix

Whenever a dataset's CustomSql contains `<<$pX>>` substitutions, the
dataset MUST declare `dataset_parameters=[…]` AND at least one
analysis-side `ParameterDeclaration` must bridge to it via
`mapped_dataset_params=[(dataset, "pX"), …]` on the analysis-side
`StringParam` / `DateTimeParam` / `IntegerParam`. The new unit test
gates against forgetting either side.

Same param with empty `default=[]` on the analysis-side `StringParam`
emits cleanly but cascade matching breaks the same way ("calculated
field has invalid syntax" on the dropdown). Always pass explicit
`default=[<sentinel>]` matching the dataset-side default.

### Browser-test selector (DOM-confirmed shape)

The error markers' visible text + their stable QS automation
attributes give two robust selector paths. QS class names rotate
between UI revs (`MuiAutocomplete-option-825` /
`css-896ft2-…erxi0re0`) so don't anchor on those; the
`data-automation-id` + `data-automation-context` pair + the literal
title string are the durable contract.

- **Analysis-editor banner** (analysis editor URL only, NOT
  dashboard): the literal string `"You have an unmapped dataset
  parameter."` appears in a banner near the sheet chrome.
- **Cascade-control red error icon** (both analysis editor AND
  dashboard, fires when the cascade tries to evaluate the unmapped
  param): the error tooltip is on a bare `<span title="…">` nested
  inside `[data-automation-id="sheet_control"]`. The
  `data-automation-context` attribute on the sheet_control names the
  parameter whose control errored (e.g. `Account`). The tooltip text
  is verbatim: `"A calculated field contains invalid syntax. Correct
  the syntax and try again."`

**DOM shape** (verified 2026-05-29, BR.x probe):

```html
<div class="sheet-control" data-automation-id="sheet_control"
     data-automation-context="Account">
  <div class="sheet-control-header">
    <div class="sheet-control-name"
         data-automation-id="sheet_control_name"
         data-automation-context="Account">
      <div style="display: flex;">
        <span class style="display: inline-grid;">…</span>
        <!-- ↓↓↓ this is the error marker — bare span, no class -->
        <span class title="A calculated field contains invalid
              syntax. Correct the syntax and try again.">…</span>
      </div>
    </div>
  </div>
</div>
```

Reference JS for a browser-layer assertion (gotcha: error text is on
the `title` ATTRIBUTE — `innerText` is empty, the prior version of
this probe missed every hit because it only checked visible text):

```javascript
() => {
    const out = [];
    // Cascade-control errors — nested span[title] on sheet_control
    document.querySelectorAll('[data-automation-id="sheet_control"]')
        .forEach(ctrl => {
            const param = ctrl.getAttribute('data-automation-context') || '';
            ctrl.querySelectorAll('span[title]').forEach(span => {
                const title = span.getAttribute('title') || '';
                if (title.includes('invalid syntax')
                    || title.includes('calculated field')) {
                    out.push({ kind: 'cascade_error', param, title });
                }
            });
        });
    // Analysis-editor banner — full-text scan since the banner DOM
    // doesn't have a stable automation-id.
    const banner = document.body.innerText || '';
    if (banner.includes('You have an unmapped dataset parameter')) {
        out.push({
            kind: 'unmapped_dataset_parameter_banner', param: null, title: null,
        });
    }
    return out;
}
```

The cascade-control error is action-triggered (fires on the
parameter pick), not on initial load. Browser test must drive the
cascade (pick the source role/control) before asserting.

## `GetUniqueAttributeValuesSyncForAnalysis` 400s on parameterized datasets — BR.x, 2026-05-29

### Symptom

A `ParameterDropDownControl` whose `SelectableValues.LinkedValues`
points at a column on a parameterized dataset (a dataset with
declared `DatasetParameters` that its CustomSql substitutes via
`<<$pX>>`) fails to populate. The browser DevTools network tab
shows a 400 on:

```
/sn/account/<acct>/api/analyses/<analysis-id>/prepared-data-sources/<dataset-id>/columns/<logical-table>.<column>/unique-attributes?Operation=GetUniqueAttributeValuesSyncForAnalysis
```

Verified with a `pg_stat_activity` capture during a triggered fetch:
**zero QS-originated queries reach Postgres**. The 400 fires entirely
on QS's side BEFORE any SQL is sent. The endpoint appears to refuse
to execute against parameterized datasets — no clever SQL escape can
help because we never reach SQL execution.

### What we tried (all blocked)

1. `value_when_unset.CustomValue = '<sentinel>'` on the analysis
   param. Fixes the "calculated field has invalid syntax" cascade
   tooltip but doesn't change the unique-values 400.
2. `'<<$pX>>' = <<$pX>>` always-true short-circuit in the WHERE.
   boto3 `CreateDataSet` rejects with
   `InvalidParameterValueException: Sql contains one/many quote
   wrapped parameters.`
3. `'<<$' || 'pX>>' = <<$pX>>` split-concat to bypass the boto3
   validator. Accepted by boto3, deployed cleanly, did NOT change
   the 400 behavior — QS still rejects the parameterized dataset
   before any SQL fires.
4. Removing `CascadingControlConfiguration` while keeping the
   parameterized dataset. Doesn't help — the LinkedValues fetch
   itself is what 400s, separately from cascade.

### The viable shapes

- **Cascade source dataset MUST be unparameterized** (no
  `DatasetParameters`, no `<<$pX>>` in SQL). The cascade narrows
  UI-side via `ColumnToMatch`.
- **OR drop cascade entirely.** Parameter dropdown sources can be
  parameterized datasets *if* you accept that the LinkedValues
  refresh doesn't fire on bridge param change — the operator sees
  the full universe in the dropdown. The dropdown pick + bridge
  re-fetch on data visuals still works correctly; only the dropdown
  options themselves don't narrow.

### Renderer divergence

This is a QS-only failure. App2 (the HTMX renderer) implements
`<<$pX>>` substitution at the `_sql_executor` layer and re-fetches
LinkedValues options on bridge param change. So an analysis that
needs the cascade narrowing CAN have it on App2 — declare the
divergence in the sheet description + ship the wider universe on
QS.

**Currently affected: none.** Daily Statement's Role → Account
cascade was the only live wiring this quirk affected; CQ.4.a
(2026-06-08) dropped the cascade entirely per operator lock ("ALL
internal accounts should be searchable"). The picker source
(`DS_L1_DS_ACCOUNTS`) is now unparameterized — every internal-scope
account is in the dropdown universe; no narrowing-by-pick needed.
The QS-side quirk itself is unchanged (a future cascade attempt
would re-encounter it); it just has no consumers today.

### Diagnostic JS — does this dataset's dropdown 400?

Open browser DevTools → Network → filter for `unique-attributes` →
trigger the dropdown / cascade. Any 400 with
`Operation=GetUniqueAttributeValuesSyncForAnalysis` confirms the
shape. The dataset id is in the URL path; verify it has declared
`DatasetParameters` in the emitted JSON. If yes, this quirk
applies.

---

## `KPIVisual.ConditionalFormatting.PrimaryValue.Icon.CustomCondition.IconOptions.Icon` rejects most semantic-icon names — CF.X-infra, 2026-06-05

### Symptom

`create_analysis` rejects the KPI with a generic
``ValidationException: Value 'EXCLAMATION_CIRCLE' at … failed to
satisfy constraint``. The error message itself lists every value
QS accepts (so this is the de-facto API doc).

### The valid enum (full set, per the AWS error)

```
TWO_BAR, THUMBS_DOWN, FACE_DOWN, ARROW_RIGHT, TRIANGLE, FLAG,
ARROW_UP_RIGHT, ARROW_DOWN_LEFT, CIRCLE, MINUS, ARROW_UP,
THREE_BAR, CHECKMARK, ARROW_DOWN_RIGHT, ARROW_UP_LEFT, CARET_UP,
X, ARROW_DOWN, ONE_BAR, PLUS, FACE_FLAT, THUMBS_UP, SQUARE,
FACE_UP, ARROW_LEFT, CARET_DOWN
```

Conspicuously absent: `EXCLAMATION_CIRCLE`, `WARNING`,
`EXCLAMATION_MARK`, `ALERT`, every "exclamation-shaped" or
"warning-shaped" name a designer would reach for first. The
warning-state slot is `TRIANGLE` — full stop.

### What works

- **Healthy / OK**: `CHECKMARK`
- **Warning / amber**: `TRIANGLE` (the warning-glyph slot)
- **Error / red**: `X`
- **Trend up**: `ARROW_UP` (or `CARET_UP` for a smaller glyph)
- **Trend down**: `ARROW_DOWN` (or `CARET_DOWN`)

### Notes

The AWS docs page for `KPIIcon` doesn't enumerate the valid set —
the validation error is the only source of truth. Deploy-probe
any new icon choice via `./run_tests.sh up_to=deploy --dialects=pg
--targets=aw` against a minimal KPI; if AWS green-lights it, also
write the new name into this quirks-log entry so we don't relearn.

Confirmed-via: AWS run 26991328435 on commit `bb36bf94`
(CF.X-infra ship). `EXCLAMATION_CIRCLE` was the original
`_KPI_AMBER_ICON_QS` value; CF.X-infra hotfix swapped to
`TRIANGLE`.

## Oracle `LISTAGG` has a ~32KB result cap — CW.2, 2026-06-09

### Symptom

Computing the audit-PDF provenance fingerprint via
`sha256(LISTAGG(per_row_sha256 …))` would have been the simplest
all-SQL form for the row-set hash. On Oracle 19c it raises
**ORA-01489** ("result of string concatenation is too long") once
the concatenated payload exceeds ~32 KB (the VARCHAR2 maximum at
default `MAX_STRING_SIZE`). At ~64 bytes per row hash, that
ceiling hits at ~500 rows — well below the ~3.27M rows the
production base table carries.

### Confirmed-via

Phase CW.2 (2026-06-09) per
`docs/audits/audit_pdf_perf.md` — the all-SQL form was the first
shape considered for the streamed fingerprint; Oracle's `LISTAGG`
cap is what forced the design to the streamed-fold approach
(per-row SHA-256 in SQL + ``cur.fetchmany(50_000)`` digests +
``hashlib.sha256().update()`` fold via Merkle-Damgård).

### Workaround

Don't concatenate per-row hashes in SQL. Compute the per-row
SHA-256 in the engine, ``ORDER BY entry`` (or ``ORDER BY rh`` for
keyless tables), stream digests as fixed-size hex strings through
Python via ``cur.fetchmany(batch_size)``, fold via
``hashlib.sha256().update(rh.encode("ascii"))``. The result is
byte-identical to ``sha256(string_agg(rh ORDER BY entry))`` —
proven empirically on DuckDB in
``tests/unit/test_audit_pdf_perf.py``. Avoids `LISTAGG` entirely,
bounded-memory regardless of row count, dialect-portable across
DuckDB / Postgres / Oracle.

Not strictly a *QuickSight* quirk but documented here per the
project convention of keeping all dialect-portability landmines in
one place. The fix lives in ``common/provenance.py::hash_table_rows``.

## PostgreSQL `pgcrypto` extension required for audit provenance — CW.2/CW.3, 2026-06-09

### Symptom

The audit provenance fingerprint on the Postgres path needs
SHA-256 of per-row canonicalized text. PG has no built-in SHA-256
function; the canonical SQL form is
``encode(digest(<text>, 'sha256'), 'hex')`` via the ``pgcrypto``
extension. Without the extension installed:

```
ERROR:  function digest(text, unknown) does not exist
```

### Confirmed-via

Phase CW.2 (2026-06-09) per Lock 3 in
``docs/audits/cw_0_audit_pdf_perf_locks.md``. The project's
"no PG extensions" portability stance was operator-locked to
allow this single exception scoped to provenance — MD5 was
considered but rejected on regulator-defensibility grounds
(MD5 collision attacks public since 2004).

### Workaround

``recon-gen schema apply`` emits
``CREATE EXTENSION IF NOT EXISTS pgcrypto;`` at the top of the
schema script on the Postgres path. DuckDB has native ``sha256()``
and Oracle has native ``STANDARD_HASH(…, 'SHA256')``; neither
needs an extension.

**Operator install prerequisite.** The role doing schema-apply
needs ``CREATE`` permission on the database to create extensions.
This is the default for owner / superuser roles; hardened deploys
with a DDL-only role may need to grant explicitly or pre-create
the extension via a DBA. On RDS PG: enable in the parameter group.
On Aurora PG: built-in, no operator action needed. On
self-hosted PG: install ``postgresql-contrib`` package.

A schema-apply failure with `CREATE EXTENSION` denied surfaces
loud; ``audit apply --execute`` won't reach the fingerprint
computation without the schema. No silent degrade to MD5
(rejected per Lock 3).

## psycopg rejects `chr(0)` (NUL) in text — switched audit canon to `chr(1)` (SOH) — CW.2, 2026-06-09

### Symptom

The Phase CW.2 streamed-fingerprint canonical SQL initially used
`chr(0)` (ASCII NUL) as the NULL → text disambiguation sentinel:
`coalesce(CAST(col AS VARCHAR), chr(0))`. DuckDB + Oracle accept
NUL inside text columns natively; PG-the-server accepts it too;
but **psycopg's driver layer rejects** NUL in any returned text
value with:

```
psycopg.errors.ProgramLimitExceeded: null character not permitted
```

The streamed-fold reads per-row hex digests back from PG and
psycopg refuses to surface text rows that include the NUL byte
that PG's `chr(0)` would have produced if it appeared in the
concat output before the SHA-256 wrap. (The hash output itself
is hex-clean, but psycopg's check fires on the constructed
canonical text before hashing in some code paths.)

### Confirmed-via

Cross-dialect verify run on `e4a5fa07` (Phase CW.2 streamed
fingerprint) — `RECON_GEN_CONFIG=run/config.postgres.yaml
./run_tests.sh up_to=qs_api` hit the error in
`tests/e2e/db/test_audit_pdf_render_verify.py`. DuckDB +
Oracle were green.

### Workaround

`common/provenance.py::_row_hash_sql_expr` switched all three
dialects from `chr(0)` to `chr(1)` (ASCII SOH — Start-of-Heading)
as the NULL sentinel. SOH is non-printing, can't appear in any
of our schema's text/numeric/date/timestamp/boolean column CASTs,
and is psycopg-safe across all dialects. Cross-dialect parity is
preserved (same byte chosen on every dialect = same canonical
text = same SHA-256 fingerprint).

Lock doc updated:
`docs/audits/cw_0_audit_pdf_perf_locks.md` Lock 2/3 notes the
change-of-mind. No `provenance_format_version` bump required
(v2 had no published PDFs in the wild yet — the change happens
on the merge that landed CW).

## Optional `pyhanko` dep: audit signing graceful-degrades — CW.5, 2026-06-09

### Symptom

``audit apply --execute`` against a ``config.yaml`` that declares
a ``signing:`` block but where the optional ``pyhanko`` extra
isn't installed used to hard-crash at the signing step. The PDF
had already been written to disk before the crash — making the
operator either think the whole command failed (the exit code
said so) or hunt for the PDF anyway.

### Confirmed-via

Operator-flagged side issue surfaced during the Phase CW perf
spike (2026-06-09). The hard-crash predates CW; Lock 5 in
``docs/audits/cw_0_audit_pdf_perf_locks.md`` covers the fix
decision.

### Workaround

``cli/audit/__init__.py::audit_apply`` wraps the
``sign_pdf_in_place(...)`` call in ``try/except ImportError``.
On ImportError, emit an unsigned PDF + a stderr warning pointing
at the fix (``pip install recon-gen[prod]``). Exit code stays 0
so the unsigned PDF is treated as a successful (degraded)
output. Genuine signing failures (bad key, expired cert) still
raise — only ``ImportError`` on the optional dep is caught.

Unit test: ``tests/audit/test_cli_smoke.py
::test_audit_apply_signing_pyhanko_missing_degrades_gracefully``.
