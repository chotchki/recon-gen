# L2 Triage Gaps

The Studio **`/etl/triage`** surface diffs the integrator's L2 YAML
declaration against what the live `<prefix>_transactions` table
actually carries and surfaces typed `Gap` records as decision cards.

Each row is one piece of L2-to-runtime divergence: a posted rail that
doesn't resolve to any declared `Rail`, a template the L2 doesn't
know about, a `(parent_role, rail)` combo with no `LimitSchedule`
covering it or a template-required metadata key missing on rows
tagged with that template. None of these break the ledger; they
break the L2-to-runtime correspondence the integrator's ETL hook is
supposed to maintain.

Healthy = empty triage. A non-empty card tells the integrator either
to fix the ETL (so postings match the L2 declaration) or to update
the L2 (so the declaration matches what the ETL actually emits).

## How the data flows

```
<prefix>_transactions          (live, append-only)
        ↓
common.l2.triage.detect_gaps   (four checks, run at request time)
        ↓
Gap records                    (typed; rendered as accordion cards)
        ↓
/etl/triage                    (operator-facing decision surface)
```

Refresh contract: `detect_gaps` queries `<prefix>_transactions`
directly, NOT a matview — so the triage page reflects whatever has
been ETL'd up to the moment of request, no `refresh_matviews_sql`
required. Studio's `/etl/run` button (which DOES refresh matviews
after seed/replant) is the L1 dashboard's refresh path; triage is
independent of that step.

## The four triage gap kinds

### 1. Unmatched rail_name

Each row is a posted transaction whose `rail_name` doesn't resolve
to any declared `Rail.name` in the L2 yaml. The check groups
`<prefix>_transactions` by `rail_name`, then filters to values not
in the L2's declared-rails set. The card shows the offending rail
name + posting count + a sample `transaction_id` so the operator
can grep their source ETL log.

**Columns:** `rail_name`, `posting_count`, `sample_transaction_id`,
`declared_rails`.

**What to do:** Either add the rail to the L2 yaml (`rails:` block)
with the right `source_role` / `destination_role` — the card's CTA
deep-links to the L2 editor's create-new form pre-filled with the
offending name — OR fix the ETL hook to translate the legacy
`rail_name` into the L2-declared canonical name. A row here means
the L2 doesn't even know about a money path the bank is running;
the L1 Limit Breach + Drift checks can't fire against undeclared
rails, so this is a silent-blind-spot indicator.

### 2. Unmatched template_name

Each row is a posted transaction whose `template_name` doesn't
resolve to any declared `TransferTemplate.name`. Same shape as
unmatched_rail but on the template axis. The card shows the
offending template name + posting count + sample transaction_id +
the declared-templates list for context.

**Columns:** `template_name`, `posting_count`,
`sample_transaction_id`, `declared_templates`.

**What to do:** Either declare the template in the L2 yaml
(`transfer_templates:` block) with its `rail` + `transfer_key`
fields — the card's CTA deep-links to the create-new form — OR fix
the ETL hook to stop tagging postings with the unrecognized
template name. Postings without a known template can't participate
in L1 Conservation bucketing — they show up under "(no template)"
which is a per-row tell that the integrator's mapping has drifted.

### 3. Missing LimitSchedule

Each row is a `(parent_role, rail_name)` tuple that fired in the
live transactions but no `LimitSchedule` covers it in either
direction (debit / credit). The L1 Limit Breach matview renders
these as "no cap" — the operator has to decide whether that's
intentional (some rails really are uncapped) or whether a schedule
was supposed to exist.

**Columns:** `parent_role`, `rail_name`, `firing_count`,
`existing_schedules_for_parent_role`.

**What to do:** Either add a `LimitSchedule` row in the L2 yaml
covering the `(parent_role, rail)` tuple — the card's CTA
deep-links to the Limits editor — OR confirm the "no cap" posture
is intentional and document it (e.g. an internal-only sweep rail
the bank really does run uncapped). The card shows the existing
schedules for the same `parent_role` as context so the operator can
spot when "we have caps for the credit direction but never added
the debit side."

### 4. Missing required metadata key

Each row is a `(template_name, metadata_key)` pair where the
template declares the key as required (via its `transfer_key`
fields) but at least one posting tagged with that template landed
without the key in its metadata JSON. The card shows the count of
missing-key postings + the total postings for that template + a
sample transaction_id.

**Columns:** `template_name`, `metadata_key`, `missing_count`,
`template_total_rows`, `sample_transaction_id`.

**What to do:** Either fix the ETL hook to emit the missing key on
every posting tagged with that template — the card's CTA deep-links
to the template editor showing which keys are required — OR drop
the key from the template's `transfer_key` list in the L2 yaml if
upstream genuinely doesn't carry it. A missing required key means
L1 Conservation can't bucket the affected rows, which silently
distorts the L1 Drift dashboard for the operator's customer
segments downstream of that template.

## L2 Coverage gaps

The triage gaps above (the four `GapKind` literals) fire when the
runtime has data the L2 doesn't account for. The two **coverage**
sections below are the inverse — the L2 declares a rail or template
that the runtime never exercises. These surface on the `/etl/run`
Coverage panel (after a refresh) rather than `/etl/triage`, but
they share the same typed catalogue because they're the same
operator surface family: "L2-to-runtime alignment is off, here's
which side."

### 5. Uncovered rail

Each row is an L2-declared `Rail.name` with zero postings in
`<prefix>_transactions`. The Coverage Rails panel renders the rail
as ✗ (`declared but no rows`). Pulls from the same data slice as
the L2FT Dead Rails hygiene check — they're two surfaces on the
same underlying check.

**Columns:** `rail_name`, `leg_shape`, `posting_count`.

**What to do:** Either the declared rail is genuinely unused —
retire it from the L2 yaml's `rails:` block — OR the ETL hook is
misrouting postings against it (check `rail_name` casing + spelling
against the L2 declaration). A long-uncovered rail is L2 noise:
it shows up in dropdowns + handbook prose but never has data, so
operators downstream lose trust in the L2 as a faithful map of the
runtime.

### 6. Uncovered template

Each row is an L2-declared `TransferTemplate.name` with zero
postings tagged with that `template_name`. Coverage Templates
panel renders the template as ✗. Inverse of `unmatched_template` —
that one means rows exist for an undeclared template; this means
the template exists but no rows cite it.

**Columns:** `template_name`, `rail`, `posting_count`.

**What to do:** Either the template is no longer in use — retire it
from the L2 yaml's `transfer_templates:` block — OR the ETL hook
stopped tagging postings with it (check the template name + the
template's `rail` field against what the ETL emits). Uncovered
templates particularly hurt L1 Conservation: a template with no
data means `template_metadata_coverage` can't bucket anything,
which leaves rows landing under "(no template)" with no analyst
recourse.
