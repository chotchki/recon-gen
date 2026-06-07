# CL.0 — Sparse balance loads via ETL: design audit

**Status:** locked 2026-06-08. Inputs: CP shape precedent + PLAN.md::Phase CL locks (5 operator locks from chotchki 2026-06-07).

## 1. Recap of phase locks (verbatim from PLAN.md)

1. **Cadence lives on Account / AccountTemplate** — not a top-level dict. Reuses CP's editor / loader / seed plumbing pattern.
2. **Two variants for v1: `sparse` + `explicit_daily`.** EOM simulated via sparse; per-state per-account.
3. **Default = `sparse`.** None ⇒ sparse at read time. No compat shim.
4. **Test generator honors the declared cadence.** Sparse emits balance rows only on activity days; explicit_daily emits every business day. Trainer plant `missing_balance_on_explicit_daily` exercises the gap path.
5. **KPI: Reported vs Inherited.** Top-line KPI + per-row R/I badge on Daily Statement; underlying balance unchanged, badge is provenance metadata.

## 2. Open-question lock-downs

### 2.1 — Badge layout (KPI icon channel, three-state source)

**LOCK (revised 2026-06-07 after Studio drive): inline KPI icon, three-state source enum.** Originally locked as a dedicated `Reported` column on the Daily Statement *postings* table; chotchki rejected that read on inspection — the postings table holds today's transaction legs, not the balance record, and in a normal sparse case there wouldn't be rows there to badge. The badge belongs on the **balance KPIs** (Opening Balance + Closing Stored), not the postings table.

**Source enum** (new matview column, per-balance column):

| Source                          | Closing Stored icon | Drift icon | Meaning                                            |
|---------------------------------|---------------------|-----------|----------------------------------------------------|
| `emitted`                       | (none)              | ✓ or ✗    | Real row from the feed                             |
| `carried_no_activity`           | ↩                   | ✓         | Sparse cadence, no postings today — benign         |
| `carried_with_activity_gap`     | ⚠                   | ✗ red     | Postings happened, balance row missing — bug       |

**Why three states, not two.** Two-state ("real" vs "calculated") swallows the most interesting case: postings happened today, but the institution didn't emit a balance row. With two states the Closing KPI shows the carried-forward value silently and the Drift KPI computes off it — drift surfaces, but the operator can't tell from the value whether the close itself is suspect. Three states split the carry-forward into benign (no activity, `drift = $0` by construction) vs gap (activity without a confirming balance — `drift = -net_flow ≠ 0`, ✗ red).

**Glyphs:**

- **`↩`** (leftwards arrow with hook) — `carried_no_activity`. Reads as "came from the past" without a legend.
- **`⚠`** (warning sign) — `carried_with_activity_gap`. chotchki 2026-06-07: the gap case isn't *just* calculated, it's a missing emit with activity behind it — the warning glyph signals on the balance value the analyst is reading, not only via Drift's downstream ✗. Two reads of the same finding (Closing ⚠ + Drift ✗) is the intended belt-and-suspenders.

Both are monochrome (no emoji rendering risk in QS), thin enough not to crowd the KPI primary value. The `text-3xl` font shrink that landed today (2026-06-07) leaves room for the prefix glyph without re-overflowing.

**Renderer plumbing.** Both renderers consume the same `_source` column:

- **QS:** `KPIPrimaryValueConditionalFormatting` keyed on `<col>_source IN ('carried_no_activity', 'carried_with_activity_gap')` → icon override `↩`. Drift KPI keeps its existing `kpi_zero_is_healthy` ✓/✗ — the gap case naturally turns ✗ red because the matview computes `drift = -net_flow ≠ 0`. No new QS primitive needed.
- **App2:** `shape_kpi` reads the `_source` column and stamps `state_icon = "↩"` when carried; existing `bootstrap.js` prefix renders it.

**Drill semantics.** Clicking the ↩-marked Closing Stored KPI drills to L1 Exceptions filtered to the `balance_cadence_gap` row for that account-day (when present — i.e. `carried_with_activity_gap` or `explicit_daily` with no row). Drill from the ✗-red Drift KPI also lands there. Both make the underlying CL.6 invariant the single triage destination.

### 2.2 — KPI band thresholds (3-band strawman finalization)

