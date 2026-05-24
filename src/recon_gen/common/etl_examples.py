"""Canonical INSERT-pattern examples for ETL authors (X.1.h).

Returns a runnable-against-the-prefix-of-your-choice SQL string that
demonstrates every base-table shape the dashboards rely on. The output
is **exemplary, not executable against your real demo seed** — every
pattern uses fixed sentinel IDs (``-EXAMPLE`` suffix) so the
statements are self-contained and never collide with seeded rows.

Each block carries:

- a ``-- WHY:`` header naming the business invariant the pattern
  protects, and
- a ``-- Consumed by:`` header naming the dashboard view that reads
  the resulting rows.

The integrator's ETL strips the ``-EXAMPLE`` suffix and wires the
column projections to their upstream feed's source fields. The
``<prefix>`` placeholder gets templated by ``sed`` (or whatever
inline-substitute the integrator's deploy pipeline uses) at the
caller's discretion — the helper itself stays prefix-agnostic.

Pre-X.1.h the helper returned a single placeholder line referencing
deleted ``apps/payment_recon/etl_examples.py`` /
``apps/account_recon/etl_examples.py`` files (gone in M.4.3 / M.4.4),
giving operators a one-line file the etl.md handbook claimed
"covered every base-table shape." X.1.h replaced the placeholder with
real patterns.
"""

from __future__ import annotations


_HEADER = """\
-- ========================================================================
-- ETL Examples — canonical INSERT patterns for ``<prefix>_transactions``
-- and ``<prefix>_daily_balances``
-- ========================================================================
--
-- Replace ``<prefix>`` with your L2 instance prefix (e.g. ``acme_pr``)
-- and strip the ``-EXAMPLE`` suffix from every sentinel ID before
-- adapting these patterns into your ETL job.
--
-- The two-table contract: every dashboard the tool ships reads from
--   ``<prefix>_transactions``  — one row per money-movement leg
--   ``<prefix>_daily_balances`` — one row per (account_id, business_day_start)
--
-- Both tables auto-assign the ``entry`` BIGSERIAL on insert; supersession
-- (rewriting a previously-Posted leg) is achieved by re-inserting with
-- the same logical ``id`` (transactions) or ``(account_id, business_day_start)``
-- (daily_balances). Highest ``entry`` wins; older rows stay for audit.
"""


_PATTERN_SINGLE_LEG_POSTED = """\
-- ------------------------------------------------------------------------
-- Pattern 1 — Single-leg Posted transfer (the simplest case)
-- ------------------------------------------------------------------------
-- WHY: Most transfers are a single leg from the bank's perspective —
--   a fee accrual debiting the customer DDA, a recurring sweep
--   crediting the suspense account. One row per leg, ``status='Posted'``,
--   no counter-leg in this table.
-- Consumed by: every L1 sheet (Drift, Overdraft, Limit Breach,
--   Today's Exceptions); L2FT Rails sheet's Transactions table.

INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, origin, metadata
) VALUES (
    'tx-EXAMPLE-001',
    'acct-EXAMPLE-cust-0001',
    'Customer #0001',
    'CustomerDDA',
    'internal',
    'DDAControl',
    -25.00,                      -- signed: Debit ⇒ negative
    'Debit',
    'Posted',
    '2030-01-15T10:30:00',
    'tr-EXAMPLE-001',
    'CustomerFeeAccrual',
    'InternalInitiated',
    NULL                         -- metadata is optional
);
"""


_PATTERN_TWO_LEG_PAIRED = """\
-- ------------------------------------------------------------------------
-- Pattern 2 — Two-leg paired transfer (debit + credit sum to 0)
-- ------------------------------------------------------------------------
-- WHY: An internal transfer has both legs visible on the bank's
--   ledger. Both rows share the same ``transfer_id``. The L1
--   Conservation invariant requires the legs to sum to zero
--   (signed_amount on debit + signed_amount on credit = 0).
-- Consumed by: L2FT Rails sheet (both legs visible); L1 Daily
--   Statement sheet (debit on cust-0001's day, credit on cust-0002's
--   day).

INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, origin, metadata
) VALUES
    (
        'tx-EXAMPLE-002-debit',
        'acct-EXAMPLE-cust-0001', 'Customer #0001', 'CustomerDDA',
        'internal', 'DDAControl',
        -100.00, 'Debit', 'Posted',
        '2030-01-15T11:00:00',
        'tr-EXAMPLE-002',
        'InternalTransferDebit', 'InternalInitiated',
        '{"counterparty_id": "acct-EXAMPLE-cust-0002"}'
    ),
    (
        'tx-EXAMPLE-002-credit',
        'acct-EXAMPLE-cust-0002', 'Customer #0002', 'CustomerDDA',
        'internal', 'DDAControl',
        100.00, 'Credit', 'Posted',
        '2030-01-15T11:00:00',
        'tr-EXAMPLE-002',
        'InternalTransferCredit', 'InternalInitiated',
        '{"counterparty_id": "acct-EXAMPLE-cust-0001"}'
    );
"""


