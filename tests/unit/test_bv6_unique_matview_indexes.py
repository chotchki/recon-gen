"""BV.6 — UNIQUE index emit assertions for matviews.

Batch 1 (commit prior to 2026-06-10) promoted existing composite indexes
to UNIQUE (or added fresh UNIQUE indexes) on 18 matviews whose natural
keys were well-defined so PG can emit ``REFRESH MATERIALIZED VIEW
CONCURRENTLY``. The 19th candidate from the inventory
(``inv_pair_rolling_anomalies``) was disqualified mid-implementation
when the spec_example seed surfaced real duplicates on its claimed key
— see ``schema.py``'s ``refresh_matviews_sql._NON_UNIQUE_MATVIEWS`` for
the carve-out reasoning.

BV.6 finish (2026-06-10) added UNIQUE indexes to the last three
matviews per operator-locked design choices:

- ``fan_in_disagreement`` — composite UNIQUE on
  ``(child_transfer_id, chain_parent_name)`` (BV.6-1).
- ``l1_exceptions`` — 5-tuple UNIQUE on
  ``(check_type, account_id, business_day, rail_name, transfer_id)``;
  PG NULLs-distinct + Oracle composite-NULL-trivially-unique converge
  on the same shape (BV.6-2).
- ``inv_money_trail_edges`` — synthetic ``edge_seq`` column added via
  ``row_number() OVER (PARTITION BY root_transfer_id, source_account_id,
  target_account_id ORDER BY tgt.id, src.id)``, with UNIQUE on the
  4-tuple ``(root, src_account, tgt_account, edge_seq)`` (BV.6-3).

These tests walk the emitted schema SQL (no DB round-trip) and assert
the per-matview UNIQUE INDEX exists with the expected column tuple.
The matching ``refresh_matviews_sql`` emits ``CONCURRENTLY`` for every
matview NOT in ``_NON_UNIQUE_MATVIEWS`` (which is now just
``inv_pair_rolling_anomalies``).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import duckdb

from recon_gen.common.db import execute_script
from recon_gen.common.l2 import L2Instance
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine._emit_helpers import insert_tx, ts
from recon_gen.common.sql import Dialect


def _empty_instance() -> L2Instance:
    """Bare L2Instance — every matview emits regardless of L2 contents,
    so a content-free instance suffices for index-shape assertions."""
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


# Matview leaf-name → (column tuple, expected index name fragment).
# The column tuple is asserted via an exact-match regex (whitespace
# tolerant). Leaf names omit the ``<prefix>_`` prefix; tests inject
# their own prefix.
_BV6_UNIQUE_MATVIEWS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("current_transactions", ("id",), "curr_tx_id"),
    ("current_daily_balances", ("account_id", "business_day_start"), "curr_db_account_day"),
    ("computed_subledger_balance", ("account_id", "business_day_start"), "csb_account_day"),
    ("computed_ledger_balance", ("account_id", "business_day_start"), "clb_account_day"),
    ("effective_balances", ("account_id", "business_day_start"), "eff_balances_account_day"),
    ("drift", ("account_id", "business_day_start"), "drift_account_day"),
    ("ledger_drift", ("account_id", "business_day_start"), "ledger_drift_account_day"),
    ("overdraft", ("account_id", "business_day_start"), "overdraft_account_day"),
    ("balance_cadence_gap", ("account_id", "business_day_start"), "bcg_account_day"),
    ("expected_eod_balance_breach", ("account_id", "business_day_start"), "eod_breach_account_day"),
    ("limit_breach", ("account_id", "business_day", "rail_name", "direction"), "lb_unique"),
    ("stuck_pending", ("transaction_id",), "sp_unique"),
    ("stuck_unbundled", ("transaction_id",), "su_unique"),
    ("chain_parent_disagreement", ("transfer_id", "child_template_name"), "cpd_unique"),
    ("xor_group_violation", ("transfer_id", "template_name", "xor_group_index"), "xgv_unique"),
    ("transfer_parents", ("child_transfer_id", "parent_transfer_id"), "tp_unique"),
    ("multi_xor_violation", ("parent_transfer_id", "parent_rail_or_template_name"), "mxv_unique"),
    ("daily_statement_summary", ("account_id", "business_day_start"), "dss_account_day"),
    # BV.6 finish (2026-06-10) — last three from batch 1's carve-out:
    ("fan_in_disagreement", ("child_transfer_id", "chain_parent_name"), "fid_unique"),
    (
        "l1_exceptions",
        ("check_type", "account_id", "business_day", "rail_name", "transfer_id"),
        "l1ex_unique",
    ),
    (
        "inv_money_trail_edges",
        ("root_transfer_id", "source_account_id", "target_account_id", "edge_seq"),
        "mte_unique",
    ),
)

# Out-of-scope matviews (no UNIQUE INDEX yet — operator owns the key
# decision). The runtime contract is that refresh_matviews_sql must
# NOT emit ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` for these; the
# plain ``REFRESH MATERIALIZED VIEW`` form survives without a UNIQUE.
# Only inv_pair_rolling_anomalies remains after BV.6 finish.
_BV6_NON_UNIQUE_MATVIEWS: tuple[str, ...] = (
    # Disqualified mid-implementation — see module docstring.
    "inv_pair_rolling_anomalies",
)


def test_bv6_every_matview_in_scope_emits_create_unique_index() -> None:
    """Every matview in the BV.6 scope ships a ``CREATE UNIQUE INDEX``
    with the expected (matview, column-tuple) pair.

    Matches against the prefixed leaf name; the index name itself is
    asserted by substring in a separate test so the column-tuple check
    here stays isolated from name-style choices.
    """
    p = "bv6"
    sql = emit_schema(_empty_instance(), prefix=p, dialect=Dialect.POSTGRES)
    for leaf, columns, _idx_frag in _BV6_UNIQUE_MATVIEWS:
        cols_pattern = r"\s*,\s*".join(re.escape(c) for c in columns)
        pattern = re.compile(
            r"CREATE\s+UNIQUE\s+INDEX\s+\S+\s+ON\s+"
            + re.escape(f"{p}_{leaf}")
            + r"\s*\(\s*" + cols_pattern + r"\s*\)",
            re.IGNORECASE | re.DOTALL,
        )
        assert pattern.search(sql), (
            f"matview {leaf}: no CREATE UNIQUE INDEX on columns {columns} found in emit_schema SQL"
        )


def test_bv6_unique_index_names_match_expected_fragments() -> None:
    """Index names follow the documented convention (existing matviews
    keep their pre-BV.6 stub, fresh matviews use ``idx_<p>_<leaf>_unique``).
    A name-stability test pinned to the convention so a future rename
    surfaces here, not as a downstream perf surprise.
    """
    p = "bv6"
    sql = emit_schema(_empty_instance(), prefix=p, dialect=Dialect.POSTGRES)
    for leaf, _cols, idx_frag in _BV6_UNIQUE_MATVIEWS:
        expected = f"idx_{p}_{idx_frag}"
        assert expected in sql, (
            f"matview {leaf}: expected UNIQUE index name fragment "
            f"{expected!r} not found in emit_schema SQL"
        )


def test_bv6_pg_refresh_concurrently_for_unique_matviews() -> None:
    """``refresh_matviews_sql`` on PG emits ``REFRESH MATERIALIZED VIEW
    CONCURRENTLY`` for every matview that ships a UNIQUE index, plain
    ``REFRESH MATERIALIZED VIEW`` for the sole remaining carve-out
    (``inv_pair_rolling_anomalies``)."""
    p = "bv6"
    sql = refresh_matviews_sql(_empty_instance(), prefix=p, dialect=Dialect.POSTGRES)
    for leaf, _cols, _ in _BV6_UNIQUE_MATVIEWS:
        marker = f"REFRESH MATERIALIZED VIEW CONCURRENTLY {p}_{leaf};"
        assert marker in sql, (
            f"matview {leaf}: expected CONCURRENTLY refresh in PG refresh SQL"
        )
    for leaf in _BV6_NON_UNIQUE_MATVIEWS:
        marker = f"REFRESH MATERIALIZED VIEW {p}_{leaf};"
        assert marker in sql, (
            f"matview {leaf}: expected plain (non-concurrent) refresh in PG refresh SQL"
        )


def test_bv6_oracle_refresh_unchanged() -> None:
    """Oracle's DBMS_MVIEW.REFRESH has no CONCURRENTLY analogue at this
    layer; ``concurrently=True`` is a no-op on Oracle. Sanity check the
    refresh SQL on Oracle contains the DBMS_MVIEW shape for every
    matview, not a PG-style REFRESH CONCURRENTLY leak.
    """
    p = "bv6"
    sql = refresh_matviews_sql(_empty_instance(), prefix=p, dialect=Dialect.ORACLE)
    assert "CONCURRENTLY" not in sql
    for leaf, _cols, _ in _BV6_UNIQUE_MATVIEWS:
        marker = f"DBMS_MVIEW.REFRESH('{p}_{leaf}'"
        assert marker in sql, (
            f"matview {leaf}: expected DBMS_MVIEW.REFRESH call in Oracle refresh SQL"
        )


# =====================================================================
# BV.6 finish — inv_money_trail_edges edge_seq round-trip.
#
# The BV.6-3 design lock added a synthetic ``edge_seq`` column via
# ``row_number() OVER (PARTITION BY root_transfer_id, source_account_id,
# target_account_id ORDER BY tgt.id, src.id)``. A round-trip seeded with
# a transfer where the (root, src_account, tgt_account) cross-product
# yields multiple rows verifies edge_seq starts at 1 and increments per
# partition. Uses DuckDB :memory: + spec_example for the schema.
# =====================================================================

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)


def test_bv6_inv_money_trail_edges_edge_seq_increments_per_partition() -> None:
    """Seed two child transfers under one parent — each contributes a
    leg pair where (root, src_account, tgt_account) collides. edge_seq
    must disambiguate: start at 1, increment to 2, etc., one bucket per
    (root, src, tgt) triple.

    Specifically: build a parent transfer with one (Src → Mid) leg pair
    and two child transfers, each contributing a (Mid → End) leg pair
    on different days. The recursive walk yields:

      depth=0, src=Src,  tgt=Mid    → 1 edge_seq = 1
      depth=0, src=Src,  tgt=End    → 1 edge_seq = 1 (different chain hop)
      depth=1, src=Mid,  tgt=End    → 2 rows from cross-product over the
                                       2 child transfers' src × tgt legs
                                       → edge_seq = 1, 2

    The cross-product is what the row_number() disambiguator solves.
    """
    p = "bv6mt"
    instance = load_instance(_SPEC_EXAMPLE)
    conn = duckdb.connect(":memory:")
    try:
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(instance, prefix=p, dialect=Dialect.DUCKDB),
            dialect=Dialect.DUCKDB,
        )
        conn.commit()
        replace_config(
            conn, prefix=p,
            cfg_json="{}", l2_json=json.dumps({"rails": []}),
            as_of=datetime(2030, 1, 1, 12, 0, 0),
        )

        # Root: 1 (Src → Mid) leg pair, no parent.
        insert_tx(
            conn, id="root-src", account_id="acct-src", account_name="Src",
            account_role="ExternalCounterparty", account_scope="external",
            account_parent_role=None,
            amount_money=-100.0, amount_direction="Debit",
            status="Posted", posting=ts(datetime(2030, 1, 1).date()),
            transfer_id="xfer-root", rail_name="ach", origin="etl",
            prefix=p,
        )
        insert_tx(
            conn, id="root-tgt", account_id="acct-mid", account_name="Mid",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=100.0, amount_direction="Credit",
            status="Posted", posting=ts(datetime(2030, 1, 1).date()),
            transfer_id="xfer-root", rail_name="ach", origin="etl",
            prefix=p,
        )
        # Child A: (Mid → End), parent=root.
        insert_tx(
            conn, id="child-a-src", account_id="acct-mid", account_name="Mid",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=-50.0, amount_direction="Debit",
            status="Posted", posting=ts(datetime(2030, 1, 2).date()),
            transfer_id="xfer-child-a", transfer_parent_id="xfer-root",
            rail_name="ach", origin="etl", prefix=p,
        )
        insert_tx(
            conn, id="child-a-tgt", account_id="acct-end", account_name="End",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=50.0, amount_direction="Credit",
            status="Posted", posting=ts(datetime(2030, 1, 2).date()),
            transfer_id="xfer-child-a", transfer_parent_id="xfer-root",
            rail_name="ach", origin="etl", prefix=p,
        )
        # Child B: (Mid → End), parent=root (creates duplicate (root, Mid, End) triple).
        insert_tx(
            conn, id="child-b-src", account_id="acct-mid", account_name="Mid",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=-30.0, amount_direction="Debit",
            status="Posted", posting=ts(datetime(2030, 1, 3).date()),
            transfer_id="xfer-child-b", transfer_parent_id="xfer-root",
            rail_name="ach", origin="etl", prefix=p,
        )
        insert_tx(
            conn, id="child-b-tgt", account_id="acct-end", account_name="End",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=30.0, amount_direction="Credit",
            status="Posted", posting=ts(datetime(2030, 1, 3).date()),
            transfer_id="xfer-child-b", transfer_parent_id="xfer-root",
            rail_name="ach", origin="etl", prefix=p,
        )
        conn.commit()

        # Refresh inv_money_trail_edges (DuckDB does DROP + CREATE TABLE AS).
        execute_script(
            cur, refresh_matviews_sql(instance, prefix=p, dialect=Dialect.DUCKDB),
            dialect=Dialect.DUCKDB,
        )
        conn.commit()

        # Read all edges. Group by (root, src, tgt); edge_seq must be 1..N per bucket.
        cur.execute(
            f"SELECT root_transfer_id, source_account_id, target_account_id, edge_seq "
            f"FROM {p}_inv_money_trail_edges "
            f"ORDER BY root_transfer_id, source_account_id, target_account_id, edge_seq"
        )
        rows = cur.fetchall()
        assert rows, "expected at least one edge"

        from collections import defaultdict
        buckets: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for root, src, tgt, seq in rows:
            buckets[(root, src, tgt)].append(int(seq))

        # Each (root, src, tgt) partition has edge_seq = 1, 2, ..., N
        # (sorted, since ORDER BY in the SELECT above sorts by edge_seq).
        for key, seqs in buckets.items():
            expected = list(range(1, len(seqs) + 1))
            assert seqs == expected, (
                f"partition {key}: edge_seq must be 1..{len(seqs)}; got {seqs}"
            )

        # The (xfer-root, acct-mid, acct-end) bucket must have >= 2 edges
        # (one per child transfer's src×tgt leg cross-product), proving
        # the disambiguator's necessity.
        mid_to_end_seqs = buckets.get(("xfer-root", "acct-mid", "acct-end"), [])
        assert len(mid_to_end_seqs) >= 2, (
            f"expected ≥2 edges for the (root, Mid, End) cross-product; "
            f"got {mid_to_end_seqs}"
        )
    finally:
        conn.close()
