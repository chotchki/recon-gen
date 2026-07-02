# How do I validate a single account-day after a load?

*Engineering walkthrough — Data Integration Team. Foundational.*

## The story

You loaded a slice into `{{ l2_instance_name }}_transactions` and
`{{ l2_instance_name }}_daily_balances`. The pre-flight invariants from
[How do I prove my ETL is working?](how-do-i-prove-my-etl-is-working.md)
returned zero rows, the dashboards render and the L1 Exceptions
KPIs look reasonable. The whole feed LOOKS fine.

Now your treasury counterpart calls and asks: *"Did my account
`gl-1850` actually reconcile yesterday? Show me opening, the
moves, closing — and prove the drift is zero."* You don't want to
hand them a SQL transcript; you want one screen that answers all
four questions in the same place.

That screen is the **Daily Statement** sheet on the L1
Reconciliation Dashboard — a parameter-bound view of one account
on one date. Pick the account, pick the date and the page renders
the account-day walk: opening balance, signed debits, signed
credits, closing stored, the Posting Drift KPI and the day's legs
as a table.

[See it live](https://recon-gen-spec.hotchkiss.io/)

## The question

"For one specific `(account_id, business_day_start)` pair from my
freshly loaded slice, can I open a single screen that confirms
the day reconciles end-to-end — and shows me every leg that
moved the balance?"

## Where to look

One sheet, two reference points:

- **L1 Reconciliation Dashboard → Daily Statement tab**. Two
  sheet controls at the top: an **Account** dropdown and a
  **Business Day** picker. Pick them and the whole page
  re-renders for that account-day.
- **`docs/Schema_v6.md` → Sign convention** — the invariant the
  Posting Drift KPI checks (`stored balance = opening + net flow`)
  is the cumulative-sum identity from the column contract. The
  Daily Statement KPI is the row-level expression of that invariant.

You can land on the Daily Statement sheet two ways:

1. **Tab-click + manual select.** Click the "Daily Statement"
   tab, then pick the account + date you want to inspect.
2. **Right-click drill from another L1 sheet.** From L1
   Exceptions or Drift, right-click any row and choose "View
   Daily Statement for this account-day" — the parameters are
   filled in for you. (Per the drill-direction convention:
   right-click moves you deeper into the investigation.)

## What you'll see in the demo

Open the dashboard, switch to the Daily Statement tab and try the
three pinned scenarios below. Each is a different shape the sheet
surfaces — clean, drift, overdraft — and together they cover the
"is this account-day OK?" decision tree.

### Scenario 1 — A clean reconciling day

Pick any GL control account in your L2 (e.g. a `gl-1010-cash-*`
ledger entry); set Business Day to yesterday. You should see:

- **Opening Balance**, **Debits (signed)**, **Credits (signed)**
  and **Closing Stored** all populated with non-zero values from
  the day's posting activity.
- **Posting Drift = $0.00**, green ✓ — stored closing equals
  opening plus net flow.
- **Posted Money Records** table — at least one leg; each row
  shows its direction (Debit / Credit), signed amount and the
  running balance after that posting.

This is what every account-day in your slice should look like
when the projection is clean.

{% if vocab.demo.drift_account %}
### Scenario 2 — A drift day

| Parameter      | Value                                            |
| -------------- | ------------------------------------------------ |
| Account        | `{{ vocab.demo.drift_account.id }}`              |
| Business Day   | the planted `days_ago` from the seed report      |

You should see:

- **Posting Drift ≠ $0.00** — stored closing balance disagrees
  with what the legs in the table account for. The KPI's green ✓
  flips to a red ✗.
- **Posted Money Records** table renders normally — every leg has
  the right direction, status and signed amount. The drift isn't
  IN a leg; it's the absence of a leg that should have been there
  (or the presence of a balance row that shouldn't have been
  bumped).

This is the planted drift scenario — the demo seeds an
unexplained delta into the {{ vocab.demo.drift_account.name }}
balance row to give the L1 Drift sheet something to surface. In
a real ETL, the same shape would mean either a posting was
dropped on its way to `{{ l2_instance_name }}_transactions` or an EOD `money`
value was written that doesn't match the postings feed.
{% endif %}

{% if vocab.demo.overdraft_account %}
### Scenario 3 — An overdraft day

| Parameter      | Value                                                |
| -------------- | ---------------------------------------------------- |
| Account        | `{{ vocab.demo.overdraft_account.id }}`              |
| Business Day   | the planted `days_ago` from the seed report          |

You should see:

- **Closing Stored** is NEGATIVE — the account ended the day
  overdrawn.
- **Posting Drift = $0.00** — the overdraft is real. A large
  outbound leg drove the balance below zero, the leg is in the
  table and the stored balance reflects it correctly.
- **Posted Money Records** table includes the offending outbound
  leg.

This is the planted overdraft scenario for
{{ vocab.demo.overdraft_account.name }}. The distinction between
Scenarios 2 and 3 is the one to keep straight: drift is a
bookkeeping break, overdraft is a real balance condition. The
Daily Statement separates them cleanly because the same row
carries both signals.
{% endif %}

{% if not vocab.demo.drift_account %}
!!! note "Worked examples need planted scenarios"
    The drift / overdraft worked examples render against the
    active L2's planted scenarios. The current L2
    (`{{ l2_instance_name }}`) has no plants of those kinds in its
    `default_scenario_for(l2)` output, so this walkthrough renders
    only the clean-day scenario above. To see the full sequence,
    re-render against an L2 with planted scenarios — e.g.
    `RECON_GEN_DOCS_L2_INSTANCE=tests/l2/sasquatch_pr.yaml mkdocs serve`.
{% endif %}

## What it means

Each KPI on the strip answers a specific question, the table
answers the rest:

- **Opening Balance** — the prior emit's end-of-day stored
  balance for this account, read from the most recent
  `{{ l2_instance_name }}_daily_balances` row before this day. Null on a
  day you expected to have history means your daily-balances feed
  has a gap.
- **Debits (signed)** / **Credits (signed)** — the day's flows
  split by direction and kept SIGNED (not absolute). By the v6
  convention Debit = negative (money out), Credit = positive
  (money in), so `Closing = Opening + Credits + Debits` — the
  signs already carry direction, you do NOT subtract.
- **Closing Stored** — the value in `{{ l2_instance_name }}_daily_balances`
  that your ETL wrote. Authoritative for the row-as-loaded.
- **Posting Drift** — `stored − (opening + net flow)`. Zero means
  the posting feed and balance feed agree on this account-day.
  Non-zero means one of the two is wrong.
- **Posted Money Records** — every Posted leg that moved this
  account on this date, with its direction, signed amount and the
  running balance after each posting. Right-click any leg → "View
  Transactions for this transfer" to see the OTHER side (the
  counterparty leg, usually posted to a different account).

