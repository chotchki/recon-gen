# Money Trail

> **What this sheet teaches.** Recursive chain traversal — where did this transfer originate, and where does it go? Pick a chain root from the dropdown and follow a money movement through every hop, hop by hop.

## What you're looking at

The sheet opens on two side-by-side visuals: a *Money Trail — Chain Sankey* diagram on the left (two-thirds width) showing source account → target account ribbons for the selected chain, and a *Money Trail — Hop-by-Hop* detail table on the right (one-third width) listing every edge ordered by depth from root to leaf. Above both sit four control knobs: a **Chain root transfer** dropdown to select which transfer chain to visualize, a **Max hops** slider (default 5), a **Min hop amount ($)** slider (default $0.00), and a **Date Range** picker. The Sankey ribbon thickness represents the sum of hop amounts; single-leg transfers (raw external arrivals or point-of-sale deposits) appear as chain members in the hop table but don't render as visible ribbons because they have no source or target pair.

## How to read the numbers

Both the Sankey and the table read from the same underlying [matview](../_glossary.md#matview--materialized-view) — the `<prefix>_inv_money_trail_edges` recursive-CTE walk over the `parent_transfer_id` [chain](../_glossary.md#chain). Each row represents one edge in a chain: a transfer's contribution to the movement of money. The matview columns are:

- `root_transfer_id` — the top-most transfer in the chain (the one with no parent; the logical starting point)
- `transfer_id` — which transfer this edge belongs to
- `depth` — the hop's distance from the root (0 = root transfer itself)
- `source_account_id`, `source_account_name`, `source_account_type` — the account sending money in this hop
- `target_account_id`, `target_account_name`, `target_account_type` — the account receiving money in this hop
- `hop_amount` — the signed amount (in dollars) moving in this leg; the Sankey groups by source and target, summing across rows
- `posted_at` — the timestamp the target leg posted
- `rail_name` — which [rail](../_glossary.md#rail) (ACH, wire, check, on-us internal, etc.) this hop traveled on

The matview filters to multi-leg transfers only — edges require both a source (negative-signed) leg and a target (positive-signed) leg with `status='Posted'`. Single-leg transfers are chain members (they appear in the recursive walk and increment depth) but don't project as visible edges because they have only one leg, not a pair.

The *Money Trail — Chain Sankey* title shows the selected chain's visual: ribbons group accounts by name and [account_role](../_glossary.md#account-role) and thickness scales with SUM(hop_amount) across all edges between that source and target in the depth range you've selected. The *Money Trail — Hop-by-Hop* table on the right shows detail you can't see in the Sankey: every hop ordered by depth asc, with `depth`, `transfer_id`, `rail_name`, source and target account names, `posted_at`, and the hop amount. Ribbon size dominates the Sankey's visual; the table lets you count hops and check posting timestamps.

## Common patterns

### Single-leg transfer in a multi-leg chain

The hop table has one row at `depth=0` (the root) with only a target leg and no matching source. The Sankey shows nothing — a single leg has no ribbons. This is expected on raw external arrivals (a payment arriving from a banking network with no internal leg to debit first) or point-of-sale deposits (a customer's card payment arriving as pure inflow). The chain still exists and still has a root, but the initial transfer is unidirectional.

### Chain ends with a single-leg transfer

The hop table shows rows climbing through hops 0 → N, then depth N+1 appears with a single leg. The chain has "completed" in the sense that the money stops flowing — the final leg is an arrival or deposit that doesn't feed downstream. This is the expected end shape for chains that terminate in an external system (a bank returning cleared funds) or a leaf operational account (the final destination of the movement).

### Deep chain (hops exceed the default 5)

You've set the **Max hops** slider and the Sankey still shows the breadth you expect, but one "ribbon" appears to stop early. The hop table at the bottom rows showing `depth=6+` beyond your slider's cap. This means the chain is deeper than your filter: more money moved through more legs than you're currently viewing. Increase the slider to expose the downstream hops. Chains deeper than 10 hops are rare and suggest a pathological recursive structure (a chain that re-enters itself or traverses a loop); contact ops if you see this — data integrity may be in question.

### Only small hops visible (large hops hidden)

You've raised the **Min hop amount ($)** slider and the Sankey shows only thin ribbons or disappears entirely. The hop table still shows all hops at all depths, but their amounts fall below your threshold. This is a filter, not a data problem: the chain *has* those hops, but you've chosen to hide the small ones so you can focus on the dominant flows. Lower the slider to restore the full picture.

## What "no rows" means

An empty sheet (Sankey blank, hop table empty) when you've picked a chain root means one of three things:

- **The chain has no multi-leg edges.** All hops in this chain are single-leg transfers (external arrivals, deposits, or single-account adjustments). The hop table would show the depth=0 root, but the Sankey has nothing to render. This is clean — not a data problem, just a chain structure that doesn't visualize.
- **Your min-amount slider is set above the chain's largest hop.** All hops fall below your dollar threshold. Lower the **Min hop amount ($)** slider to $0 to restore the full chain, or increase it again if you're deliberately filtering for only the bulk moves.
- **Your max-hops slider is set below the root depth.** Rare, but if you've set **Max hops** to 0 and picked a non-root transfer, both the Sankey and table disappear. Raise the slider to at least 1 to see any hops.

If *App Info* shows `last_refresh_at` as null or the matview row count as zero across the board, the investigation-pipeline didn't run and the matviews are stale. That's an ops alert.

## Cross-sheet drills

The Money Trail sheet has no outbound drills. The visuals are all internal to the navigation — you pick a chain root in the dropdown and the Sankey + table re-render. The Sankey and table are locked to the sheet-level date range and the three parameter knobs (root, max hops, min amount).

Related pages may expose drills INTO this sheet from other investigation sheets (e.g., Recipient Fanout or Volume Anomalies may offer "walk this transfer's chain" actions), but those are wired on the source sheets, not here.

## Related handbook pages

- [Account Network](account-network.md) — the same matview (`<prefix>_inv_money_trail_edges`), viewed account-centrically rather than chain-rooted. Use it when you've identified an anchor account and want to see all money flowing in and out.
- [Recipient Fanout](recipient-fanout.md) — the investigation app's entry point for behavior. You may land here from a fanout row drill.
- [Volume Anomalies](volume-anomalies.md) — the z-score companion. Anomalies can link you here to visualize the chain.

---

*First time here? See the [Vocabulary](../_glossary.md) for `matview`, `chain`, `account_role`, `rail`, `transfer`, and other project-specific terms.*