_PATTERN_FORCE_POSTED = """\
-- ------------------------------------------------------------------------
-- Pattern 3 — Force-posted external transfer (Fed-statement ingest)
-- ------------------------------------------------------------------------
-- WHY: Fed-wire and processor settlements arrive after-the-fact —
--   the bank didn't initiate them, the external rail did, and the
--   ledger has to reflect the movement that already happened. Tag
--   ``origin='ExternalForcePosted'`` so L1 Drift / Overdraft views
--   can split bank-initiated drift from force-posted drift (the two
--   classes have different operational meanings).
-- Consumed by: L1 Drift sheet (origin breakdown); Audit PDF
--   force-posted section.

INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, origin, metadata
) VALUES (
    'tx-EXAMPLE-003',
    'acct-EXAMPLE-frb-master',
    'Federal Reserve Bank — Master',
    'ExternalCounterparty',
    'external',
    NULL,                         -- external accounts have no parent role
    50000.00,
    'Credit',
    'Posted',
    '2030-01-15T16:45:00',
    'tr-EXAMPLE-003',
    'ConcentrationToFRBSweep',
    'ExternalForcePosted',        -- THIS is the contract
    '{"fed_reference": "20300115B1Q9X9X9X9X9000001"}'
);
"""


_PATTERN_PENDING_THEN_POSTED = """\
-- ------------------------------------------------------------------------
-- Pattern 4 — Pending → Posted lifecycle (supersession)
-- ------------------------------------------------------------------------
-- WHY: ACH and check transactions have a Pending phase before they
--   settle. The ETL writes the Pending row first; the same logical
--   ``id`` gets re-inserted with ``status='Posted'`` once the rail
--   confirms. The BIGSERIAL ``entry`` column auto-assigns a higher
--   value for the second insert, so the supersession rule
--   (highest entry per id wins) surfaces only the Posted state.
--   The Pending row stays for audit. ``supersedes='Lifecycle'``
--   on the second row signals "this is a normal lifecycle
--   advancement, not a TechnicalCorrection rewrite."
-- Consumed by: L1 Pending Aging sheet (until Posted); L1
--   Supersession Audit sheet's Lifecycle bucket.

-- 4a. The initial Pending insert.
INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, origin, metadata
) VALUES (
    'tx-EXAMPLE-004',
    'acct-EXAMPLE-cust-0001', 'Customer #0001', 'CustomerDDA',
    'internal', 'DDAControl',
    -750.00, 'Debit', 'Pending',
    '2030-01-15T09:00:00',
    'tr-EXAMPLE-004',
    'CustomerOutboundACH', 'InternalInitiated',
    '{"customer_id": "cust-0001"}'
);

-- 4b. Same logical id, status flipped to Posted. ``entry`` auto-
-- assigns higher; ``supersedes='Lifecycle'`` documents the why.
INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, origin,
    supersedes, metadata
) VALUES (
    'tx-EXAMPLE-004',                  -- same logical id
    'acct-EXAMPLE-cust-0001', 'Customer #0001', 'CustomerDDA',
    'internal', 'DDAControl',
    -750.00, 'Debit', 'Posted',        -- status advances
    '2030-01-17T14:30:00',             -- posting advances
    'tr-EXAMPLE-004',
    'CustomerOutboundACH', 'InternalInitiated',
    'Lifecycle',
    '{"customer_id": "cust-0001"}'
);
"""


_PATTERN_TECHNICAL_CORRECTION = """\
-- ------------------------------------------------------------------------
-- Pattern 5 — TechnicalCorrection (rewriting a previously-Posted leg)
-- ------------------------------------------------------------------------
-- WHY: When the back-office discovers a posting was wrong (typo on
--   the amount, wrong account, etc.) the canonical fix is to
--   re-insert the row with the corrected values + ``supersedes=
--   'TechnicalCorrection'``. Highest ``entry`` wins so the dashboards
--   see the corrected state; the original is preserved for audit.
-- Consumed by: L1 Supersession Audit sheet's TechnicalCorrection
--   bucket; Audit PDF supersession aggregate.

INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, origin,
    supersedes, metadata
) VALUES (
    'tx-EXAMPLE-005',                  -- same logical id as the original
    'acct-EXAMPLE-cust-0001', 'Customer #0001', 'CustomerDDA',
    'internal', 'DDAControl',
    -125.00,                           -- corrected amount (was -1250.00)
    'Debit', 'Posted',
    '2030-01-15T13:00:00',             -- posting unchanged from original
    'tr-EXAMPLE-005',
    'CustomerFeeAccrual', 'InternalInitiated',
    'TechnicalCorrection',
    '{"correction_reason": "amount_typo"}'
);
"""


