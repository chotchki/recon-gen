# CL.0 — Sparse balance loads via ETL: design audit

**Status:** locked 2026-06-08. Inputs: CP shape precedent + PLAN.md::Phase CL locks (5 operator locks from chotchki 2026-06-07).

## 1. Recap of phase locks (verbatim from PLAN.md)

1. **Cadence lives on Account / AccountTemplate** — not a top-level dict. Reuses CP's editor / loader / seed plumbing pattern.
2. **Two variants for v1: `sparse` + `explicit_daily`.** EOM simulated via sparse; per-state per-account.
3. **Default = `sparse`.** None ⇒ sparse at read time. No compat shim.
4. **Test generator honors the declared cadence.** Sparse emits balance rows only on activity days; explicit_daily emits every business day. Trainer plant `missing_balance_on_explicit_daily` exercises the gap path.
5. **KPI: Reported vs Inherited.** Top-line KPI + per-row R/I badge on Daily Statement; underlying balance unchanged, badge is provenance metadata.

## 2. Open-question lock-downs

### 2.1 — Badge layout (column vs inline glyph)

**LOCK: dedicated column.** A separate `Reported` column showing `R` / `I` next to the balance value. Reasons:

- Inline glyph next to a currency value clutters the cell width math; the existing Daily Statement table already has narrow currency columns that wrap awkwardly at 3-digit balances.
- A column header `Reported` is operator-readable signal: column-of-Rs-and-Is is itself a scan-pattern (a column of all-R is a healthy explicit_daily account; column with R-I-I-R-I... is a sparse account where reporting is intermittent).
- Sort-by-column-header lets an operator surface all the "Inherited" rows together for triage.

Width: 8ch column. Header text "Rpt." (abbreviated for compactness). Cell content single character `R` or `I`, rendered `text-secondary-fg` so it doesn't compete with the balance value visually.

### 2.2 — KPI band thresholds (3-band strawman finalization)

**LOCK: 90% / 50% as documented in PLAN.md.** No data-side rationale to deviate. Mirrors the BO.4 sign-flip KPI band pattern (operator already trained on this 3-band vocabulary).

- **Green (healthy):** `reported_pct >= 90%` — ETL feed is healthy; majority of balance rows are explicit reports.
- **Amber (check feed):** `50% <= reported_pct < 90%` — sparse reporting normal for activity-driven feeds, but worth checking the trend hasn't shifted.
- **Red (ETL likely down):** `reported_pct < 50%` — too many inherited rows; the feed may have stopped reporting balances. Surface as Investigation-drillable.

KPI label: `Reported vs Inherited` (verbatim from chotchki). Numerator/denominator: count of rows `WHERE provenance = 'R'` over total rows in the analysis window.

### 2.3 — Per-fixture cadence choices

**Strawman, operator can adjust at CL.11:**

- **spec_example:** all internal accounts on `sparse` (the default). Lightweight illustration. No `explicit_daily` accounts means no `balance_cadence_gap` rows in the demo — that's fine for the minimal fixture. One demo account flipped to `explicit_daily` so the gap-invariant has at least one possible firing target if the trainer plant fires.
- **sasquatch_pr:** 70/30 split — most internal accounts `sparse`, GL control + clearing-suspense + customer-ledger on `explicit_daily` (the institution-internal control accounts MUST report daily for reg purposes; the activity-day-driven feed shape applies to customer-facing accounts). Realistic for a midsize bank persona.
- **heavy_density_v1:** 80/20 split — bulk-sparse with a sample of explicit_daily across the account-template fanout. Tests the matview's window-function performance on the largest fixture.

## 3. Carry-forward SQL across 3 dialects

The post-CL drift matview's effective-balance computation uses `LAST_VALUE` with `IGNORE NULLS`. All 3 target dialects accept the same SQL shape (per BZ.1 window-function precedent).

```sql
-- BEFORE (current drift matview shape, simplified):
SELECT account_id, business_day, balance
FROM <prefix>_daily_balances
WHERE business_day BETWEEN :start AND :end

-- AFTER (carry-forward effective balance):
SELECT
  account_id,
  business_day,
  LAST_VALUE(balance) IGNORE NULLS
    OVER (PARTITION BY account_id ORDER BY business_day) AS effective_balance,
  CASE
    WHEN balance IS NOT NULL THEN 'R'   -- reported
    ELSE 'I'                            -- inherited (carry-forward)
  END AS provenance
FROM <prefix>_daily_balances_calendar
WHERE business_day BETWEEN :start AND :end
```

