# BE.4 Phase B — small-tail review (judgment cases)

**Slice owner**: small-tail agent (`tests/audit/`, `tests/docs/`,
`tests/e2e/`, `tests/js/`).

**Status**: 8 of 15 hits migrated cleanly; 7 hits flagged here for
principal review because the inline literal is documenting *shape*
(test-input fixture, dict key from a different concept, or filter
label from a different surface) rather than asserting a value-coupled
production constant.

The lint's category-1 mapping to `_DRIFT_NAME` / `_OVERDRAFT_NAME` /
`_RAILS_NAME` is the side-effect of those being the only module-level
`UPPER_SNAKE` constants holding the string value the test uses —
*not* a signal that the test is semantically coupled to the L1
dashboard sheet name. Migrating creates a false loud-fail surface
(rename `_DRIFT_NAME` and a chart-renderer fixture goes red for
zero domain-correctness reason).

Phase C should pick one of: (a) allowlist these literals with a
`# noqa: no-inline-production-constants` style suppression on the
assert line; (b) promote the *correct* source-of-truth constants in
src and migrate the tests to those; or (c) refactor the tests to
use neutral strings (e.g. chart fixtures `["A", "B", "C"]`) that
the lint won't match.

## Hit-by-hit

### #9 — `tests/e2e/test_dashboard_driver.py:170`

```python
assert set(driver.filter_options("Rails")) == {
    "ach", "wire", "check", "internal", "zba",
}
```

Lint maps `"Rails"` to `_RAILS_NAME` from
`apps/l2_flow_tracing/app.py:135`. But the assertion reads the
*smoke app's* filter label, declared inline at
`common/html/_smoke_app.py:68` (`ParameterMultiSelectSpec(name="rails",
label="Rails", ...)`). The L2FT sheet name and the smoke-app filter
label share the word "Rails" by coincidence — they are different
domain concepts on different surfaces.

**Proposed action**: promote the smoke-app filter label to a
module-level constant in `_smoke_app.py` (e.g. `_RAILS_FILTER_LABEL
= "Rails"`) and migrate the test against that. Phase C work.

### #10 + #11 — `tests/e2e/test_l1_account_filters.py:406, 408`

```python
assert expected_day1["Drift"] == expected_drift_from_narrative, (
    f"day1={effective_day1!r} account={picked_account!r}: matview's "
    f"`drift` column ({expected_day1['Drift']}) doesn't equal "
    ...
)
```

`expected_day1["Drift"]` is a **column-name key** into a Daily
Statement row dict. The Daily Statement KPI column title is set
inline at `apps/l1_dashboard/app.py:1636` (`title="Drift"`) — a
distinct literal from `_DRIFT_NAME = "Drift"` (line 261, the
top-level *sheet name*). They share the value "Drift" by domain
coincidence (the Daily Statement column displays the drift metric
on the Drift sheet) but are different production constants.

Migrating to `_DRIFT_NAME` would couple the column-header dict key
to the sheet name, creating a false loud-fail when one rename
happens without the other.

**Proposed action**: promote the Daily Statement KPI title to a
module-level constant (e.g. `_DAILY_STATEMENT_DRIFT_COLUMN_TITLE`)
and migrate the test against that. Phase C work.

### #12 + #13 — `tests/js/test_render_barchart.py:211, 212`

```python
_render_into_target(page, {
    "categories": ["Drift", "Overdraft", "Limit"],
    "series": [{"values": [1, 2, 3]}],
})
ticks = cast(list[str], page.evaluate(...))
browser.close()
assert "Drift" in ticks
assert "Overdraft" in ticks
assert "Limit" in ticks
```

These are **generic chart-renderer fixture data**. The test passes
arbitrary strings as bar categories and asserts they round-trip as
tick labels. The strings `"Drift"` and `"Overdraft"` are the
test author's clarity choice — they could equally be `["A", "B",
"C"]` with no domain consequence. The lint matches them to
`_DRIFT_NAME` / `_OVERDRAFT_NAME` because those are module-level
constants holding the same value, but the test is not asserting
anything about the L1 dashboard.

**Proposed action**: refactor to neutral fixture strings (e.g.
`["Cat A", "Cat B", "Cat C"]`) so the assertion still verifies
round-trip without tripping the lint. Phase C work.

### #14 + #15 — `tests/js/test_render_linechart.py:112, 113`

Same shape as #12+#13 — generic line-chart legend round-trip with
dashboard-flavoured strings as fixture data.

**Proposed action**: same as #12+#13 — neutral fixture strings.

## Migrations applied (the clean 8)

For traceability:

| # | File | Line(s) | Imported constant |
|---|---|---|---|
| 1 | `tests/audit/test_cli_smoke.py` | 476 | `_DRIFT_NAME` |
| 2 | `tests/audit/test_cli_smoke.py` | 577 | `DEFAULT_PREFIX` |
| 3 | `tests/audit/test_cli_smoke.py` | 595 | `_DRIFT_NAME` |
| 4 | `tests/audit/test_cli_smoke.py` | 610 | `_SUPERSESSION_AUDIT_NAME` |
| 5 | `tests/audit/test_pdf_sqlite.py` | 446 | `_DRIFT_NAME` |
| 6 | `tests/audit/test_pdf_sqlite.py` | 448 | `_OVERDRAFT_NAME` |
| 7 | `tests/docs/test_handbook_vocabulary.py` | 62 | `_SASQUATCH_PERSONA_ACRONYM` |
| 8 | `tests/docs/test_handbook_vocabulary.py` | 216 | `_SASQUATCH_PERSONA_ACRONYM` |

Per-file pytest verification: green
(`test_cli_smoke.py` 23 passed; `test_pdf_sqlite.py` 8 passed;
`test_handbook_vocabulary.py` 32 passed).

## BE.2 lint remaining-count after this slice

The slice-wide verification snippet (per the contract) returns
**7 remaining hits** after migration — the 7 documented above.
Phase C action required before enabling the BE.2 lint.