_PATTERN_BUNDLED = """\
-- ------------------------------------------------------------------------
-- Pattern 6 — Bundled transfer (rail uses an Aggregating settlement)
-- ------------------------------------------------------------------------
-- WHY: Card-network settlements bundle hundreds of authorizations
--   into one wire. Each authorization gets its own row in
--   ``<prefix>_transactions`` carrying the ``bundle_id`` of the
--   eventual settlement. The aggregating rail's settlement leg
--   carries the same ``bundle_id`` plus ``status='Posted'``. L1's
--   Stuck Unbundled view fires on Posted-but-unbundled rows whose
--   age exceeds the rail's ``max_unbundled_age``.
-- Consumed by: L1 Unbundled Aging sheet; L2FT Rails sheet's Bundle
--   filter dropdown.

INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, bundle_id,
    origin, metadata
) VALUES
    (
        'tx-EXAMPLE-006-auth-1',
        'acct-EXAMPLE-merch-0001', 'Coffee Shop Merchant', 'MerchantDDA',
        'internal', 'MerchantDDAControl',
        4.25, 'Credit', 'Posted',
        '2030-01-15T08:15:00',
        'tr-EXAMPLE-006-auth-1',
        'MerchantCardSettlement',
        'bundle-EXAMPLE-001',          -- shared with the settlement leg
        'ExternalRailFeed', NULL
    ),
    (
        'tx-EXAMPLE-006-auth-2',
        'acct-EXAMPLE-merch-0001', 'Coffee Shop Merchant', 'MerchantDDA',
        'internal', 'MerchantDDAControl',
        7.75, 'Credit', 'Posted',
        '2030-01-15T08:42:00',
        'tr-EXAMPLE-006-auth-2',
        'MerchantCardSettlement',
        'bundle-EXAMPLE-001',          -- same bundle as above
        'ExternalRailFeed', NULL
    );
"""


_PATTERN_CHAINED = """\
-- ------------------------------------------------------------------------
-- Pattern 7 — Chained transfer (parent → child causality)
-- ------------------------------------------------------------------------
-- WHY: Some transfers fire because of an earlier transfer — a
--   reversal chain, a Fed-settlement triggered by a customer wire,
--   a fraud reversal triggered by a Posted authorization. Set
--   ``transfer_parent_id`` on the child to the parent's
--   ``transfer_id`` so the Investigation Money Trail + Account
--   Network sheets can walk the causality graph.
-- Consumed by: Investigation Money Trail sheet's recursive walk;
--   Investigation Account Network sheet's parent / child edges.

INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, transfer_parent_id,
    rail_name, origin, metadata
) VALUES (
    'tx-EXAMPLE-007',
    'acct-EXAMPLE-cust-0001', 'Customer #0001', 'CustomerDDA',
    'internal', 'DDAControl',
    -50.00, 'Debit', 'Posted',
    '2030-01-16T10:00:00',
    'tr-EXAMPLE-007-child',
    'tr-EXAMPLE-002',                  -- parent: the Pattern 2 transfer
    'CustomerFeeAccrual', 'InternalInitiated',
    '{"reason": "wire_outbound_fee"}'
);
"""


_PATTERN_DAILY_BALANCE = """\
-- ------------------------------------------------------------------------
-- Pattern 8 — Daily balance row (one per account-day)
-- ------------------------------------------------------------------------
-- WHY: The dashboards read a STORED end-of-day balance independent
--   of summing transactions — the ETL writes it once per
--   (account_id, business_day_start) at EOD close. The L1 Drift
--   invariant compares stored vs. SUM(signed_amount); a divergence
--   surfaces in the Drift sheet, which is how operations notices
--   missing or duplicated postings.
-- Consumed by: L1 Drift sheet (compares stored ``money`` against
--   computed); L1 Daily Statement sheet (opening + closing
--   balances); L1 Limit Breach sheet (reads static caps from
--   ``<prefix>_config.l2_yaml`` per Phase AW; per-day overrides
--   would come from ``metadata.limits`` here).

INSERT INTO <prefix>_daily_balances (
    account_id, account_name, account_role, account_scope,
    account_parent_role, expected_eod_balance, business_day_start,
    business_day_end, money, metadata
) VALUES (
    'acct-EXAMPLE-cust-0001',
    'Customer #0001',
    'CustomerDDA',
    'internal',
    'DDAControl',
    NULL,                              -- expected_eod_balance optional
    '2030-01-15T00:00:00',
    '2030-01-16T00:00:00',
    1875.00,                           -- stored EOD balance
    NULL                               -- no per-day metadata / overrides
);
"""


