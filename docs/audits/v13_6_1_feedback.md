# recon-gen v13.5.0→v13.6.1 — upstream feedback (sparse-feed findings)

Companion to **`phase3_review_v13_5_0_effective_balances_UPSTREAM.md`** (the carry-day
`effective_balances` release-blocker — already filed, fixed in v13.6.1, re-verified: 0 duplicate
`(account, business_day_start)` rows on bundled `spec_example` + on a sparse-cadence instance; a
`(account_id, business_day_start)` uniqueness assertion on `effective_balances` is the suggested
regression guard).

Finding 1 below is **HIGH** — the Daily Statement (a headline CL surface) goes blank for
sparse-reporting accounts, which is CL's *target* use case. Findings 2–4 are minor / questions.

---

## 1. (HIGH) Daily Statement renders blank for sparse accounts on the default (anchor) day

**Symptom:** with a genuinely sparse balance feed (accounts report a balance only on activity /
sync days — the CL `sparse` cadence), the Daily Statement won't load for an account: you pick the
account, the statement is blank, until you manually move the date picker to a day that account
happened to report.

**Mechanism (all confirmed in source):**

1. The Daily Statement balance-date picker **defaults to a single anchor day** —
   `AsOfFrame` (`common/as_of_frame.py`): `LOCKED_ANCHOR = date(2030, 1, 1)` for locked binding, or
   `date.today()` for `live()`.
2. `build_daily_statement_summary_dataset` (`apps/l1_dashboard/datasets.py`) does **strict
   `business_day_start = <anchor>` equality**. Its own comment notes the latest-on-empty fallback
   was **removed in AR.2**: *"anchor-day with no data ⇒ blank statement."*
3. A **sparse account very likely has no emitted balance on that exact anchor day.**
4. The CL carry-forward is *supposed* to cover this — but `effective_balances`
   (`common/l2/schema.py`) builds its calendar from `in_scope_calendar_days = SELECT DISTINCT
   business_day_start FROM current_daily_balances` — i.e. **only days some account reported, not a
   full business-day calendar.** So if *nobody* reported on the anchor day (common on a sparse
   feed), the day isn't in the spine and **no account** gets a carried row there.

Net: a single fixed anchor day + strict equality + no fallback + an emitted-days-only carry-forward
spine = blank Daily Statement for sparse accounts on the default date. The `DateView` is *declared*
`EmptyBehavior.LATEST_ON_EMPTY` (fall back to the latest day with data ≤ anchor), but the SQL no
longer honors it — AR.2 dropped it **assuming dense data**, which is exactly the assumption a
sparse feed violates.

**Repro (bundled fixture):**
```bash
# seed any instance, then simulate a fleet-wide no-report anchor day:
recon-gen schema apply  --l2 tests/l2/spec_example.yaml -c <duckdb-cfg> --execute
recon-gen data   apply  --l2 tests/l2/spec_example.yaml -c <duckdb-cfg> --execute
recon-gen data  refresh --l2 tests/l2/spec_example.yaml -c <duckdb-cfg> --execute
# delete every balance row for the latest business day, refresh, and that day
# disappears from daily_statement_summary for ALL accounts — even ones with a
# well-defined carried balance and same-day activity:
DELETE FROM demo_daily_balances WHERE business_day_start = (SELECT max(business_day_start) FROM demo_daily_balances);
-- recon-gen data refresh … --execute
SELECT count(*) FROM demo_daily_statement_summary
WHERE business_day_start = (SELECT max(business_day_start) FROM demo_current_daily_balances);  -- rows for the new latest day
```
(Verified on a sparse-cadence instance: deleting one day's balances fleet-wide drops that day
entirely from the Daily Statement — 0 rows for any account — despite carried balances + activity.)

**Fixes (any one unblocks; ranked):**
1. **Re-honor `LATEST_ON_EMPTY` in the Daily Statement SQL** — resolve the displayed day to the
   latest day ≤ anchor *that the account has a row for*. Smallest fix; matches the view's own
   declared semantics.
2. **Make the `effective_balances` spine a complete business-day calendar** (`generate_series`
   over business days) instead of emitted-days-only — then every account has a carried row on every
   business day incl. the anchor. One change, also fixes the fleet-quiet-day-vanishing case.
3. **Data-derive the anchor** (max balance day) for live feeds rather than a fixed date.

---

## 2. (minor, editor) `balance_cadence` select renders blank for unset accounts

When `balance_cadence` is unset (`None`) the Account/AccountTemplate edit form shows the empty
`<option value="">` selected, while help text says *"Sparse (default)."* The control gives no
indication that `None` resolves to `sparse`. A declared value populates correctly
(`<option value="sparse" selected>`). **Fix:** pre-select `sparse` on `None`, or label the empty
option `Sparse (default)`.

## 3. (minor, render) `bullets()` strips `<br/>`, collapsing multi-paragraph descriptions

`apps/l1_dashboard/app.py` `bullets()` strips `<br/>` from `<li>` (QS rejects it), silently
collapsing the paragraph breaks of any entity whose `description` has blank-line-separated
paragraphs (`\n\n`); dozens of `UserWarning: bullets(): stripped <br/> …` at startup. **Fix
(matches the warning's hint):** split a `\n\n`-separated description into multiple `<li>`s, or
render the body as block content that admits paragraph breaks.

## 4. (question) `balance_cadence_gap` vs Daily-Statement `carried_with_activity_gap` diverge on mixed-cadence instances

On bundled `spec_example` (post-v13.6.1, clean): L1 `balance_cadence_gap` reports **1499** rows
while `daily_statement_summary.carried_with_activity_gap` reports **9** — same data, both genuine
(no dups). It's a scope difference: `balance_cadence_gap` counts the full account × in-scope-day
spine incl. `declared_daily_missing` (130; `explicit_daily` accounts) **and** pre-first-emit
`sparse_with_activity`; the Daily Statement counts only post-first-emit closing carries
(`WHERE closing_balance_carried IS NOT NULL`). **Question:** is the L1 `balance_cadence_gap` card
meant to count pre-first-emit activity days? If not it may overcount on instances mixing
`explicit_daily` accounts with heavy sparsity. An all-sparse instance that emits on every activity
day reports 0 on both (coherent), so this only bites mixed-cadence fixtures.

---

Findings 1–4 reproduce on bundled fixtures / the public editor; no instance-specific context.
