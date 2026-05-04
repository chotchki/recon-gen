# How do I tag a force-posted external transfer correctly?

*Engineering walkthrough — Data Integration Team. Extension.*

## The story

Most postings the bank makes originate inside the bank — an
operator at {{ vocab.institution.name }} initiated the ACH, the
wire, the internal transfer. A handful of postings are *forced on
us* by an external system: the Fed pushes an inbound ACH credit
before our origination side caught up; the card processor reports
a settlement before our internal catch-up posts. The bank still
has to record those rows in `{{ l2_instance_name }}_transactions` (otherwise the
GL is wrong), but they're a different *kind* of posting.

The schema captures this with the `origin` column. Three values:

- `InternalInitiated` — the bank started this. The default for
  almost every row.
- `ExternalForcePosted` — an external system started this; we
  recorded it for GL parity. The minority case.
- `ExternalAggregated` — an external system pre-aggregated several
  underlying events into one row before we recorded it (e.g., a
  daily batch summary). Less common than ForcePosted; same
  treatment for drift purposes.

Get this tag wrong on Fed-statement ingest and the L1 drift
classification misclassifies the row, silently under-firing on real
exceptions.

## The question

"My Fed-statement loader has a row that doesn't have a matching
internal posting yet. What do I write in `origin` and what
`metadata` do I attach so the L1 invariant views see it as
'Fed-side, internal catch-up pending' instead of as a real
exception?"

## Where to look

Two reference points:

- **`docs/Schema_v6.md` → `origin` column spec** — the per-value
  failure-mode notes describe exactly which checks under-fire when
  you skip `ExternalForcePosted`.
- **L2 instance YAML** — the L2 declares any rails / chains that
  expect a force-posted parent followed by an internal catch-up
  child. The L2 Flow Tracing dashboard shows you which chains your
  instance models, and the rails / chains your Fed loader feeds
  into.

## What you'll see in the demo

For an inbound Fed ACH credit that hasn't matched an outbound
internal origination yet, two columns drive the classification:

```sql
'ExternalForcePosted',                         -- origin column
JSON_OBJECT(
    'source'            VALUE 'fed_statement', -- metadata key
    'statement_line_id' VALUE 'fed-stmt-2026-04-20-line-042'
)                                              -- metadata column
```

`origin` is the structural switch the L1 drift classification
reads. `metadata.source` is the provenance tag that lets
Investigation walkthroughs explain *which upstream system*
generated the row. Both are required.

## What it means

The L1 invariant layer treats the two `origin` values
asymmetrically:

1. **`origin = 'InternalInitiated'` rows are subject to the full
   set of L1 SHOULD checks** (drift, overdraft, limit breach,
   stuck pending, stuck unbundled). If an internal posting is
   wrong, *the bank* is wrong, and an operator should
   investigate.
2. **`origin = 'ExternalForcePosted'` rows are excluded from
   "operator initiated drift" checks but *included* in the
   reconciliation-against-external-system checks** that your L2
   declares. The exact set depends on which rails / chains your L2
   models. Generally:
   - **Drift between an internal control account and the external
     system's view** counts a day where the *sum* of
     `ExternalForcePosted` rows on that account doesn't equal the
     *sum* of internal catch-up postings.
   - **External activity without internal catch-up** lists
     individual `ExternalForcePosted` rows that have no follow-up
     internal posting after a grace period.
   - **Internal origination without external confirmation** is the
     inverse: an internal origination posted but no matching
     `ExternalForcePosted` confirmation arrived.

So the wrong tag flips the row from one check to its opposite — or
out of all checks entirely. There's no benign default.

## Drilling in

The decision tree for any row your loader sees:

- **The bank started it (operator, scheduler, internal automation):**
  `origin = 'InternalInitiated'`. Default for ~99% of rows.
- **An external system started it and we're recording for GL parity:**
  `origin = 'ExternalForcePosted'`. `metadata.source` should name
  the upstream (`'fed_statement'`, `'card_processor'`,
  `'wire_correspondent'`, etc.).
- **An external system pre-aggregated events before we recorded
  them:** `origin = 'ExternalAggregated'`. Use when the inbound
  row is a roll-up — the underlying events are not individually
  available.

A subtlety on the `transfer_parent_id` chain: when an external
force-post *eventually* gets matched by an internal catch-up,
**the catch-up's `transfer_parent_id` should point at the
force-post's `transfer_id`**, not the other way around. The Fed
row is the parent; the internal catch-up is the child. This is
easy to get backwards if your loader treats the catch-up as the
"main" event.

Concretely, for a Fed-pushed inbound ACH followed two days later
by an internal origination catch-up:

```sql
-- Day 0: Fed force-posts. ExternalForcePosted, no parent.
INSERT INTO {{ l2_instance_name }}_transactions
    (..., transfer_id, transfer_parent_id, origin, ...)
VALUES (..., 'fed-xfer-EXAMPLE-001', NULL, 'ExternalForcePosted', ...);

-- Day 2: internal catch-up posts. transfer_parent_id points at the Fed row.
INSERT INTO {{ l2_instance_name }}_transactions
    (..., transfer_id, transfer_parent_id, origin, ...)
VALUES (..., 'int-xfer-EXAMPLE-042', 'fed-xfer-EXAMPLE-001',
            'InternalInitiated', ...);
```

Skip the parent link on the catch-up and the chain integrity check
in the validation walkthrough flags an orphan; the L1 drift check
on day 2 won't subtract correctly because it can't tell that
`int-xfer-EXAMPLE-042` is "the catch-up for" the Fed row.

## Next step

Once your Fed-statement projection is wired up:

1. **Verify the tag pair lands correctly**. Pull a sample Fed
   force-post row from your loaded data and confirm both
   `origin = 'ExternalForcePosted'` AND `JSON_VALUE(metadata,
   '$.source') = 'fed_statement'` are set. Either alone is wrong.
2. **Run the day-0 + day-N pre-flight against the chain**. Use the
   orphan-parent query (Invariant 3 in the validation walkthrough)
   on a window that covers your longest expected catch-up lag —
   typically 5 business days for ACH, 1 day for card.
3. **Open L1 Today's Exceptions and inspect the relevant L1
   sheets**. If the L1 Drift KPI spikes after a Fed-statement load,
   you're either missing the catch-up postings (real exception) or
   your Fed rows are tagged `InternalInitiated` (tag bug — they
   get counted as bank activity instead of Fed activity).

## Related walkthroughs

- [How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](how-do-i-populate-transactions.md) —
  the foundational projection. This walkthrough is its
  Fed-statement variant.
- [How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md) —
  Invariant 3 (parent-chain integrity) is the pre-flight check
  for the catch-up linkage described here.
- [L1 Reconciliation Dashboard: Drift](../l1/drift.md) —
  the **downstream consumer**. Read this to understand what a
  correctly tagged Fed row enables.
- [Schema_v6 → `origin` column spec](../../Schema_v6.md#table-1-prefix_transactions) —
  the column-contract details.
