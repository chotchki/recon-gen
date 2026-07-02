# How do I add an AML inbound-flow cap?

*Customization walkthrough — Compliance / Integrator. Reskinning + extending.*

## The story

Compliance wants aggregated inbound deposits that approach the
federal currency-transaction-reporting threshold surfaced. The
bank's policy: any single customer DDA that takes in more than $20K
of ACH credits in one business day gets flagged for AML review.
You want that breach on the L1 Limit Breach sheet just like a
per-rail send cap — but DISTINGUISHABLE, so the routing logic fans
it to the AML review queue instead of operations triage.

[`LimitSchedule.direction`](../../concepts/l2/limit-schedule.md)
already does exactly this. You declare a second `LimitSchedule` on
the same `(parent_role, rail)` pair as a hypothetical Outbound send
cap (or a brand-new pair if there's no Outbound counterpart) with
`direction: Inbound`. The L1 `limit_breach` matview already UNIONs
both directions, so the row surfaces with no schema migration or
matview rewrite.

## The question

"How do I add a $20K daily ACH inbound cap per customer DDA so
compliance sees breaches on the L1 Limit Breach sheet, routed to
the AML review queue rather than ops triage?"

## Where to look

Three reference points:

- **[Limit schedule (concept)](../../concepts/l2/limit-schedule.md)**
  — the field semantics: how Outbound and Inbound differ, the
  triple-key uniqueness rule and what the L1 matview does with the
  row.
- **`tests/l2/spec_example.yaml`** — the minimal fixture carries one
  Outbound cap plus one Inbound cap on the same parent
  (`CustomerLedger`) but DIFFERENT rails (`ExternalRailOutbound`
  Outbound, `ExternalRailInbound` Inbound), proving the per-direction
  shape round-trips through loader → validator → matview → dashboard.
- **`run/sasquatch_pr.yaml`** (or your own L2 yaml under `run/`)
  — the real-world example carries a $20K Inbound cap on
  `(DDAControl, CustomerInboundACH)` modeled after the federal CTR
  threshold. Search for `direction: Inbound` to find it.

## The change

In your `run/<institution>.yaml`, find the `limit_schedules:`
block and add a new entry with `direction: Inbound`:

```yaml
limit_schedules:
  # existing Outbound caps...
  - parent_role: DDAControl
    rail: CustomerOutboundACH
    cap: 12000
    # Outbound is the default; omitting `direction:` here keeps the
    # YAML byte-equivalent to pre-AB.1 fixtures.
    description: '$12K daily ACH outbound cap per customer DDA.'

  # New Inbound (AML) cap on the same parent — different rail
  # name because the inbound rail is a distinct entity.
  - parent_role: DDAControl
    rail: CustomerInboundACH
    cap: 20000
    direction: Inbound
    description: |
      $20K daily ACH inbound cap per customer DDA. AML / structuring
      threshold — mirror of the federal CTR rule applied to ACH
      inbound volume. Breaches route to the AML review queue, not
      the ops triage queue that Outbound payout breaches use.
```

If the Outbound + Inbound pair lives on the same rail name (rare,
but allowed), the `(parent_role, rail)` pair appears twice in the
list — the U5 uniqueness check broadens to the
`(parent_role, rail, direction)` triple, so both rows are accepted.

## How to verify

Re-emit the L2-derived schema and seed against your demo DB:

```bash
recon-gen schema apply -c run/config.yaml --execute
recon-gen data apply -c run/config.yaml --execute
recon-gen data refresh -c run/config.yaml --execute
```

The first command rewrites the `<prefix>_limit_breach` matview
with the second UNION-ALL branch picking up your new cap. The
second one re-seeds the demo data — `auto_scenario.py` plants an
`InboundCapBreachPlant` for the Inbound cap (amount = min(cap × 1.5,
rail range.max × 3) floored at cap + $1 — the AB.5 realism clamp; a
rail with no amount_typical_range keeps the naive cap × 1.5), so the
dashboard has a row to surface immediately.

Open the L1 Limit Breach sheet. You should see:

- One new row whose Direction column reads "Inbound" and whose
  `outbound_total` column is `~$20,001` (the AB.5 realism clamp
  pins the plant to min(cap × 1.5, rail range.max × 3) then floors
  at cap + $1 — CustomerInboundACH's range [50, 5000] lands it at
  $20,001, not the naive $20K × 1.5).
- The existing Outbound rows (if any) still present, marked
  "Outbound" in the same column.
- L1 Exceptions inherits the new row automatically — its
  UNION-over-matviews already reads from `<prefix>_limit_breach`
  unchanged.

[See it live](https://recon-gen-spec.hotchkiss.io/) (the
`spec_example` fixture carries the Inbound cap).

## What you should NOT do

- **Don't add a new matview just for inbound caps** — the existing
  `limit_breach` matview already handles both directions. Adding
  a second matview would double the dashboard's matview count
  AND fork the L1 Exceptions UNION, which buys nothing.
- **Don't try to encode "AML routing" as an L2 enum on the
  schedule itself.** The direction column on the dashboard row
  IS the routing signal — your downstream pager / ticketing
  integration reads the matview's `direction` column and routes
  per its own policy (Outbound → ops, Inbound → AML). The L2
  yaml doesn't need to model the routing target.
- **Don't omit the `description` field on the Inbound cap.** The
  L1 Getting Started sheet renders LimitSchedule prose in a
  bullet list; missing prose makes a bullet with just the
  parent/rail/cap which reads cryptically to a non-technical
  auditor.

## Related

- [LimitSchedule (concept)](../../concepts/l2/limit-schedule.md) —
  field-by-field semantics, including the U5 triple-key rule.
- [L1 Invariants reference → Per-direction flow cap](../../L1_Invariants.md#5-per-direction-flow-cap)
  — the SHOULD-constraint the matview encodes, with the
  Outbound / Inbound theorem split.
- [Schema_v6 → LimitSchedule](../../Schema_v6.md) — the data
  contract for the matview's column shape, including the
  `direction` column.
