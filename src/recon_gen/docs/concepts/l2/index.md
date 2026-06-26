# Concepts — L2 model

The L2 YAML is the modeling vocabulary an integrator uses to declare an
institution's shape, and it's the ONE source every shipped dashboard
reads from. Feed a different YAML and the dashboards' contents change —
no code touched.

Each page below takes one primitive on its own, then shows a concrete
example pulled from the active L2 instance (``spec_example`` is the
fallback when the active L2 hasn't declared an entity of that type yet).

## Primitives

- [Account](account.md) — the singleton, a 1-of-1 GL or external
  counterparty.
- [Account template](account-template.md) — the SHAPE of an N-of-many
  account class (per-customer DDA, per-merchant settlement) materialized
  at posting time.
- [Rail](rail.md) — a single money-movement primitive. TwoLeg posts a
  debit + credit; SingleLeg posts one leg reconciled by a template or
  aggregating rail.
- [Transfer template](transfer-template.md) — a multi-rail bundle with an
  ``expected_net`` close-out (e.g. an "ACH origination cycle").
- [Chain](chain.md) — a parent → child firing rule. One child = required;
  two or more children = XOR alternation ("exactly one of these MUST
  fire").
- [Limit schedule](limit-schedule.md) — a daily flow cap per
  ``(parent_role, rail_name, direction)`` (Outbound send-cap by default,
  Inbound for the AML / structuring threshold).

For the institution-tour view that walks every entity at once, see
[Training Story](../../scenario/index.md). For the data-side feed
contract those entities project into, see [Schema v6](../../Schema_v6.md).