The `<prefix>_daily_balances_calendar` is a CL-introduced view: cross-join `accounts × business_day_calendar` LEFT JOIN `daily_balances` so every (account, business_day) pair exists with NULL for unreported days. The window function then back-fills.

**Dialect compat:**
- **DuckDB:** `IGNORE NULLS` supported. ✓
- **PostgreSQL 17:** `LAST_VALUE(...) IGNORE NULLS OVER (...)` supported (added in PG 16). ✓
- **Oracle 19c:** Native syntax. ✓

Per [[feedback_sql_dialect_convergence_preferred]]: no per-dialect arm needed.

**Edge case:** an account with NO reported rows for the entire window. `LAST_VALUE IGNORE NULLS` returns NULL; downstream drift check treats NULL effective_balance as "no balance ever known" → no drift firing (correct — can't drift from a value that doesn't exist).

## 4. `balance_cadence_gap` invariant SQL

```sql
CREATE TABLE <prefix>_balance_cadence_gap AS
SELECT
  bd.account_id,
  bd.business_day,
  -- The most-recent reported balance before this gap day
  (SELECT balance
   FROM <prefix>_daily_balances db2
   WHERE db2.account_id = bd.account_id
     AND db2.business_day < bd.business_day
   ORDER BY db2.business_day DESC LIMIT 1) AS last_reported_balance,
  -- How many business days since that last report
  (SELECT bd.business_day - MAX(db3.business_day)
   FROM <prefix>_daily_balances db3
   WHERE db3.account_id = bd.account_id
     AND db3.business_day < bd.business_day
     AND db3.balance IS NOT NULL) AS days_since_last_report
FROM <prefix>_business_day_calendar bd
JOIN <prefix>_accounts a ON a.id = bd.account_id
LEFT JOIN <prefix>_daily_balances db ON
  db.account_id = bd.account_id AND db.business_day = bd.business_day
WHERE
  a.balance_cadence = 'explicit_daily'  -- only check declared-daily accounts
  AND db.business_day IS NULL          -- no reported row that day
```

Dashboard card on L1 Exceptions sheet; subtitle: *"Account declared `explicit_daily` cadence — every business day requires a reported balance row. Missing rows in red."*

## 5. Drift / ledger-drift / overdraft matview rewrites

The 3 L1 invariants currently key off `daily_balances.balance` directly. Post-CL they key off the new `effective_balance` from the carry-forward shape. This is the **risk cell** (CL.5) — operator-eyes review BEFORE merge.

Each matview's modification:

- **drift:** `JOIN daily_balances` → `JOIN <prefix>_effective_balances` (the new carry-forward view). Drift formula unchanged; just the input column.
- **ledger_drift:** same.
- **overdraft:** same; the negative-balance check fires on `effective_balance < 0` instead of `balance < 0`.

Test-data implication: sparse fixtures will have many more matview rows post-CL because every business day now has an effective balance (vs only activity days having ANY balance row before). The L1 invariant rows themselves shouldn't change — overdraft only fires when carry-forward stays negative; drift only fires when carry-forward disagrees with subledger sum. Both behaviors are preserved.

## 6. Dataclass + loader

```python
# common/l2/primitives.py
BalanceCadence: TypeAlias = Literal["sparse", "explicit_daily"]

@dataclass(frozen=True, slots=True)
class Account:
    # ... existing fields ...
    business_day_offset: int | None = None  # (CP)
    balance_cadence: BalanceCadence | None = None  # (CL)

    def __post_init__(self) -> None:
        # (CP guard preserved)
        # ... existing range guard ...
        # (CL guard) — Literal type narrowing happens at the loader,
        # not __post_init__; pyright would flag a non-Literal assignment.

@dataclass(frozen=True, slots=True)
class AccountTemplate:
    # Same shape; template offset already fans out to instances per Lock 4.
    balance_cadence: BalanceCadence | None = None
```

Helper `resolve_cadence(account_or_template) -> BalanceCadence` returns `"sparse"` when the attribute is None.