**LOCK: 90% / 50% as documented in PLAN.md.** No data-side rationale to deviate. Mirrors the BO.4 sign-flip KPI band pattern (operator already trained on this 3-band vocabulary).

- **Green (healthy):** `reported_pct >= 90%` — ETL feed is healthy; majority of balance rows are explicit reports.
- **Amber (check feed):** `50% <= reported_pct < 90%` — sparse reporting normal for activity-driven feeds, but worth checking the trend hasn't shifted.
- **Red (ETL likely down):** `reported_pct < 50%` — too many inherited rows; the feed may have stopped reporting balances. Surface as Investigation-drillable.

KPI label: `Reported vs Inherited` (verbatim from chotchki). Numerator/denominator: count of rows `WHERE provenance = 'R'` over total rows in the analysis window.

### 2.3 — Per-fixture cadence choices

**Strawman, operator can adjust at CL.11:**

- Comment: This is fine

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

## 4. `balance_cadence_gap` invariant SQL (cadence-aware)

**LOCK (revised 2026-06-07): two firing modes, both materialize into the same invariant table.** The original lock only fired on `explicit_daily` accounts. The sparse-with-activity case (postings on a day with no emitted balance row) is the more interesting institutional bug shape — it can hide indefinitely in the current dashboard because the row simply doesn't exist to be flagged. Both modes go to the same matview so L1 Exceptions has a single triage destination.

```sql
CREATE TABLE <prefix>_balance_cadence_gap AS
WITH today_postings AS (
  SELECT
    {date_trunc_tx_posting} AS business_day,
    tx.account_id,
    SUM(tx.amount_money) AS net_flow,
    COUNT(*) AS leg_count
  FROM <prefix>_current_transactions tx
  WHERE tx.status <> 'Failed'
  GROUP BY {date_trunc_tx_posting}, tx.account_id
)
SELECT
  bd.account_id,
  bd.business_day,
  a.balance_cadence,
  -- Most-recent reported balance before this gap day
  (SELECT balance
   FROM <prefix>_daily_balances db2
   WHERE db2.account_id = bd.account_id
     AND db2.business_day < bd.business_day
   ORDER BY db2.business_day DESC LIMIT 1) AS last_reported_balance,
  -- Postings on this gap day (NULL when zero — the sparse-benign case)
  tp.net_flow AS gap_day_net_flow,
  tp.leg_count AS gap_day_leg_count,
  -- Which firing mode this row represents (drives display + drill copy)
  CASE
    WHEN a.balance_cadence = 'explicit_daily' THEN 'declared_daily_missing'
    WHEN a.balance_cadence = 'sparse' AND tp.leg_count > 0 THEN 'sparse_with_activity'
  END AS gap_kind
FROM <prefix>_business_day_calendar bd
JOIN <prefix>_accounts a ON a.id = bd.account_id
LEFT JOIN <prefix>_daily_balances db
  ON db.account_id = bd.account_id AND db.business_day = bd.business_day
LEFT JOIN today_postings tp
  ON tp.account_id = bd.account_id AND tp.business_day = bd.business_day
WHERE
  db.business_day IS NULL                            -- no emitted row that day
  AND (
    a.balance_cadence = 'explicit_daily'             -- declared-daily mode
    OR (a.balance_cadence = 'sparse' AND tp.leg_count > 0)  -- sparse-gap mode
  )
```

**Firing modes:**

- `gap_kind = 'declared_daily_missing'` — account declared `explicit_daily` cadence, every business day requires a reported balance row, this day has none. Fires regardless of activity.
- `gap_kind = 'sparse_with_activity'` — account declared `sparse` cadence (no-emit on quiet days is fine), but postings happened today and no confirming balance row was emitted. The institution has activity it can't reconcile against a known close.

Dashboard card on L1 Exceptions sheet; subtitle: *"Balance row missing where the cadence requires one. `declared_daily_missing` = `explicit_daily` account with no row today. `sparse_with_activity` = `sparse` account where postings happened today but no balance row was emitted to reconcile against."*

**Drill from Daily Statement:** the `↩` (or `⚠` for the gap case) + Drift's ✗ combo on Closing Stored + Drift KPIs lands here filtered to `(account_id, business_day)`.

