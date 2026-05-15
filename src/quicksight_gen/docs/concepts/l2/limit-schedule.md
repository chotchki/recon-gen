# Limit schedule

A **limit schedule** declares a daily outbound-flow cap for a
``(parent_role, rail_name)`` pair. Operationally: "all customer
DDAs combined MUST NOT send more than $50M of outbound ACH on any
single business day".

Each schedule has:

- ``parent_role`` — the role whose children's outbound flow gets
  summed up (e.g. ``DDAControl`` aggregates every customer DDA).
  Direct (singleton) caps use the singleton account's role itself.
- ``rail_name`` — which kind of money movement this cap
  applies to. Caps are per-rail-type, not global.
- ``cap`` — the daily ceiling, expressed as a Money type (currency
  symbol + amount). The L1 ``limit_breach`` matview surfaces every
  ``(account, business_day)`` whose summed outbound exceeds this.

The L1 dashboard's Limit Breach sheet shows breach rows, ranked by
"how far over the cap". A breach is a SHOULD-violation, not
necessarily a hard regulatory failure — the bank can choose to honor
or block individual transactions; the dashboard's job is to surface
the breach so an operator can decide.

Per a pending SPEC update, every ``(parent_role, rail_name)``
pair MUST be unique across the schedule list — two schedules
keying on the same pair would silently override each other at
matview-emit time.

> Limit schedules are *configuration*, not topology. They don't
> appear in the accounts diagram or chains diagram — they're a
> per-(role, rail_name) ceiling the L1 limit-breach matview
> consults. The diagram below is a conceptual representation of the
> mapping rather than an actual graph.

## Specific example for you

{{ l2_limit_schedule_focus() }}