## 7. Editor FieldSpec

```python
# In _ACCOUNT_FIELDS and _ACCOUNT_TEMPLATE_FIELDS (positioned adjacent
# to business_day_offset — the EOD/cadence knobs cluster):
FieldSpec(
    name="balance_cadence",
    label="Balance cadence",
    helper=(
        "Sparse (default): balance rows arrive only on activity days. "
        "Explicit-daily: balance rows MUST arrive every business day "
        "(missing day = gap violation, surfaces on L1 Exceptions)."
    ),
    kind="select",
    options=("", "sparse", "explicit_daily"),  # empty = default-sparse
    required=False,
),
```

No CSS hide needed (works on any scope, unlike business_day_offset).

## 8. Trainer plant

```python
# common/l2/plant_registry.py — new entry:
PlantKindEntry(
    kind="missing_balance_on_explicit_daily",
    category=PlantCategory.L1_INVARIANT,
    family="L1 Cap",
    primitives=(
        # operator picks the account_id from the explicit_daily roster +
        # the business_day to gap
        PrimitiveStringField(name="account_id", helper="..."),
        PrimitiveStringField(name="business_day", helper="YYYY-MM-DD"),
    ),
    plant_function=...,  # DELETEs the (account_id, business_day) row from
                         # <prefix>_daily_balances; the matview surfaces it
                         # as a balance_cadence_gap row on the next refresh
    dashboard_check=DashboardCheck(
        matview_name="balance_cadence_gap",
        ...
    ),
    tour_destination=TourDestination(
        primary_url="/dashboards/l1/sheets/exceptions",
        ...
    ),
)
```

If no `explicit_daily` accounts exist in the fixture, plant raises a typed error ("Fixture has no explicit_daily accounts; declare one or pick a different plant kind").

## 9. Dashboard KPI + R/I badge

Typed primitive `BalanceProvenanceBadge`:

```python
# common/tree/visuals.py
@dataclass(frozen=True, slots=True)
class BalanceProvenanceBadge:
    """CL — surface where this row's balance came from.

    `reported_field` is the contract column carrying 'R' or 'I' from
    the carry-forward view. App2 + QS renderers translate to the
    visible glyph; the contract row stays the literal character so
    operators can SELECT it directly from the matview.
    """
    reported_field: ColumnSpec
    reported_label: str = "R"
    inherited_label: str = "I"
```

KPI `Reported vs Inherited`:

```python
KPI(
    label="Reported vs Inherited",
    metric=...,  # COUNT(provenance='R') / COUNT(*) ratio over visible window
    bands=KPIValueThresholdBanding(
        green_at=Decimal("0.90"),
        amber_at=Decimal("0.50"),
    ),
)
```

Per BK.2 KPI banding precedent, the 3-band primitive already exists.

## 10. Done-when (CL.0 audit only)

- [x] All 5 phase locks restated (§1)
- [x] Badge layout locked (§2.1)
- [x] KPI band thresholds locked (§2.2)
- [x] Per-fixture cadence strawman picks documented (§2.3)
- [x] Carry-forward SQL spec'd across 3 dialects (§3)
- [x] `balance_cadence_gap` invariant SQL spec'd (§4)
- [x] L1 matview rewrite plan documented (§5) — **risk cell; operator eyes before merge**
- [x] Dataclass + loader shape spec'd (§6)
- [x] Editor FieldSpec shape spec'd (§7)
- [x] Trainer plant shape spec'd (§8)
- [x] KPI + badge primitive spec'd (§9)

CL.1 can fire as soon as this audit lands.

## 11. Flagged for morning review

- **Per-fixture cadence picks** (§2.3) are strawman; chotchki adjusts at CL.11 if a specific demo scenario wants different splits.
- **CL.5 matview rewrite** touches L1 invariants — extra-careful pass before any merge to main. The carry-forward semantics are mathematically equivalent to the current behavior on sparse-default fixtures (no behavior change when balance reports are dense); the new behavior kicks in only when an account has missing-day rows that need filling.
- **CL.10 re-lock** will break byte-identity intentionally because the new `<prefix>_daily_balances_calendar` and `<prefix>_effective_balances` views add rows. Commit messages document the expected delta.