A clean account-day:

- Posting Drift = 0 (the two feeds agree).
- Closing ≥ 0 (no overdraft).
- Every transfer resolves to its counterpart leg when you drill
  it (chains intact).

If any of those three is off, the row is an exception. The
sheet's purpose is to make it the work of about ten seconds
to confirm or disprove all three for one specific account-day.

## Drilling in

A few patterns the sheet exposes that the morning rollups
collapse away:

- **Drift sign tells you which feed is wrong.** Positive drift
  (stored higher than recomputed) means a posting is missing
  from `{{ l2_instance_name }}_transactions` OR the balance was bumped above
  what the postings explain. Negative drift is the opposite —
  most often a duplicate or oversigned leg in
  `{{ l2_instance_name }}_transactions`.
- **A one-sided transfer means the chain is broken.** Right-click
  a leg → "View Transactions for this transfer" to land on every
  leg of that `transfer_id`. If the counterpart leg isn't there,
  that's the visible symptom of an Invariant 1 violation
  ([pre-flight](how-do-i-prove-my-etl-is-working.md)) — the
  transfer has only one side loaded.
- **A high leg count for a quiet account is a signal.** Most
  customer DDAs see ≤10 legs on a normal day. Twenty-plus on a
  single day usually means a batch was written without a
  `transfer_id` collapse — each line in the source file became
  its own transfer instead of legs of one transfer.
- **Overdraft on a sub-ledger ≠ drift.** The L1 Overdraft check
  fires on `closing < 0`; the L1 Drift check fires on `stored ≠
  recomputed`. The planted overdraft account
  ({{ vocab.demo.overdraft_account.name if vocab.demo.overdraft_account else "see Scenario 3 above" }})
  has the first without the second. Treat them as orthogonal
  checks even though they share a row on the Daily Statement.

## Next step

Once you've opened the Daily Statement on a freshly loaded
account-day and the three signals look clean (drift = 0,
closing ≥ 0, every transfer resolves to its counterpart leg):

1. **Spot-check three more account-days from the same load.**
   The pre-flight catches universal violations; the Daily
   Statement catches account-specific ones. Sample one GL
   control account, one customer DDA, one external counter —
   different shapes, different ways the same projection bug
   surfaces.
2. **If drift is non-zero**, jump to
   [What do I do when the demo passes but my prod data fails?](what-do-i-do-when-demo-passes-but-prod-fails.md)
   Symptom 4 — it walks the three sub-causes (sign-flip,
   missing posting, business_day_start mismatch) with one-shot
   SQL for each.
3. **If a leg's transfer has no counterpart when you drill it**,
   you've got an Invariant 1 violation. Re-run pre-flight
   Invariant 1 scoped to that `transfer_id` to confirm, then
   audit the projection branch that emitted the leg.
4. **Hand the screen to your treasury counterpart.** Once the
   Daily Statement shows a clean account-day on a real account
   from your load, screenshot it and send it to whoever owns
   the account. They'll spot semantic issues (wrong amounts,
   wrong memos, unexpected counter-accounts) that no SQL
   invariant can catch.

## Related walkthroughs

- [How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md) —
  the universal pre-flight invariants. The Daily Statement is
  the single-account-day version of those invariants, rendered
  as a screen instead of a SQL transcript.
- [What do I do when the demo passes but my prod data fails?](what-do-i-do-when-demo-passes-but-prod-fails.md) —
  Symptom 4 (Drift KPI fires unexpectedly) is the natural
  follow-up when the Daily Statement shows a non-zero drift.
- [How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](how-do-i-populate-transactions.md) —
  the projection that this sheet reads. A drift here usually
  traces to a branch of that projection.
- [L1 Reconciliation Dashboard: Drift](../l1/drift.md) /
  [Drift Timelines](../l1/drift-timelines.md) — the
  dashboard-side aggregate views of the per-day drift the
  Daily Statement makes inspectable.
- [L1 Reconciliation Dashboard: Overdraft](../l1/overdraft.md) —
  the aggregate view of overdraft days; the Daily Statement is
  where you confirm the overdraft is real (not a drift symptom
  in disguise).
