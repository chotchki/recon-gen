# Metadata Popup

> **What this affordance teaches.** Per-row JSON inspection without leaving the dashboard. When a transaction or balance leg carries free-form metadata — plant tags, force-posted markers, supersession reasons, customer ETL extras — the `{}` View metadata entry on the row-drill menu shows you exactly what the database stored.

## Where the affordance lives

On every row of the *Posting Ledger* table on the [Transactions](transactions.md) sheet and the *Posted Money Records* table on the [Daily Statement](daily-statement.md) sheet, hover any row to reveal the trailing `⋯` row-drill menu button. Click it; the menu opens with `{} View metadata` as its first entry, sitting alongside the existing drill destinations (e.g. *View Transactions for this transfer*).

Pick `{} View metadata`. A side panel slides in from the right carrying the row's metadata JSON pretty-printed and tree-rendered.

## What you're looking at

The side panel renders the row's raw `metadata` column from `<prefix>_transactions` — the same JSON your customer ETL wrote (or the seed pipeline planted, in demo land). Raw keys, no friendlier-label rewriting. Plant primitives use known keys (`plant_kind`, `plant_anchor_day`, `plant_id`); force-posted transfers use `force_posted: true`; supersession lifecycle entries carry `supersedes: BundleAssignment` and friends. Customer ETL can stash whatever else it needs in the same column — the popup shows you what landed.

The header carries:

- The `transaction_id` of the row whose metadata you're inspecting
- A **Copy** button — writes the pretty-printed JSON to the clipboard (falls back to `document.execCommand('copy')` when the Clipboard API is denied)
- **Expand all** / **Collapse all** buttons — bulk-toggle every `<details>` node in the tree (also bound to Ctrl+E / Ctrl+Shift+E when focus is inside the panel)

Below the header sits the JSON tree itself. Object and array nodes render as collapsible `<details>` rows with a compact preview line (`"plant": { 3 fields }` / `"counterparties": [ 5 items ]`); primitive leaves render inline as JSON literals (`"value"`, `true`, `null`, `42`). The tree opens to depth 2 by default and closes deeper — the operator guidance was "be prepared for awful nested JSON," so the default keeps shallow context visible without dumping a thousand expanded nodes on you at once.

## Empty state

Rows where the database's `metadata` column is `NULL`, `{}`, `[]`, the JSON literal `null` or missing the field entirely render a single line:

> *No metadata for this row.*

No toolbar, tree or Copy button renders — there's nothing to copy. This is healthy. Most ledger legs don't carry metadata; the column exists for the legs that do.

## Common patterns

### "Why is this row planted?"

In demo deployments, the seed pipeline tags planted scenarios with `metadata: {"plant_kind": "<kind>", "plant_anchor_day": "<iso-date>", ...}`. Click `{} View metadata` on a suspicious row and read `plant_kind` — it tells you exactly which L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) violation pattern the row exists to demonstrate. Use [Drift](drift.md), [Overdraft](overdraft.md), [Pending Aging](pending-aging.md), etc. to follow the violation back to its invariant tab.

### "What did the ETL stamp on this leg?"

Customer ETL hooks (see the *Customization* handbook) can write arbitrary JSON into `<prefix>_transactions.metadata` for downstream forensics — counterparty identifiers from the source system, batch-processor run IDs, regulatory tags, whatever the integrator needed at the time. The popup is the dashboard-side window into those tags without writing a SQL query.

### "Is the supersession-lifecycle entry legit?"

Higher-entry rows on the [Supersession Audit](supersession-audit.md) sheet carry a `supersedes` reason. The popup shows the whole metadata blob, not just the `supersedes` field, so you can cross-check whatever else the ETL tagged when it appended the new entry.

### "Copy this for the ticket."

Click **Copy**. The button flashes *Copied!* for 1.5s. Paste the pretty-printed JSON into your incident ticket / Slack thread / vendor support email. The clipboard payload is the same text the textarea holds — exactly what `json.dumps(metadata, indent=2)` produced server-side.

## Where it doesn't appear

- **QuickSight bundles.** The popup is App2-only by operator decision (CY operator lock 7). The deployed QuickSight dashboards have no `⋯` row-drill menu and no side-panel chrome; QS users inspecting metadata must query the base table directly. The affordance was built primarily as a troubleshooting tool for the self-hosted App2 / Studio iteration loop where the operator already runs the L2-aware dashboard locally. (The same JSON is in the database either way; the question is only whether the dashboard surfaces it.)
- **Sheets without a tree-flagged Table.** The popup attaches only to `Table` visuals declared with `metadata_popup=True` in the tree wiring (today: *Posting Ledger* on Transactions + *Posted Money Records* on Daily Statement). Adding the affordance elsewhere requires lighting up the same flag on the destination visual; the construction-time guard fails loud if the bound dataset's contract doesn't carry a `metadata` column.

## What it's NOT

- **Not an editor.** The popup observes, never mutates. There's no Save button, inline edit or DELETE entry. The metadata column is a customer-ETL-owned surface; the dashboard is read-only against it.
- **Not in the audit PDF.** The regulator-ready audit report (`recon-gen audit apply`) shows L1 invariant findings, not per-row metadata. Metadata is operator-facing troubleshooting context; the audit surface is the regulator-facing findings set.
- **Not a second DB round-trip.** The metadata JSON travels as a URL query param sourced from the already-rendered row payload. The popup-open path is `htmx.ajax → render → swap` — no per-click `SELECT` against the database. (The cost is paid once when the row payload renders the table.)

## When schema drift breaks it

If a future schema change drops or renames the `metadata` column, or weakens the `IS JSON` constraint, the wiring fails loud — the `Table.__post_init__` guard raises `ValueError` at generate time, and a malformed-JSON row makes the metadata route return a 500 with `'metadata JSON parse failed: …'`. No silent empty-render fallback (operator lock 8). If you see the loud error, the schema and the tree have drifted and one of them needs to be corrected; the popup is doing its job.

## Related handbook pages

- [Transactions](transactions.md) — the per-leg ledger where the popup lives on the *Posting Ledger* table.
- [Daily Statement](daily-statement.md) — the per-account-day narrative where the popup lives on the *Posted Money Records* table.
- [Supersession Audit](supersession-audit.md) — when the metadata you care about is the `supersedes` reason chain; the popup carries it inline.

---

*First time here? See the [Vocabulary](../_glossary.md) for [matview](../_glossary.md#matview--materialized-view), [account_role](../_glossary.md#account-role), and the other project-specific terms.*
