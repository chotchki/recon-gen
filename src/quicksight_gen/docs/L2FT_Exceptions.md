# L2FT Hygiene Exceptions

The L2 Flow Tracing dashboard's **L2 Hygiene Exceptions** sheet
surfaces six runtime checks against the integrator's L2 YAML
declaration. Each row in any check is a piece of L2 declaration that
doesn't match what the live `<prefix>_current_transactions` matview
shows — a declared rail with no postings, a Posted transaction
against an undeclared rail, a Required chain whose parent fired but
child didn't, etc. None of these break the ledger; they break the
L2-to-runtime correspondence the integrator's ETL is supposed to
maintain.

Healthy = empty. A non-empty row tells the integrator either to fix
their ETL (so the L2 declaration matches reality) or retire the
declaration (so the YAML matches what the ETL actually emits).

## How the data flows

```
<prefix>_transactions
        ↓
<prefix>_current_transactions     (max-Entry-per-id projection)
        ↓
six per-check datasets:
  ├── l2ft_exc_chain_orphans          (Required-chain firings missing children)
  ├── l2ft_exc_unmatched_rail_name    (Postings against undeclared rails)
  ├── l2ft_exc_dead_rails             (Declared rails with no postings)
  ├── l2ft_exc_dead_bundles_activity  (bundles_activity targets with no matching postings)
  ├── l2ft_exc_dead_metadata          (Declared metadata keys nothing carries)
  └── l2ft_exc_dead_limit_schedules   (LimitSchedule cells with no outbound debit)
        ↓
l2ft_unified_exceptions               (UNION ALL — drives the Hygiene Exceptions sheet)
```

Refresh contract: the underlying check runs at dataset-query time
against `<prefix>_current_transactions`, so a fresh
`refresh_matviews_sql(instance)` after every ETL batch keeps the
six checks current. There is no separate `l2ft_exc_*` matview.

## The six L2FT hygiene checks

### 1. Chain Orphans

Each row is a declared Required chain edge (`parent → child`) where
the parent rail fired in the window but no matched child firing
followed within the SLA. The matched-child count uses the child's
`transfer_parent_id` to link back to a parent transfer-id — so a
true orphan is "parent fired, no child cited the parent." XOR-group
multi-or-none violations are deferred to a follow-on substep; the
current row count surfaces single-required-child gaps only.

**Columns:** `parent_name`, `child_name`, `parent_firing_count`,
`child_firing_count`, `orphan_count`.

**What to do:** Either fix the ETL so the child rail fires when the
parent does (the L2 says it should), or retire the chain edge from
the L2 YAML if the parent-child causality no longer holds. Each row
names the parent + child rail — drill to the L2FT Chains sheet for
the firing-count history.

### 2. Unmatched Rail Name

Each row is a posted transaction whose `rail_name` doesn't match
any declared `Rail.name`. The query LEFT JOINs `current_transactions`
to a CTE of declared rail names and filters to the NULL side. Output
groups by `rail_name` with a count of postings — so each row is one
undeclared rail that the runtime is using.

**Columns:** `rail_name`, `posting_count`.

**What to do:** Either add the rail to the L2 YAML (`rails:` block)
with the right `source_role` / `destination_role`, or fix the ETL to
stop emitting rows with that `rail_name`. A row here means the L2
doesn't even know about a money path the bank is running — the L1
limit-breach + drift checks can't fire against undeclared rails, so
this is a silent-blind-spot indicator.

### 3. Dead Rails

Each row is an L2-declared rail with zero matching postings in the
window. Same shape as the Rails dataset but pre-filtered to rows
where `COALESCE(total_postings, 0) = 0`. The KPI shows the count;
the detail table lists each dead rail with its leg shape so the
integrator can decide whether to retire the declaration or fix the
ETL.

**Columns:** `rail_name`, `leg_shape`.

**What to do:** Either the declared rail is genuinely unused (retire
it from the L2 YAML), or the ETL is misrouting postings against it
(check `rail_name` casing + spelling against the L2). A long-dead
rail is L2 noise — it shows up in dropdowns + handbook prose but
never has data.

### 4. Dead Bundles Activity

Each row is an `(aggregating_rail, bundle_target)` pair the L2
declared via `Rail.bundles_activity` that the runtime never matched
— no posting carries `rail_name = bundle_target`. Per Z.B, every
`bundles_activity` ref is an Identifier rail name (no transfer_type
form remains).

**Columns:** `aggregating_rail`, `bundle_target`.

**What to do:** Either the aggregating rail's bundling actually
includes the named target rail (check the ETL's `bundle_id` writes —
each authorization that should roll into the aggregating settlement
must carry `bundle_id`), or the L2's `bundles_activity` over-claims
what the rail bundles. Drop the false claim from the L2.

### 5. Dead Metadata Declarations

Each row is a `(rail, metadata_key)` pair the L2 declared via
`Rail.metadata_keys` that no posting carries a non-null value for
in the window. Each declared pair gets its own SQL fragment in the
UNION ALL; the path is the static `$.<key>` JSONPath against
`<prefix>_current_transactions.metadata`.

**Columns:** `rail_name`, `metadata_key`.

**What to do:** Either the ETL needs to start writing the key into
`transactions.metadata` (the L2 says this rail should carry it), or
the L2 declares a key that's not actually used — drop it from the
rail's `metadata_keys` block. Live metadata is what the L2FT
cascade dropdowns drive off; a dead declaration shows an empty
dropdown to operators who expected it to narrow.

### 6. Dead Limit Schedules

Each row is a `(parent_role, rail_name)` LimitSchedule cell with
zero outbound debit flow against it in the window. The NOT EXISTS
clause checks `current_transactions` for any Debit posting matching
the role + rail. A cap nobody routes against can't ever fire as a
limit-breach.

**Columns:** `parent_role`, `rail_name`, `cap`.

**What to do:** Either the rail genuinely doesn't carry outbound
debit flow from this role (retire the LimitSchedule entry), or the
ETL routes the flow through a different (`role`, `rail`) cell than
the L2 expects (verify the `account_parent_role` denormalization
on every Debit row). A dead LimitSchedule means the L1 limit-breach
check has nothing to catch — silent for that combination.
