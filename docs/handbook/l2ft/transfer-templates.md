# Transfer Templates

> **What this sheet teaches.** Multi-leg transfer flow — the degree to which each declared transfer template fires its expected legs and balances to the net declared by the L2 instance ([flow tracing](../_glossary.md#l2-flow-tracing--per-chain-transfer-integrity)) specification. Every row on this sheet is one shared Transfer instance, grouped by which template ([transfer template](../_glossary.md#template)) it matched, and measured against the L1 balance invariant and the L2 chain-completion invariant.

## What you're looking at

The sheet opens on a **Date From** / **Date To** filter spanning the date range, followed by **Template**, **Completion**, **Metadata Key**, and **Metadata Value** filters to narrow the data. Below the filter bar is an *Edge legend* text box naming the three flow types you'll see in the Sankey below. A *Multi-Leg Flow — Account → Template → Account* Sankey dominates the middle of the sheet, visualizing how money flows through each template's declared structure: debit accounts on the left feed into the template middle node, and credit accounts on the right receive. Matched chain children (declared follow-on rails that actually fired) appear as edges flowing out of the template; orphan chain children (declared but not firing) appear as dashed links with "(orphan)" appended to the node name. Below the Sankey sits the *Template Instances* table, showing one row per shared Transfer with balance detail and chain-completion status.

## How to read the numbers

The Sankey and Table both read from two companion datasets: `l2ft-tt-instances-ds` (one row per shared Transfer firing) and `l2ft-tt-legs-ds` (one row per leg or chain-child edge). Both datasets join the declared transfer templates from the L2 instance against the runtime `current_transactions` ledger to find all transfers whose `template_name` matches a declared template.

Each row in the *Template Instances* table carries:

- `template_name` — the declared transfer template this instance matched
- `transfer_id` — the unique identifier of the shared Transfer
- `posting` — the earliest posting timestamp of any leg in this transfer
- `expected_net` — the target net amount the L2 declared for this template (in dollars)
- `actual_net` — `SUM(amount_money)` of all legs with this `template_name` + `transfer_id` (in dollars)
- `net_diff` — `actual_net − expected_net` (in dollars; zero or near-zero is clean)
- `leg_count` — the number of legs that comprise this transfer
- `completion_status` — one of:
  - **'Complete'** — `ABS(actual_net − expected_net) < $0.01` (balance OK) AND every Required child chain fired AND every XOR group has exactly one fired member
  - **'Imbalanced'** — `ABS(actual_net − expected_net) >= $0.01` (L1 conservation breach)
  - **'Orphaned'** — balanced but a Required chain child didn't fire OR an XOR group has 0 or more than 1 fired members (L2 chain breach)

The Sankey's width represents `SUM(amount_abs)` across all matching legs on each edge. Template legs (the account-to-template-to-account spine) are always shown; chain-matched edges show declared follow-on rails that fired; chain-orphan edges are synthetic rows for declared chain children that didn't fire, making the Sankey a complete picture of what the L2 instance expected versus what the runtime delivered.

## Common patterns

### All Complete, every firing balanced and chained

Every row in the *Template Instances* table reads 'Complete'; the Sankey shows only thick flow edges with no "(orphan)" suffix nodes. This is the steady-state expectation — every declared template fires, balances, and every Required child leg posts as scheduled. If you're seeing this across multiple templates and dates, your transfer-template declarations match the runtime correctly.

### One template, many Imbalanced rows

Several rows for the same `template_name` carry `completion_status = 'Imbalanced'`, meaning the net amount on those transfer_ids diverges from the declared `expected_net`. The `net_diff` column shows how much: positive means the sum of postings is *higher* than the template expected, negative means *lower*. This is an L1 conservation breach — the ledger doesn't agree with itself at the template level. Cross to the **Rails** tab to inspect the individual legs and see whether a debit or credit was dropped or duplicated.

### Orphaned transfers across a chain

Multiple rows show `completion_status = 'Orphaned'` despite `net_diff` near zero. The Sankey shows thinner or missing edges flowing out of the template node to one or more child rail names. The template legs themselves fired (the account ↔ template flow is present), but one or more declared child chains didn't — meaning the declared multi-leg flow topology is incomplete. This is an L2 chain-completion breach. Check the L2 instance's chain declarations: is the declared Required child rail actually supposed to fire for this template, or was the declaration stale when the transfer fired?

### Sparse or empty Sankey despite table rows

The table shows rows but the Sankey appears thin or empty. This can happen when `leg_count` is low (few legs per transfer, so the Sankey's proportional widths collapse) or when metadata cascade filters are in effect. Widening the **Metadata Key** / **Metadata Value** filters or picking **Template = All** can expand the visible flow. Remember: the Sankey is capped to the top 30 source/target nodes by magnitude; if your L2 has 100+ accounts × any-template, the "Other" bucket absorbs the tail.

### Mismatch between Sankey and Table filters

You pick **Completion = Imbalanced** on the dropdown and the table narrows but the Sankey still shows "Complete" flows. Both the Table and Sankey filter together (same `template_name` / `completion_status` / date window), but the Sankey aggregates across all selected rows, so the visual may look coarser than the table. If the mismatch is stark (e.g., table shows "Imbalanced" but Sankey shows heavy flow), widen the **Date From** / **Date To** window to confirm the filtering is working as expected.

## What "no rows" means

A clean Transfer Templates sheet means every declared transfer template's runtime firings (1) balance to their declared expected_net and (2) fire all Required child chains and satisfy XOR group constraints. That is the steady-state expectation, not an edge case. If you see zero rows:

- **Confirm the filters aren't over-narrow.** A very specific **Template** + **Completion** combination with a narrow date window can legitimately show zero if no transfers matched that profile during that period. Widen the date range or pick **Template = All** to check.
- **Confirm matview freshness.** Cross to *App Info* and look for the transaction count on the *Matview Status* table. If it's zero, the ETL hasn't run yet. If it's positive, the SQL is dry — either no templates are declared in the L2 instance, or no postings carry a `template_name` matching a declared template.
- **Check the L2 instance.** If you're seeing an empty sheet across a 30-day window, confirm the L2 instance has `transfer_templates` declared and that postings in the system carry matching `template_name` metadata.

If *App Info* shows `last_refresh_at` as null or the matview row count as zero across the board, the L2 matview pipeline didn't run. That's an ops alert, not a "clean" signal.

## Cross-sheet drills

The Transfer Templates sheet does not define row-level drills. To inspect individual legs in detail, cross to the **Rails** tab and filter by **Rail** name; to inspect the parent chains, cross to the **Chains** tab and filter by **Chain** name.

## Related handbook pages

- [Rails — Transactions Explorer](rails.md) — the per-leg detail surface; use this when you need to see the full journal of every posting on a specific transfer.
- [Chains — Per-Instance Explorer](chains.md) — the per-chain firing surface; use this when you want to see whether the declared chain children fired for a parent transfer.
- [L2 Exceptions](l2-exceptions.md) — unified view of all six L2 hygiene checks; use this to understand what kinds of declaration-vs-runtime mismatches the L2 is detecting system-wide.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L2`, `template`, `chain`, `matview`, and the other project-specific terms.*
