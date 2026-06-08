# Getting Started

> **What this dashboard is for.** Board-cadence summary of the bank's account coverage, transaction throughput, money movement, and ledger integrity. Scan for trends in a 30-day rolling window; click any row or bar to drill into operational details from the Account Reconciliation dashboard.

## What you're looking at

The Getting Started sheet is a navigation layer — you won't find KPIs or visuals here, only text boxes that preview each of the four operational sheets below. The dashboard opens with a welcome message explaining the clickability model: accent-coloured cells are interactive. Click a cell in one of the sheet descriptions to jump to that tab and start investigating. The descriptions beneath each sheet name tell you what you're about to see — *Program Health* (a single tripwire count), *Account Coverage* (open vs active account counts and detail tables), *Transaction Volume Over Time* (daily and period tallies by transfer type), and *Money Moved* (net and gross dollars by rail).

## How to read the dashboard

Each sheet in this dashboard is built to answer one question at the 30-day executive cadence:

**Program Health** — Is the ledger clean? A single threshold-banded KPI tile shows the count of L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) invariant violations open in the selected date window. Green means zero violations; amber means at least one; red means 20 or more — a systemic mark the board should see flagged. Open the L1 Dashboard (linked from the sheet) for per-row triage. This is the speedbump check: *before* reading the volume trends below, confirm nothing is broken at the accounts layer.

**Account Coverage** — Which accounts do we have on the books, and how many are actually moving money? Two KPIs show the total (all-time) open-account count and the narrower active-account count within the date window. Two bar charts break both down by `account_type` so you can see the shape of the deposit base next to the GL control accounts that drive operations. A detail table lists every account with its last activity date and a transaction-leg count, sorted by activity so the busiest accounts surface at the top.

**Transaction Volume Over Time** — What is the daily transaction throughput, and how is it composed by transfer type? Three KPIs report the total transfer count (per-transfer deduped, Posted-only), the sibling per-leg all-status count (shown for App Info parity), and the average daily volume over active days. A stacked-bar chart by `rail_name` shows daily counts over time (trend plus composition); a clustered bar on a log scale shows the period total per rail so the long tail of smaller rails stays readable when one rail dominates.

**Money Moved** — What is the total dollar volume, and which direction is the net? Two KPIs report the net money moved (`Σ signed amount` — expected near zero on a balanced book, positive for net inflow, negative for net outflow) with a sign glyph, and the gross handle (`Σ absolute amounts` regardless of direction). A stacked bar by `rail_name` shows daily gross dollars over time; a log-scale clustered bar shows period gross by rail so the executive can see where the volume is coming from without the long tail disappearing.

## Common workflows on this dashboard

**Spot-check the ledger health first.** Open the *Program Health* sheet and scan the KPI tile. If it's green (zero), the books are honest; move to coverage + volume. If it's amber or red, drill to the L1 Dashboard, pick the relevant account / date / check_type, and triage. Ledger breaks are upstream of everything else — volume numbers don't matter if the balances don't reconcile.

**Scan account coverage against volume trends.** Move to *Account Coverage* and check whether the active-account count equals the total open count. If they're equal, every open account moved money in the window — a healthy signal. If the gap is large, you have idle accounts; the detail table sorts by activity so you can see which ones. Then flip to *Transaction Volume Over Time* to see if that coverage gap explains a volume dip (you have more accounts but lower daily throughput, possibly due to dormant accounts dragging the average).

**Assess the money composition.** Open *Money Moved* and check the net vs gross. On a balanced institution, net should be near zero (every outbound transfer is offset by an inbound); a large positive net means deposits exceeded payouts in the window, and large negative means payouts exceeded deposits. Use the gross-dollar bar chart and the log-scale period breakdown to see which rails moved the most handle. Compare this against the *Transaction Volume Over Time* count to spot volume-per-rail spikes (volume jumped on a specific rail but dollars didn't, or vice versa — suggests a composition shift in what people are moving).

**Drill from a bar into Account Reconciliation details.** The sheet descriptions are clickable at the top of each sheet (in the accent color). When you've seen the pattern you're looking for (a volume spike on the ACH rail, for example), click it to drill to Account Reconciliation with the rail pre-filtered. That's where you see the actual transaction detail (legs, statuses, timestamps, metadata) for investigation.

## Where to start

**First time opening the dashboard.** Start at *Program Health* — it's the first sheet after Getting Started and takes 10 seconds to read. If the KPI is green, move down through *Account Coverage* → *Volume* → *Money Moved* in order. If it's amber or red, open the L1 Dashboard link and triage the ledger breaks first; you'll come back to the Executives sheets once that's resolved.

**Month-end check.** Skim all four sheets' KPIs + the top chart (daily or open-count bar) to spot trends — are volumes growing, shrinking, or flat? Has coverage expanded? Is the ledger clean? These four visuals tell the story in under two minutes. If something looks off, drill into the detail table or the Account Reconciliation tab for a closer look.

**On-call alert.** The Program Health KPI is your alert surface — if it goes red (≥20 violations), something broke upstream. Click the L1 Dashboard link and find the violation. The account + date it shows are your starting point. Don't close the alert until the KPI is back to green.

## Related handbook pages

- [Program Health](../executives/program-health.md) — the ledger-integrity tripwire and how to read the threshold bands.
- [Account Coverage](../executives/account-coverage.md) — open vs active account semantics and the detail-table sort order.
- [Transaction Volume Over Time](../executives/transaction-volume.md) — the per-transfer vs per-leg distinction and log-scale interpretation.
- [Money Moved](../executives/money-moved.md) — net vs gross money semantics and sign indicators.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`, `account_role`, `rail`, `template`, `carry-forward`, and the other project-specific terms.*