_PATTERN_DAILY_BALANCE_WITH_LIMITS = """\
-- ------------------------------------------------------------------------
-- Pattern 9 — Daily balance carrying per-day Limit Schedule override
-- ------------------------------------------------------------------------
-- WHY: Static LimitSchedule caps are read by the L1 Limit Breach
--   matview from ``<prefix>_config.l2_yaml`` (Phase AW); per-day
--   overrides — when an ETL wants to override the static cap for a
--   specific account-day — go under ``metadata.limits`` as a JSON
--   map keyed by ``rail_name`` (the LimitSchedule's ``rail`` field
--   per Z.B's symmetric collapse — formerly ``transfer_type``).
--   Phase AV (2026-05-23) renamed the column from ``limits`` to
--   ``metadata`` and demoted the per-rail caps to a nested key so
--   the column has room for siblings (scenario_id per AV.5).
-- Consumed by: nothing on main reads ``metadata.limits`` today;
--   reserved for ETL-side per-day override scenarios.

INSERT INTO <prefix>_daily_balances (
    account_id, account_name, account_role, account_scope,
    account_parent_role, expected_eod_balance, business_day_start,
    business_day_end, money, metadata
) VALUES (
    'acct-EXAMPLE-cust-0001',
    'Customer #0001',
    'CustomerDDA',
    'internal',
    'DDAControl',
    NULL,
    '2030-01-15T00:00:00',
    '2030-01-16T00:00:00',
    1875.00,
    -- Per-day cap override under ``metadata.limits``; keys are L2
    -- Rail names (LimitSchedule.rail). Sibling keys can live
    -- alongside (e.g. ``"scenario_id": "..."`` for AV.5).
    '{"limits": {"CustomerOutboundACH": 10000.00, "CustomerOutboundWire": 25000.00}}'
);
"""


_PATTERN_METADATA_EXTENSION = """\
-- ------------------------------------------------------------------------
-- Pattern 10 — Custom metadata key (the open-set extension contract)
-- ------------------------------------------------------------------------
-- WHY: ``metadata`` is the universal extras container. Add any
--   key your team needs (loan_id, originating_branch, fraud_score,
--   ...) — the dashboards' default reads ignore unknown keys. To
--   make a metadata key surface in the L2FT cascade dropdowns,
--   declare it in the L2 YAML's ``metadata_keys`` block; the
--   walkthrough at
--   ``walkthroughs/etl/how-do-i-add-a-metadata-key.md`` covers
--   the full extension contract.
-- Consumed by: L2FT Rails sheet's Metadata Key / Value cascade
--   (when the key is L2-declared); ad-hoc SQL via
--   ``JSON_VALUE(metadata, '$.your_key')`` (always).

INSERT INTO <prefix>_transactions (
    id, account_id, account_name, account_role, account_scope,
    account_parent_role, amount_money, amount_direction, status,
    posting, transfer_id, rail_name, origin, metadata
) VALUES (
    'tx-EXAMPLE-010',
    'acct-EXAMPLE-cust-0001', 'Customer #0001', 'CustomerDDA',
    'internal', 'DDAControl',
    -200.00, 'Debit', 'Posted',
    '2030-01-15T15:30:00',
    'tr-EXAMPLE-010',
    'CustomerOutboundACH', 'InternalInitiated',
    '{"customer_id": "cust-0001", "originating_branch": "BR-DENVER", "fraud_score": 0.02}'
);
"""


_PATTERNS: tuple[str, ...] = (
    _PATTERN_SINGLE_LEG_POSTED,
    _PATTERN_TWO_LEG_PAIRED,
    _PATTERN_FORCE_POSTED,
    _PATTERN_PENDING_THEN_POSTED,
    _PATTERN_TECHNICAL_CORRECTION,
    _PATTERN_BUNDLED,
    _PATTERN_CHAINED,
    _PATTERN_DAILY_BALANCE,
    _PATTERN_DAILY_BALANCE_WITH_LIMITS,
    _PATTERN_METADATA_EXTENSION,
)


def generate_etl_examples_sql() -> str:
    """Return the canonical INSERT-pattern SQL string.

    See module docstring for the full contract; the output is one
    pattern block per ``Pattern N`` heading, each carrying ``-- WHY:``
    + ``-- Consumed by:`` documentation. Output is stable across
    invocations (no random IDs, no timestamps that vary by clock).
    """
    return _HEADER + "\n" + "\n".join(_PATTERNS)
