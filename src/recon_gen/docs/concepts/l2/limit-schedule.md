# Limit schedule

A **limit schedule** is a daily flow cap keyed on a
``(parent_role, rail, direction)`` triple. Operationally: "any single
customer DDA MUST NOT send more than $50M of outbound ACH on a business
day" or "any single customer DDA MUST NOT receive more than $20K of
inbound ACH on any day". The cap is per CHILD account, not the combined
flow across all of a role's children — every matched account is checked
on its own against the same ceiling.

Each schedule has:

- ``parent_role`` — picks WHICH accounts the cap governs: every child
  whose ``parent_role`` matches (e.g. ``DDAControl`` picks up every
  customer DDA). Each matched account is evaluated on its own, never
  pooled. Direct (singleton) caps name the singleton account's own role.
- ``rail`` — the kind of money movement this cap applies to. Caps are
  per-rail-type, never global. (Renamed from ``transfer_type`` so it
  references a Rail name directly — a templated leg no longer slips the
  cap by carrying the template's transfer_type instead.)
- ``cap`` — the daily ceiling as a Money value (currency symbol +
  amount). The L1 ``limit_breach`` matview surfaces every
  ``(account, business_day, rail_name, direction)`` cell whose
  day-summed flow clears this.
- ``direction`` — which side the cap watches. ``Outbound`` (the default)
  is the classic per-rail send cap, compared against Debit-flow totals
  (money LEAVING the child). ``Inbound`` is the AML / structuring
  threshold pattern, compared against Credit-flow totals (money
  arriving). One ``(parent_role, rail)`` pair can carry an Outbound AND
  an Inbound schedule at once — they're separate uniqueness keys.

The L1 dashboard's Limit Breach sheet lists the breach rows ranked by how
far over the cap each one sits, with a Direction column splitting
Outbound from Inbound. A breach is a SHOULD-violation, not a hard
regulatory failure — the bank can honor or block individual transactions;
the dashboard's job is to surface the breach so an operator can decide.
Routing convention: Outbound breaches head for ops triage queues, Inbound
(AML) breaches for the compliance review queue.

Every ``(parent_role, rail, direction)`` triple MUST be unique across the
schedule list (validator rule U5). Two schedules on the same triple would
silently clobber each other at matview-emit time.

> Limit schedules are CONFIGURATION, not topology. They don't show up in
> the accounts diagram or the chains diagram — they're a per-``(parent_role,
> rail, direction)`` ceiling the L1 limit-breach matview consults. The
> diagram below is a conceptual picture of that mapping, not an actual
> graph.

## Specific example for you

{{ l2_limit_schedule_focus() }}
