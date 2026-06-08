# Chains

> **What this sheet teaches.** Chain completion status — whether every declared child of a parent transfer fired when it should have. Each row on this sheet is one parent transfer instance; *Completed* means all Required children appeared and XOR groups honored their cardinality; *Incomplete* means at least one Required child is missing or an XOR group was orphaned / duplicated.

## What you're looking at

The sheet opens on two filter controls — **Date From** and **Date To** to scope by posting date, **Chain** (multi-select) to narrow by declared parent rail / template name, and **Completion** (multi-select) to show only Completed or Incomplete firings. Below the filters sit a single table: *Chain Instances*, one row per parent transfer firing. Each row reports the parent's posted date, declared chain name, transfer ID, completion status, the count of Required children that fired, the count of Required children declared, the parent amount in dollars, and the parent leg's status. A metadata cascade (Key + Value dropdowns) narrows further by transaction metadata when needed.

## How to read the numbers

The table reads from the `l2ft-chain-instances` dataset, which joins the L2's declared chain ([chain](../_glossary.md#chain--a-declared-sequence-of-leg-patterns)) topology to runtime parent firing counts and matched-child detection. Each row represents one distinct `parent_transfer_id` — a single parent transfer that fired and triggered child chains as follow-ons.

The columns on the table:

- `parent_posting` — the business date the parent leg posted
- `parent_chain_name` — the L2-declared parent rail or transfer [template](../_glossary.md#template--a-blueprint-for-transfer-structure) name
- `parent_transfer_id` — the unique transfer ID of the parent firing
- `completion_status` — one of:
  - *Completed* — every Required child [chain](../_glossary.md#chain--a-declared-sequence-of-leg-patterns) fired against this parent transfer_id AND every XOR-group (mutually-exclusive leg set) had exactly one member fire
  - *Incomplete* — at least one Required child is missing, or any XOR group was orphaned (zero fires) or duplicated (>1 fires)
- `required_fired` / `required_total` — two Required Children columns. `required_total` is the count of Required child chains the L2 declared for this parent; `required_fired` is how many actually posted. A chain with only XOR-sibling children (optional-by-design) declares zero Required, so a 0/0 row is normal and healthy for those parents.
- `parent_amount_money` — the posted amount in dollars (cents converted from the ledger)
- `parent_status` — the status of the parent leg (Pending / Posted / Failed)

The dataset filters `WHERE parent_posting >= <<date_start>> AND parent_posting <= <<date_end>>` (pushdown from the Date pickers) and applies the Chain and Completion dropdowns via sentinel-guarded `IN (...)` clauses — clearing a dropdown reverts to "match all rows". The metadata cascade (Metadata Key + Value) applies a JSONPath `IN (...)` predicate on the parent's metadata JSON.

## Common patterns

### Completed with 0/0 required children

A row with `completion_status='Completed'`, `required_fired=0`, and `required_total=0`. This parent's declared children are all XOR-group members — exactly one SHOULD fire, and the system found exactly one (or zero XOR groups entirely). A 0/0 completion is healthy, not a violation.

### Incomplete: missing required children

A row with `completion_status='Incomplete'` where `required_fired < required_total`. One or more declared Required children never posted against this parent transfer ID. This is the signature of an incomplete chain firing — the parent fired but one or more downstream legs the L2 declared never materialized. Drill down into the L2 Exceptions sheet (right-click → *View in Chains*) to narrow to this chain parent and see which children are orphaned. Then cross to the Rails sheet to inspect the parent's postings and metadata, confirming whether upstream conditions should have triggered the child.

### Incomplete: XOR violation

A row with `completion_status='Incomplete'` but `required_fired = required_total`. The Required children count matches, but an XOR group was orphaned or duplicated — zero members of a mutually-exclusive group fired, or more than one fired. XOR groups are declared as *exactly one SHOULD fire per parent instance*; the SQL detects both under-fire (orphan) and over-fire (violation) cases and lumps them into Incomplete. The table doesn't distinguish the XOR failure mode (it counts cardinality mismatches, not the specific group), so compare your L2 declaration's xor_group definitions to the metadata and parent transfer ID to diagnose which group is broken.

### High magnitude parent with incomplete children

An Incomplete row with a large `parent_amount_money` and low `required_fired` count. A substantial transfer fired but its declared downstream legs didn't follow. This pattern often indicates a configuration mismatch between the L2 declaration and the actual flow topology — the parent rail / template is real, but the child chains the L2 declared don't match what the runtime actually produces. Escalate to the L2 owner to audit the chain declarations (are the child names correct? are the parent-to-child routing rules up-to-date?).

### No rows in window

A blank table with no Completed or Incomplete rows. Either no parent transfers fired in the date range, or the Chain / Completion filters are too narrow. Widen the date window or clear the Chain dropdown; if you still get zero rows, no declared chain parents were exercised during that period — check the Rails sheet to confirm postings exist, and confirm the parent rail / template names in your L2 declaration match the actual `rail_name` / `template_name` values in the ledger.

## What "no rows" means

An empty *Chain Instances* table can mean several things:

- **No declared chains in the L2.** If your instance has no chain declarations at all, the table is always empty. Cross to *Getting Started* and confirm the L2 instance's `chains:` block is populated.
- **No chain parents fired in the date window.** The L2 declares chains but no parent transfer fired during the selected dates. Widen the date range; if still empty, check the Rails sheet (same date filter) to see whether any postings exist at all.
- **Filter too narrow.** You've selected a specific Chain name or Completion status that has no matches in the window. Clear the filters and try again.
- **Matview stale.** Cross to *App Info* and check the transaction matview's `last_refresh_at` timestamp. If it predates your expected chain firings, the dashboard is showing a stale state — the ETL pipeline refreshes on every load, so wait a few minutes and reload.

## Cross-sheet drills

- **L2 Exceptions table row → Chains** (right-click → *View in Chains (filter parent_chain_name to entity_a)*). When you spot a Chain Orphans or multi-XOR violation on the L2 Exceptions sheet, drill here to narrow the *Chain Instances* table to that parent chain, showing every firing with its completion status. Lets you see the scope of the problem — is it one parent firing or many?

## Related handbook pages

- [Rails — Transactions Explorer](rails.md) — the per-leg transaction journal; use it when you've found an Incomplete chain and want to inspect the actual postings that did (or didn't) fire.
- [Transfer Templates — Multi-Leg Flow](transfer-templates.md) — the sibling surface for visualizing declared transfer template leg topology; chains are parent→child routing, templates are the multi-leg flow within a single transfer.
- [L2 Exceptions](l2-exceptions.md) — the unified violation summary; when you see Chain Orphans or other orphan-rate violations, cross here to triage.
- [Getting Started](getting-started.md) — the L2 instance guide; use it to confirm your instance's chain declarations are loaded.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L2`, `chain`, `template`, `matview`, and the other project-specific terms.*
