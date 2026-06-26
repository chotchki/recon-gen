# Account

An Account is a singleton account — a 1-of-1 thing the institution
holds, declared ONCE and referenced everywhere by its role (a singleton
that Rails reference by Role). Three fields carry it: ``id`` (the
stable database identifier), ``role`` (the abstract slot the rest of
the L2 model refers to — e.g. ``CashDueFRB``, ``DDAControl``) and
``scope``, which is either ``internal`` (the institution's own ledger
account) or ``external`` (a counterparty the institution doesn't own —
the Federal Reserve, a card processor, a beneficiary bank — whoever
sits on the far side of a flow). The remaining fields are optional but
each drives real behavior (``name``, ``expected_eod_balance``,
``parent_role``, ``business_day_offset``, ``balance_cadence``):
``balance_cadence`` drives the ``balance_cadence_gap`` L1 invariant, and
``business_day_offset`` shifts end-of-day and is rejected on ``external``
scope. id/role/scope are what every downstream consumer keys off.

Accounts roll up via ``parent_role`` — a sub-ledger holding account
points at a control account that aggregates many sub-ledgers. The drift,
overdraft and limit-breach matviews walk that parent link to compute
parent-level rollups ON TOP of the leaf view (so a breach surfaces both
at the child and at the aggregate).

The two scopes drive different downstream behavior:

- **``internal`` accounts** participate in double-entry — every Rail
  that posts to one expects an offsetting credit/debit. They show up
  in the L1 invariant matviews (drift, overdraft) and in the per-day
  Daily Statement.
- **``external`` accounts** stand for counterparties; the institution
  does not own them. They appear as the OTHER side of inbound /
  outbound flows, and the Investigation app's account-network sheet
  walks edges through them.

> An Account is PHYSICAL and 1-of-1 — it exists exactly once. For a
> 1-of-many shape (per-customer DDA, per-merchant settlement) reach for
> an [Account template](account-template.md) instead.

## Specific example for you

{{ l2_account_focus() }}
