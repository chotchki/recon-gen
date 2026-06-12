# Account Network

> **What this sheet teaches.** Who does this account exchange money with, on either side? The *Account Network* sheet visualizes the directed graph of money movements flowing in and out of a chosen anchor account, distinguishing inbound counterparties (those sending money INTO your anchor) from outbound counterparties (those your anchor sends money TO).

## What you're looking at

Two Sankey diagrams dominate the top half of the sheet — *Inbound — counterparties → anchor* on the left and *Outbound — anchor → counterparties* on the right. Each ribbon's thickness represents the total dollar amount flowing along that edge. Below the Sankeys sits an *Account Network — Touching Edges* table listing every transfer leg connecting the anchor to a counterparty, ordered by amount descending. An *Anchor account* dropdown at the top left lets you pick which account to examine; a *Min hop amount ($)* slider filters noise edges below a dollar threshold.

The sheet uses the same underlying [matview](../_glossary.md#matview--materialized-view) as the *Money Trail* sheet (`inv_money_trail_edges`) but presents it from the anchor account's point of view rather than from a chain's point of view. Every row on this sheet is one transfer leg; both Sankeys and the table all narrow when you pick an anchor and adjust the slider.

## How to read the numbers

All three visuals (inbound Sankey, outbound Sankey, *Account Network — Touching Edges* table) read from the same `inv_money_trail_edges` matview, a recursive walk over the `transfer_parent_id` linkages in the base `transactions` table. Each row is one leg of a multi-leg transfer, capturing:

- `root_transfer_id` — the topmost transfer in the parent chain (the original money movement)
- `transfer_id` — the transfer this leg belongs to
- `depth` — how many hops this leg is from the root (0 = root itself)
- `source_account_id`, `source_account_name`, `source_account_type` — the account the money flows FROM
- `target_account_id`, `target_account_name`, `target_account_type` — the account the money flows TO
- `hop_amount` — the leg amount in dollars
- `posted_at` — when the leg posted
- `rail_name` — which transfer rail carried this leg (ACH, wire, internal, etc.)

The matview filters `WHERE status = 'Posted'` and `amount_money > 0` (targets only; negative amounts are sources by sign convention). The SQL joins each transfer's source leg (negative amount) to each target leg (positive amount) to emit one row per directed edge.

The two Sankeys are directionally filtered:

- **Inbound Sankey** — rows where `target_display = anchor` (the anchor is the receiving end). Ribbon source = counterparty, ribbon target = your anchor.
- **Outbound Sankey** — rows where `source_display = anchor` (your anchor is the sending end). Ribbon source = your anchor, ribbon target = counterparty.

The *Account Network — Touching Edges* table reads the bidirectional dataset (no direction filter) and displays every leg touching the anchor, adding a `counterparty_display` column (target when source is the anchor; source when target is the anchor) so the table's walk-the-flow drill can move to the other side.

The *Anchor account* dropdown parameter (`pInvANetworkAnchor`) narrows all three datasets via `(source_display = anchor OR target_display = anchor)` (bidirectional) or directional-specific predicates (inbound/outbound). The *Min hop amount ($)* parameter filters `hop_amount >= <<$pInvANetworkMinAmount>>` at the database.

## Common patterns

### Unbalanced inbound vs. outbound volume

One Sankey is visually dense; the other is sparse or empty. This is normal — most accounts have asymmetric flow (a customer receives deposits but sends only refunds, or a liquidity buffer account sweeps money out but rarely receives). The Sankey diagram simply reflects the directionality of money at that anchor.

### One dominant counterparty on each side

Most of the Inbound ribbon thickness comes from a single source; most of the Outbound thickness flows to a single target. In most institutions, this is expected — settlement accounts concentrate flow, operational accounts have a few key partners. Use the *Min hop amount ($)* slider to suppress small noise edges and see the bulk movement.

### Walk-the-flow discovery

Left-click any node in either Sankey to pivot the anchor to that counterparty. The sheet re-renders in place, showing that counterparty's inbound and outbound network. This is your path-following tool — follow a suspicious money trail through the graph one hop at a time.

### Empty Sankeys after anchor selection

You picked an anchor and the Sankeys render empty or nearly empty. This usually means:

- The anchor account has very little flow (check the base transactions table volume for that account ID).
- The anchor's flow is all below the *Min hop amount ($)* threshold (lower the slider to see micro-transfers).
- The anchor is a one-way valve (a pure sweeper or pure feeder with no counterflow).

### Cycles or bidirectional edges

You notice the same counterparty appears as both a ribbon source and target (money flowing both ways with the anchor). This is expected — many banking relationships are mutual (you send ACH, they send ACH back; you wire out, they wire in). The two Sankeys keep this clean: inbound shows their sends to you; outbound shows your sends to them.

## What "no rows" means

A clean empty sheet means one of the following:

- **No anchor selected.** On first paint, the dropdown shows no default — you must pick an anchor to populate the Sankeys and table. The sheet is waiting for your choice.
- **Anchor has no Posted legs.** The `inv_money_trail_edges` matview carries only Posted transactions (Pending legs don't render). If your anchor has only Pending or Failed legs, the sheet stays empty — use the Daily Statement sheet to see the Pending state for that account.
- **All flow is below the min-amount threshold.** If the anchor's legs are all smaller than the *Min hop amount ($)* slider value, the table renders empty. Lower the slider to see the micro-transfers.
- **Matview is stale.** Cross to *App Info* and check the *Matview Status* table's `inv_money_trail_edges` row. If `last_refresh_at` is older than the most recent posting and you've moved money through this anchor since, the dashboard is showing yesterday's state.

If the matview row count shows zero on the App Info sheet, the SQL is dry — no transfers in the entire system. That's an ops alert, not an empty-state signal.

## Cross-sheet drills

- **Inbound Sankey node** (left-click). Pivots the anchor to that counterparty and re-renders the Sankeys and table in place.
- **Outbound Sankey node** (left-click). Pivots the anchor to that counterparty and re-renders the Sankeys and table in place.
- **Touching Edges table row** (right-click → *Walk to other account on this edge*). Pivots the anchor to the non-anchor side of that edge (target when source was the anchor; source when target was) and re-renders the sheet.

## Related handbook pages

- [Money Trail](money-trail.md) — the transfer-chain complement; use when you want to see the full parent-to-leaf path a transfer took rather than the account-centric inbound/outbound split.
- [Daily Statement](../l1/daily-statement.md) — per-account narrative; drill here when you want to see every Pending + Posted leg for an anchor account on a specific day.
- [Transactions](../l1/transactions.md) — the raw leg list; the final destination when you need to inspect metadata or timestamps.

## QS parity notes

See [quirks log §dependent-dropdown-no-refresh](../../reference/quicksight-quirks.md) if the anchor dropdown shows "All" on screen but the data is correctly filtered to your selected anchor.

---

*First time here? See the [Vocabulary](../_glossary.md) for `matview`, `rail`, `chain`, and the other project-specific terms.*
