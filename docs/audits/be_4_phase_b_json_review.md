# BE.4 Phase B — tests/json/ review

Agent slice: tests/json/ (77 hits). 76 migrated cleanly; 1 flagged
CATEGORY 6 (shape-vs-value coincidence).

## CATEGORY 6 — flagged for principal review

### 1. `tests/json/test_l1_dashboard.py:549` — visual title "Drift" collides with `_DRIFT_NAME`

**File + line**: `tests/json/test_l1_dashboard.py:549` (post-import-shift).

**Inline literal**: `"Drift"`.

**Src constant the lint matched**: `_DRIFT_NAME = "Drift"` at
`src/recon_gen/apps/l1_dashboard/app.py:261` — the **name of the Drift
sheet** (a dedicated dashboard tab showing leaf + parent account-balance
drift violations).

**Why this is shape-vs-value, not migration-ready**: the test asserts
the **visual titles** of the Daily Statement sheet's KPI row:

```python
ds = _sheet_by_name(app, _DAILY_STATEMENT_NAME)
titles = [v.title for v in ds.visuals]
assert titles == [
    "Opening Balance",
    "Debits (signed)",
    "Credits (signed)",
    "Closing Stored",
    "Drift",  # <-- this literal
    "Posted Money Records",
]
```

The literal `"Drift"` here is the **display label of a KPI tile** inside
the Daily Statement sheet — it shows `stored_balance − recomputed_balance`
for the picked (account, business_day) pair. Source:
`src/recon_gen/apps/l1_dashboard/app.py:1636` —

```python
kpi_row.add_kpi(
    width=kpi_width,
    title="Drift",
    subtitle=(
        "Stored − recomputed. Non-zero ⇒ feed doesn't reconcile."
    ),
    values=[ds_summary["drift"].max(currency=True)],
)
```

The Drift **sheet** (`_DRIFT_NAME`) and the Drift **KPI tile in Daily
Statement** are unrelated concepts that happen to share the spelling
"Drift" because they both surface the same underlying violation. Coupling
the test to `_DRIFT_NAME` would mean: if someone renames the Drift sheet
to "Account Drift" or similar, this Daily-Statement-KPI test would also
fail — misleadingly, because the KPI tile name is independently
authored at the KPI's `add_kpi(title=...)` call.

**Action taken**: per-line suppression comment with reason:

```python
"Drift",  # typing-smell: ignore[no-inline-production-constants]: visual title (drift KPI in Daily Statement sheet); shares spelling with _DRIFT_NAME (separate sheet name) but unrelated concepts — see docs/audits/be_4_phase_b_json_review.md
```

**Proposed Phase C action** (deferred to principal):

Two options:

1. **Keep the suppression** (lowest cost). The lint catches the
   coincidence and the operator manually confirms it's intentional.
   Phase C just inherits the suppression.

2. **Promote a `_DRIFT_KPI_TITLE = "Drift"` sibling constant** in
   `src/recon_gen/apps/l1_dashboard/app.py` next to the
   `kpi_row.add_kpi(title=...)` call at line 1636, then drop the
   suppression and import the new name. Slight cost: one more constant
   on the src side. Slight benefit: the test asserts the actual KPI
   tile's title against a named source-of-truth.

My read: option 1 wins. The KPI tile label "Drift" is local
vocabulary inside one sheet's body; promoting it to a module-level
constant for the sake of test-side imports is premature
architecture. The suppression comment is louder than the constant
would be — the next person to read it sees *why* the literal is
allowed-inline rather than just *that* it is.

But this is the principal's call.

---

## Summary

- 76 hits migrated via direct imports (CATEGORY 1 sheet names + titles,
  CATEGORY 2 dataset / parameter / CF IDs, CATEGORY 3 cleanup tags,
  CATEGORY 4 sentinels — `_DRILL_RESET_SENTINEL`).
- 1 hit suppressed with inline `# typing-smell: ignore[...]` + reason
  (CATEGORY 6 shape-vs-value coincidence above).
- No src/ refactors needed for the 76 migration-ready hits — Python's
  underscore-prefix is a convention, not enforcement.
- 0 unsuppressed lint hits remaining in `tests/json/` (verified via the
  Phase A survey snippet with suppression filtering applied).