### 4.a — Posted Money Records empty-state copy (App2-only)

When the Daily Statement's Posted Money Records table is empty AND the closing balance is carried (source ≠ `emitted`), override App2's generic BQ.1 "No matches" empty-state with carry-forward context:

| Source                          | Empty-state copy                                                          |
|---------------------------------|---------------------------------------------------------------------------|
| `carried_no_activity`           | "No postings today — balance carried forward from {prior_date}"           |
| `carried_with_activity_gap`     | n/a (table will not be empty — postings happened, that's the whole point) |

App2-only because QS's native "No data" treatment isn't customizable per row-context (BQ.1 empty-state lives entirely in `bootstrap.js`). The `{prior_date}` is sourced from a new matview column `closing_carried_from_date` (NULL when source = `emitted`, populated from the date of the LAST_VALUE pull when carried). Add this column to the CL.5 matview rewrite alongside the source enum.

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

## 9. Dashboard KPI + source icon

**Per-balance KPI source icons** — no new dataclass primitive needed. The carry-forward semantics surface entirely through the existing `KPIPrimaryValueConditionalFormatting` channel keyed on the matview's per-balance `<col>_source` enum (see §2.1). App2's `shape_kpi` reads the same column and stamps `state_icon`. Drop the previously-proposed `BalanceProvenanceBadge` dataclass — the conditional-formatting hook already does the job.

**Fleet-wide ETL health KPIs — Info tab, not L1 Dashboard overview** (chotchki 2026-06-07 lock):

The original §9 had a single `Reported vs Inherited` fleet ratio (count(reported) / count(all)) on the L1 Dashboard overview. That conflated two different questions and produced false alarms on healthy sparse fixtures (a sparse account *should* have many inherited days; the ratio doesn't measure health).

Split into two ratios, both on the **Info tab** alongside the other matview-health canaries:

### 9.a — Templates reporting today

```python
KPI(
    label="Sparse templates reporting today",
    subtitle=(
        "For each `sparse` account template, healthy = at least one "
        "account instance emitted a balance row today. Templates with "
        "zero instances reporting today indicate the feed for that "
        "shape may have stopped. Numerator: templates with ≥1 emitted "
        "balance row today. Denominator: all sparse templates."
    ),
    metric=...,  # COUNT(DISTINCT template_id WHERE emitted_today) / COUNT(DISTINCT sparse_template_id)
    bands=KPIValueThresholdBanding(
        green_at=Decimal("0.90"),
        amber_at=Decimal("0.50"),
    ),
)
```

**Why template-level not account-level for daily judgment:** an individual sparse account legitimately may not emit any balance on a quiet day. Aggregating to the template-shape gives the per-day signal: if ANY instance of the template reports, the feed for that shape is alive.

### 9.b — Accounts with recent activity (30-day window)

```python
KPI(
    label="Sparse accounts active in last 30 days",
    subtitle=(
        "For each `sparse` account, healthy = at least one emitted "
        "balance row in the last 30 days. Looser bar than per-day "
        "judgment because individual sparse accounts may legitimately "
        "have no activity on any given day. Accounts with zero rows "
        "in 30 days are stale — likely closed, or the feed for that "
        "account specifically has dropped."
    ),
    metric=...,  # COUNT(DISTINCT account_id WHERE emitted_in_30d) / COUNT(DISTINCT sparse_account_id)
    bands=KPIValueThresholdBanding(
        green_at=Decimal("0.90"),
        amber_at=Decimal("0.50"),
    ),
)
```

**Window choice (30 days):** strawman, operator can tune at CL.11. 30 days is a billing-cycle / monthly-report rhythm — accounts truly idle for 30+ days warrant a look regardless of cadence policy.

### 9.c — Explicit-daily compliance (corollary)

For accounts declared `explicit_daily`, the `balance_cadence_gap` matview's `declared_daily_missing` row IS the per-day compliance signal — no separate KPI needed. The count of `gap_kind = 'declared_daily_missing'` rows in the current window is the natural metric, surfaceable as a single KPI on the L1 Exceptions sheet alongside other invariant counts.

Per BK.2 KPI banding precedent, the 3-band primitive (`KPIValueThresholdBanding`) already exists.

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
