# Money Trail

> **What this sheet teaches.** Recursive chain traversal — where did this transfer originate, and where does it go? Pick a chain root from the dropdown and follow a money movement through every hop, hop by hop.

## What you're looking at

Two side-by-side visuals open the sheet: a *Money Trail — Chain Sankey* diagram on the left (two-thirds width) showing source account → target account ribbons for the selected chain, and a *Money Trail — Hop-by-Hop* detail table on the right (one-third width) listing every edge ordered by depth from root to leaf. Above both sit four control knobs: a **Chain root transfer** dropdown to select which transfer chain to visualize, a **Max hops** slider (default 5), a **Min hop amount ($)** slider (default $0.00) and a **Date Range** picker. Sankey ribbon thickness represents the sum of hop amounts. Single-leg transfers (raw external arrivals or point-of-sale deposits) belong to the chain but never surface in either visual — a rendered edge needs both a source leg and a target leg, and a single leg is only one half.

## How to read the numbers

Both the Sankey and the table read from the same underlying [matview](../_glossary.md#matview--materialized-view) — the `<prefix>_inv_money_trail_edges` recursive-CTE walk over the `transfer_parent_id` [chain](../_glossary.md#chain). Each row represents one edge in a chain: a transfer's contribution to the movement of money. The matview columns are:

- `root_transfer_id` — the top-most transfer in the chain (the one with no parent; the logical starting point)
- `transfer_id` — which transfer this edge belongs to
- `depth` — the hop's distance from the root (0 = root transfer itself)
- `source_account_id`, `source_account_name`, `source_account_type` — the account sending money in this hop
- `target_account_id`, `target_account_name`, `target_account_type` — the account receiving money in this hop
- `hop_amount` — the dollar amount moving through this leg (the inflow/target leg, so always positive); the Sankey groups by source and target, summing across rows
- `posted_at` — the timestamp the target leg posted
- `rail_name` — which [rail](../_glossary.md#rail) (ACH, wire, check, on-us internal, etc.) this hop traveled on

The matview filters to multi-leg transfers only — edges require both a source (negative-signed) leg and a target (positive-signed) leg with `status='Posted'`. Single-leg transfers are chain members (they appear in the recursive walk and increment depth) but don't project as visible edges because they have only one leg, not a pair.

The *Money Trail — Chain Sankey* title shows the selected chain's visual: ribbons group accounts by name and [account_role](../_glossary.md#account-role) and thickness scales with SUM(hop_amount) across all edges between that source and target in the depth range you've selected. The *Money Trail — Hop-by-Hop* table on the right shows detail you can't see in the Sankey: every hop ordered by depth asc, with `depth`, `transfer_id`, `rail_name`, source and target account names, `posted_at` and the hop amount. Ribbon size dominates the Sankey's visual; the table lets you count hops and check posting timestamps.

## Common patterns

### Single-leg transfer in a multi-leg chain

You pick a chain whose root is a single-leg transfer — a raw external arrival (a payment arriving from a banking network with no internal leg to debit first) or a point-of-sale deposit (a customer's card payment arriving as pure inflow). The root never renders as an edge: the matview emits a row only where a transfer has both a source leg and a target leg, and a single leg is half of that. So the hop table starts at the first multi-leg hop (`depth=1` or deeper) and the Sankey draws only the descendant ribbons. The chain still exists and still has a root, the opening transfer just doesn't visualize.

### Chain ends with a single-leg transfer

The hop table climbs through the multi-leg hops and then simply stops — the terminal arrival or deposit is a single leg, so it produces no edge row and the table tops out at the last paired hop. The chain has "completed" in the sense that the money stops flowing: the final transfer is an arrival or deposit that doesn't feed downstream. This is the expected end shape for chains that terminate in an external system (a bank returning cleared funds) or a leaf operational account (the final destination of the movement).

### Deep chain (hops exceed the default 5)

You've capped **Max hops** low and a ribbon appears to stop early. Both visuals share the same `depth <= max-hops` filter, so the hop table tops out at your cap too — the deeper hops aren't fetched at all, on either side. The chain runs deeper than your filter: more money moved through more legs than you're viewing. Raise the slider to expose the downstream hops. Chains deeper than 10 hops are rare and suggest a pathological recursive structure (a chain that re-enters itself or traverses a loop); contact ops if you see this — data integrity may be in question.

### Only small hops visible (large hops hidden)

You've raised the **Min hop amount ($)** slider and the Sankey thins out or disappears — only hops at or above your threshold survive. Both the Sankey and the hop table read the same filtered dataset, so the table drops the sub-threshold hops too. This is a filter, not a data problem: the chain HAS those small hops, you've chosen to hide them so you can focus on the dominant flows. Lower the slider to restore the full picture.

## What "no rows" means

An empty sheet (Sankey blank, hop table empty) when you've picked a chain root means one of three things:

- **The chain has no multi-leg edges.** All hops in this chain are single-leg transfers (external arrivals, deposits or single-account adjustments). The matview emits no edge for any of them — an edge needs both a source leg and a target leg — so the Sankey AND the hop table both come up empty. This is clean, not a data problem, just a chain structure that doesn't visualize.
- **Your min-amount slider is set above the chain's largest hop.** All hops fall below your dollar threshold. Lower the **Min hop amount ($)** slider to $0 to restore the full chain, or increase it again if you're deliberately filtering for only the bulk moves.
- **Your max-hops slider is below the chain's first paired hop.** The slider floors at 1, so the root and its immediate edges always survive — but a chain whose multi-leg hops all sit deeper than your cap renders empty on both visuals. Raise **Max hops** to expose them.

If *App Info* shows the matview `row_count` as zero across the board, the investigation pipeline didn't run and the matviews are stale. (`<prefix>_inv_money_trail_edges` carries no date dimension, so its `latest_date` reads null even when healthy — check the row count, not the date.) That's an ops alert.

## Cross-sheet drills

The Money Trail sheet has no outbound drills. The visuals are all internal to the navigation — you pick a chain root in the dropdown and the Sankey + table re-render. The Sankey and table are locked to the sheet-level date range and the three parameter knobs (root, max hops, min amount).

Related pages may expose drills INTO this sheet from other investigation sheets (e.g., Recipient Fanout or Volume Anomalies may offer "walk this transfer's chain" actions), but those are wired on the source sheets, not here.

## Related handbook pages

- [Account Network](account-network.md) — the same matview (`<prefix>_inv_money_trail_edges`), viewed account-centrically rather than chain-rooted. Use it when you've identified an anchor account and want to see all money flowing in and out.
- [Recipient Fanout](fanout.md) — the investigation app's entry point for behavior. You may land here from a fanout row drill.
- [Volume Anomalies](anomalies.md) — the z-score companion. Anomalies can link you here to visualize the chain.

---

*First time here? See the [Vocabulary](../_glossary.md) for `matview`, `chain`, `account_role`, `rail`, `transfer` and other project-specific terms.*
