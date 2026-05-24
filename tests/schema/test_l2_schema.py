"""Schema-emitter tests for ``common.l2.emit_schema`` (M.1.4).

The emitter is a text-template, so most checks assert on the rendered
DDL string. A live-Postgres execution proof exists outside the unit
suite (verified manually via psycopg2 against ``run/config.yaml`` —
the M.0.10 pattern); the kitchen-sink integration test in M.1.6 will
formalize that.

Per the M.1 testing principle: every load-bearing schema feature
(prefix isolation, idempotency, the v6 column shape, the L1 Amount
invariant CHECK, portable JSON storage) gets a guard.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from recon_gen.common.l2 import (
    Identifier,
    L2Instance,
    emit_schema,
    refresh_matviews_sql,
)


def _strip_comments(sql: str) -> str:
    """Return SQL with line-comment lines (-- …) removed.

    Used by tests that assert on the absence of patterns like ``JSONB`` or
    ``GIN`` — those words legitimately appear in explanatory comments
    (e.g. ``-- portability constraint: no JSONB``) and we don't want
    those to trip the negative assertion.
    """
    return "\n".join(
        line for line in sql.split("\n")
        if not line.lstrip().startswith("--")
    )


def _instance(prefix: str) -> L2Instance:
    """A minimal L2Instance — schema emit doesn't read the entity lists."""
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


# -- Prefix isolation --------------------------------------------------------


def test_uses_l2_instance_prefix() -> None:
    """Tables + indexes carry the instance prefix per F10 isolation rule."""
    sql = emit_schema(_instance("ksk"), prefix="ksk")
    assert "CREATE TABLE ksk_transactions" in sql
    assert "CREATE TABLE ksk_daily_balances" in sql
    assert "CREATE INDEX idx_ksk_transactions_account_posting" in sql
    assert "CREATE INDEX idx_ksk_daily_balances_business_day" in sql


def test_two_instances_emit_isolated_table_names() -> None:
    """Two L2 instances coexist in one DB by using distinct prefixes."""
    a = emit_schema(_instance("aaa"), prefix="aaa")
    b = emit_schema(_instance("bbb"), prefix="bbb")
    assert "aaa_transactions" in a and "aaa_transactions" not in b
    assert "bbb_transactions" in b and "bbb_transactions" not in a


# -- Idempotency -------------------------------------------------------------


def test_emits_drop_before_create() -> None:
    """Every CREATE has a DROP IF EXISTS for the same object before it."""
    sql = emit_schema(_instance("idem"), prefix="idem")
    drop_idx = sql.index("DROP TABLE IF EXISTS idem_transactions")
    create_idx = sql.index("CREATE TABLE idem_transactions")
    assert drop_idx < create_idx, "DROP must precede CREATE"

    drop_db_idx = sql.index("DROP TABLE IF EXISTS idem_daily_balances")
    create_db_idx = sql.index("CREATE TABLE idem_daily_balances")
    assert drop_db_idx < create_db_idx


def test_drops_daily_balances_before_transactions_for_fk_safety() -> None:
    """Drop daily_balances first so any future FKs from it to transactions
    don't block the drop. Order matters in idempotent DDL."""
    sql = emit_schema(_instance("ord"), prefix="ord")
    db_drop = sql.index("DROP TABLE IF EXISTS ord_daily_balances")
    tx_drop = sql.index("DROP TABLE IF EXISTS ord_transactions ")
    assert db_drop < tx_drop


# -- v6 column shape per L1 SPEC ---------------------------------------------


def test_emits_entry_column_on_both_tables() -> None:
    """L1 F's Entry primitive — BIGSERIAL on both transactions + daily_balances."""
    sql = emit_schema(_instance("ent"), prefix="ent")
    # Both tables have entry BIGSERIAL
    assert "entry                BIGSERIAL      NOT NULL" in sql
    assert "entry                  BIGSERIAL      NOT NULL" in sql


def test_transactions_includes_amount_money_and_direction() -> None:
    """Per L1 SPEC: Amount = (Money, Direction); both columns present."""
    sql = emit_schema(_instance("amt"), prefix="amt")
    assert "amount_money         DECIMAL(20,2)  NOT NULL" in sql
    assert "amount_direction     VARCHAR(20)    NOT NULL" in sql
    assert "amount_direction IN ('Debit', 'Credit')" in sql


def test_transactions_includes_amount_invariant_check() -> None:
    """L1 Amount INVARIANT: money agrees with direction.

    money ≥ 0 if direction = Credit; money ≤ 0 if direction = Debit.
    Encoded as a Postgres CHECK so the DB rejects rows that violate it.
    """
    sql = emit_schema(_instance("inv"), prefix="inv")
    assert "amount_direction = 'Credit' AND amount_money >= 0" in sql
    assert "amount_direction = 'Debit'  AND amount_money <= 0" in sql


def test_transactions_includes_transfer_parent_id() -> None:
    """L1 SPEC: Transfer.Parent recursive chain (Phase L addition)."""
    sql = emit_schema(_instance("tp"), prefix="tp")
    assert "transfer_parent_id   VARCHAR(100)" in sql


def test_transactions_includes_transfer_completion_and_origin() -> None:
    """L1 SPEC: Transfer.Completion + Transaction.Origin both denormalized."""
    sql = emit_schema(_instance("co"), prefix="co")
    assert "transfer_completion  TIMESTAMP" in sql
    assert "origin               VARCHAR(50)    NOT NULL" in sql
    # Origin is open enum — no CHECK so integrators can extend.
    assert "origin IN" not in sql


def test_transactions_status_is_open_enum() -> None:
    """L1 SPEC says Status ⊇ {Posted}. No closed CHECK on status."""
    sql = emit_schema(_instance("st"), prefix="st")
    assert "status               VARCHAR(50)    NOT NULL" in sql
    # No CHECK constraint on status (would close the enum).
    assert "status IN" not in sql


def test_transactions_rail_name_is_open_enum() -> None:
    """Z.B (2026-05-15): rail_name carries the per-leg type identifier
    (Rail.transfer_type and the legacy column collapsed away). The
    column stays open-set — integrators add new rails by extending the
    L2 instance, no CHECK constraint."""
    sql = emit_schema(_instance("tt"), prefix="tt")
    assert "rail_name            VARCHAR(100)   NOT NULL" in sql
    # No CHECK on rail_name — keeps the column extensible.
    assert "rail_name IN" not in sql
    # Z.B: no transfer_type column at all (collapsed into rail_name).
    # Narrative SQL comments may still reference the old name historically;
    # the column declaration is what matters.
    assert "transfer_type        VARCHAR" not in sql
    assert "transfer_type INTEGER" not in sql
    assert "transfer_type IN" not in sql


def test_daily_balances_includes_expected_eod_and_metadata() -> None:
    """L1 SPEC: ExpectedEODBalance + open metadata JSON both denormalized
    onto the row.

    AV (2026-05-23) renamed ``limits`` → ``metadata`` and demoted the
    per-rail caps to a nested ``metadata.limits`` key so the column
    has room for siblings (scenario_id per AV.5, future per-day tags).
    Bounded VARCHAR so the column behaves like a string on both
    dialects (CLOB cannot be aggregated; bounded VARCHAR can)."""
    sql = emit_schema(_instance("eb"), prefix="eb")
    assert "expected_eod_balance   DECIMAL(20,2)" in sql
    assert "metadata               VARCHAR(4000)" in sql
    # Old column name MUST NOT appear in the CREATE TABLE block (a
    # half-done rename would leave it).
    assert "limits                 VARCHAR(4000)" not in sql


def test_daily_balances_money_is_signed() -> None:
    """L1 Non-negative Stored Balance is SHOULD, not MUST.

    Overdraft is observable — the balance column accepts negatives so the
    dashboard can surface them. The transactions table has a sign-direction
    CHECK on its ``amount_money``, but daily_balances has no CHECK constraint
    on its ``money`` column at all.

    M.1a.7's ``<prefix>_overdraft`` view legitimately filters on
    ``money < 0`` — that's the whole point of the view. The regex below
    looks for a CHECK on the bare ``money`` column inside the
    daily_balances CREATE TABLE block, NOT any usage in a downstream view.
    """
    sql = emit_schema(_instance("sg"), prefix="sg")
    assert "money                  DECIMAL(20,2)  NOT NULL" in sql
    # Find the daily_balances CREATE TABLE block specifically.
    db_block_match = re.search(
        r"CREATE TABLE sg_daily_balances\s*\((.*?)\);",
        sql,
        re.DOTALL,
    )
    assert db_block_match is not None
    db_block = _strip_comments(db_block_match.group(1))
    # Within that block, no CHECK references the bare ``money`` column.
    # (The amount_money CHECK lives in the transactions table, not here.)
    assert re.search(r"CHECK\s*\([^)]*\bmoney\b", db_block) is None


def test_daily_balances_business_day_window_check() -> None:
    """A BusinessDay's end MUST be after its start."""
    sql = emit_schema(_instance("bd"), prefix="bd")
    assert "business_day_end > business_day_start" in sql


# -- Portability constraint --------------------------------------------------


def test_metadata_uses_text_with_is_json_check() -> None:
    """SPEC's portability constraint: bounded VARCHAR + IS JSON, not JSONB.

    Bounded so the column behaves like a string on both dialects —
    Oracle CLOB can't be aggregated, ordered, or compared with VARCHAR
    literals (ORA-00932). 4000 chars covers every JSON metadata
    document the L2 schema emits in practice.
    """
    sql = emit_schema(_instance("p"), prefix="p")
    # transactions.metadata (longstanding).
    assert "metadata             VARCHAR(4000)" in sql
    assert "metadata IS NULL OR metadata IS JSON" in sql
    # daily_balances.metadata (AV: was ``limits``; same JSON-validity
    # contract; both base tables now carry the symmetric column).
    assert "metadata               VARCHAR(4000)" in sql
    # No JSONB type used in any actual SQL statement (comments allowed).
    assert "JSONB" not in _strip_comments(sql).upper()


def test_no_gin_indexes_per_portability_constraint() -> None:
    """SPEC: no GIN indexes on JSON; B-tree only.

    Checks for the actual GIN-index syntax (``USING GIN``) rather than
    the bare substring 'GIN' — which would match ``ORIGIN`` (the
    Transaction.Origin column name) and produce false positives.
    """
    sql = emit_schema(_instance("g"), prefix="g")
    no_comments = _strip_comments(sql)
    assert "USING GIN" not in no_comments.upper()
    # B-tree is the default and only allowed; assert at least one B-tree
    # index actually got emitted to make the negative meaningful.
    assert "CREATE INDEX" in no_comments


# -- Primary keys -----------------------------------------------------------


def test_transactions_pk_includes_entry() -> None:
    """Per L1 F: physical row key is (id, entry); logical key is id."""
    sql = emit_schema(_instance("pk1"), prefix="pk1")
    assert "PRIMARY KEY (id, entry)" in sql


def test_daily_balances_pk_includes_entry() -> None:
    """Per L1 F: physical row key is (account_id, business_day_start, entry)."""
    sql = emit_schema(_instance("pk2"), prefix="pk2")
    assert "PRIMARY KEY (account_id, business_day_start, entry)" in sql


# -- Account denormalization (per Implementation Entities) ------------------


@pytest.mark.parametrize("col", [
    "account_id", "account_name", "account_role",
    "account_scope", "account_parent_role",
])
def test_transactions_denormalizes_account(col: str) -> None:
    """SPEC's StoredTransaction = Transaction + Transfer + Account fields."""
    sql = emit_schema(_instance("a"), prefix="a")
    # Both tables carry the account fields.
    assert f"  {col}" in sql or f"  {col} " in sql


@pytest.mark.parametrize("col", [
    "account_id", "account_name", "account_role",
    "account_scope", "account_parent_role",
])
def test_daily_balances_denormalizes_account(col: str) -> None:
    """SPEC's DailyBalance = StoredBalance + Account fields."""
    sql = emit_schema(_instance("a"), prefix="a")
    # Just verify the column name appears (we already assert on transactions
    # via the same parametrize; here we trust same-string-in-template).
    assert col in sql


# -- Current* views (M.1.5) --------------------------------------------------


def test_emits_current_transactions_view() -> None:
    """L1 CurrentTransaction theorem materialized as max-Entry-per-ID view."""
    sql = emit_schema(_instance("v"), prefix="v")
    assert "CREATE MATERIALIZED VIEW v_current_transactions AS" in sql
    # Per L1 CurrentTransaction set-comprehension definition: the view
    # selects rows whose entry equals the max entry for the same logical id.
    assert "WHERE tx.entry = (" in sql
    assert "SELECT MAX(entry) FROM v_transactions WHERE id = tx.id" in sql


def test_emits_current_daily_balances_view() -> None:
    """L1 CurrentStoredBalance theorem materialized as max-Entry-per-(account,day) view."""
    sql = emit_schema(_instance("v"), prefix="v")
    assert "CREATE MATERIALIZED VIEW v_current_daily_balances AS" in sql
    # Per L1 CurrentStoredBalance: max-Entry per (Account, BusinessDay).
    assert "WHERE sb.entry = (" in sql
    assert "WHERE account_id = sb.account_id" in sql
    assert "AND business_day_start = sb.business_day_start" in sql


def test_view_drops_precede_table_drops() -> None:
    """Views must drop before tables they depend on (Postgres dependency)."""
    sql = emit_schema(_instance("ord"), prefix="ord")
    view_drop = sql.index("DROP MATERIALIZED VIEW IF EXISTS ord_current_transactions")
    table_drop = sql.index("DROP TABLE IF EXISTS ord_transactions ")
    assert view_drop < table_drop


def test_view_creates_after_table_creates() -> None:
    """Views must be created after the tables they reference."""
    sql = emit_schema(_instance("ord"), prefix="ord")
    table_create = sql.index("CREATE TABLE ord_transactions")
    view_create = sql.index("CREATE MATERIALIZED VIEW ord_current_transactions")
    assert table_create < view_create


def test_views_use_l2_instance_prefix() -> None:
    """View names + their referenced base tables share the prefix."""
    sql = emit_schema(_instance("aaa"), prefix="aaa")
    assert "aaa_current_transactions" in sql
    assert "aaa_current_daily_balances" in sql
    # The view's body references the prefixed base tables, not bare names.
    assert "FROM aaa_transactions tx" in sql
    assert "FROM aaa_daily_balances sb" in sql


# -- v1.1 columns (M.1a.1 — SPEC catch-up) -----------------------------------


def test_transactions_includes_rail_name_not_null() -> None:
    """SPEC: every leg's L2 Rail name denormalized so BundleSelector by
    RailName resolves to a simple WHERE without a transfer→rail join."""
    sql = emit_schema(_instance("v11"), prefix="v11")
    assert "rail_name            VARCHAR(100)   NOT NULL" in sql


def test_transactions_includes_template_name_nullable() -> None:
    """SPEC: TransferTemplate name; NULL when the leg posts standalone."""
    sql = emit_schema(_instance("v11"), prefix="v11")
    # NULL allowed: just the column declaration, no NOT NULL.
    assert re.search(
        r"\btemplate_name\s+VARCHAR\(100\)(?!\s+NOT NULL)",
        sql,
    ), "template_name should be nullable"


def test_transactions_includes_bundle_id_nullable() -> None:
    """SPEC: L1 Transaction.BundleId — populated by AggregatingRail bundlers."""
    sql = emit_schema(_instance("v11"), prefix="v11")
    assert re.search(
        r"\bbundle_id\s+VARCHAR\(100\)(?!\s+NOT NULL)",
        sql,
    ), "bundle_id should be nullable"


def test_transactions_includes_supersedes_open_enum() -> None:
    """SPEC: L1 Transaction.Supersedes — open enum, no CHECK so integrators
    may extend the v1 set (Inflight / BundleAssignment / TechnicalCorrection)."""
    sql = emit_schema(_instance("v11"), prefix="v11")
    assert re.search(
        r"\bsupersedes\s+VARCHAR\(50\)(?!\s+NOT NULL)",
        sql,
    ), "supersedes should be nullable + open enum"
    # Confirm no CHECK constraint constrains the supersedes value set.
    assert re.search(
        r"CHECK\s*\(\s*supersedes\s+IN\s*\(",
        sql,
    ) is None, "supersedes must be open enum (no CHECK)"


def test_daily_balances_includes_supersedes_open_enum() -> None:
    """SPEC: L1 StoredBalance.Supersedes — only TechnicalCorrection applies
    in practice but the column is open enum to match the transactions side."""
    sql = emit_schema(_instance("v11"), prefix="v11")
    # Find the daily_balances CREATE TABLE block specifically.
    db_block_match = re.search(
        r"CREATE TABLE v11_daily_balances\s*\((.*?)\);",
        sql,
        re.DOTALL,
    )
    assert db_block_match is not None
    db_block = db_block_match.group(1)
    assert re.search(
        r"\bsupersedes\s+VARCHAR\(50\)(?!\s+NOT NULL)",
        db_block,
    ), "daily_balances.supersedes should be nullable + open enum"


def test_emits_bundler_eligibility_index() -> None:
    """SPEC: AggregatingRails query for Posted, unbundled rows by rail_name.
    Partial index on `bundle_id IS NULL` keeps it small as bundled count grows."""
    sql = emit_schema(_instance("be"), prefix="be")
    assert "CREATE INDEX idx_be_transactions_bundler_eligibility" in sql
    assert "ON be_transactions (rail_name, status)" in sql
    assert "WHERE bundle_id IS NULL" in sql


def test_bundler_eligibility_index_drops_before_create() -> None:
    """The new bundler index participates in the same DROP-before-CREATE
    idempotency the other indexes follow."""
    sql = emit_schema(_instance("be"), prefix="be")
    drop_idx = sql.index("DROP INDEX IF EXISTS idx_be_transactions_bundler_eligibility")
    create_idx = sql.index("CREATE INDEX idx_be_transactions_bundler_eligibility")
    assert drop_idx < create_idx


# -- L1 invariant views (M.1a.7) --------------------------------------------


def _instance_with_limits(prefix: str) -> L2Instance:
    """L2 instance with one LimitSchedule entry — exercises the
    inline-CASE-branch lookup the limit_breach view uses."""
    from decimal import Decimal
    from recon_gen.common.l2 import LimitSchedule
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(
            LimitSchedule(
                parent_role=Identifier("DDAControl"),
                rail=Identifier("ach"),
                cap=Decimal("12000.00"),
            ),
        ),
    )


_L1_VIEW_NAMES = (
    "computed_subledger_balance",
    "computed_ledger_balance",
    "drift",
    "ledger_drift",
    "overdraft",
    "expected_eod_balance_breach",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
)


@pytest.mark.parametrize("view", _L1_VIEW_NAMES)
def test_l1_invariant_view_emitted_per_instance(view: str) -> None:
    """Every L1 invariant view appears in the emit_schema output, prefixed
    by the L2 instance name."""
    sql = emit_schema(_instance("v6"), prefix="v6")
    assert f"CREATE MATERIALIZED VIEW v6_{view}" in sql
    assert f"DROP MATERIALIZED VIEW IF EXISTS v6_{view}" in sql


@pytest.mark.parametrize("view", _L1_VIEW_NAMES)
def test_l1_invariant_view_drops_before_creates(view: str) -> None:
    """Drop precedes create — idempotency holds for the L1 invariant view block."""
    sql = emit_schema(_instance("v6"), prefix="v6")
    drop_idx = sql.index(f"DROP MATERIALIZED VIEW IF EXISTS v6_{view}")
    create_idx = sql.index(f"CREATE MATERIALIZED VIEW v6_{view}")
    assert drop_idx < create_idx


def test_l1_invariant_views_drop_before_base_table_drops() -> None:
    """L1 views depend on the Current* views (which depend on the base
    tables). DROPing Current* before L1 views fails with "dependent
    objects still exist" on a re-run, so L1 view drops MUST emit
    before the base block's drops. Surfaced by M.2.6's first run
    against real Postgres."""
    sql = emit_schema(_instance("ord"), prefix="ord")
    # All L1 view drops emit before the base block's first DROP VIEW.
    overdraft_drop = sql.index("DROP MATERIALIZED VIEW IF EXISTS ord_overdraft")
    current_drop = sql.index(
        "DROP MATERIALIZED VIEW IF EXISTS ord_current_daily_balances",
    )
    assert overdraft_drop < current_drop


def test_l1_invariant_view_drops_emit_at_top_of_script() -> None:
    """Catches the regression — every L1 view drop happens before any
    base-block CREATE (the base block contains the Current* drops + all
    base creates as a single chunk)."""
    sql = emit_schema(_instance("top"), prefix="top")
    first_create = sql.index("CREATE TABLE top_transactions")
    for view in _L1_VIEW_NAMES:
        drop_idx = sql.index(f"DROP MATERIALIZED VIEW IF EXISTS top_{view}")
        assert drop_idx < first_create, (
            f"top_{view} drop must come before any CREATE TABLE"
        )


def test_drift_view_filters_to_leaf_accounts_with_nonzero_drift() -> None:
    """`<prefix>_drift` is a leaf-account view (account_parent_role IS NOT NULL)
    that returns only rows where stored ≠ computed."""
    sql = emit_schema(_instance("dr"), prefix="dr")
    drift_match = re.search(
        r"CREATE MATERIALIZED VIEW dr_drift AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert drift_match is not None
    body = drift_match.group(1)
    assert "account_parent_role IS NOT NULL" in body
    assert "money <> cb.computed_balance" in body
    assert "account_scope = 'internal'" in body


def test_ledger_drift_view_filters_to_parent_accounts() -> None:
    """`<prefix>_ledger_drift` returns rows where stored ≠ computed at the
    parent-account level (uses computed_ledger_balance helper which itself
    is gated to accounts whose role IS a parent_role)."""
    sql = emit_schema(_instance("ld"), prefix="ld")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW ld_ledger_drift AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "money <> cb.computed_balance" in body
    assert "JOIN ld_computed_ledger_balance" in body


def test_overdraft_view_filters_internal_money_lt_zero() -> None:
    """`<prefix>_overdraft` returns internal accounts × days where money < 0."""
    sql = emit_schema(_instance("ov"), prefix="ov")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW ov_overdraft AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "money < 0" in body
    assert "account_scope = 'internal'" in body


def test_expected_eod_balance_breach_excludes_null_expectations() -> None:
    """Accounts without expected_eod_balance set MUST NOT appear — the
    SHOULD-constraint is only meaningful for accounts the L2 declared
    an expectation for."""
    sql = emit_schema(_instance("eod"), prefix="eod")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW eod_expected_eod_balance_breach AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "expected_eod_balance IS NOT NULL" in body
    assert "money <> sb.expected_eod_balance" in body


def test_limit_breach_reads_limit_schedule_caps_from_config() -> None:
    """AW.4 (2026-05-23): per-LimitSchedule caps no longer baked as
    CASE branches at emit time. The matview LEFT JOINs `<prefix>_config
    .l2_yaml.$.limit_schedules` and reads cap per-row via JSON path
    (multi-key filter: parent_role + rail + direction). Matview is
    persona-blind — same SQL across all L2s."""
    sql = emit_schema(_instance_with_limits("lb"), prefix="lb")
    # No per-LimitSchedule literals baked.
    assert (
        "WHEN tx.account_parent_role = 'DDAControl' "
        "AND tx.rail_name = 'ach' THEN 12000"
    ) not in sql
    # The JOIN clause reads from the config table's l2_yaml.
    assert "FROM lb_config" in sql
    # JSON_VALUE on PG/Oracle reads the cap field.
    assert "JSON_VALUE(ls.value, '$.cap')" in sql
    # Direction filter lives in the JOIN's ON clause.
    assert "= 'Outbound'" in sql
    assert "= 'Inbound'" in sql


def test_limit_breach_view_emits_uniform_body_with_no_limit_schedules() -> None:
    """AW.4 (2026-05-23): matview body is the same whether or not the
    L2 declares LimitSchedules. With no schedules in the JSON, the LEFT
    JOIN finds no matches → cap is NULL → outer WHERE `cap IS NOT NULL`
    filters → matview is inert."""
    sql = emit_schema(_instance("nolim"), prefix="nolim")  # _instance has no limit_schedules
    assert "CREATE MATERIALIZED VIEW nolim_limit_breach" in sql
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW nolim_limit_breach AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # New shape: LEFT JOIN to config's l2_yaml limit_schedules iteration.
    assert "FROM nolim_config" in body
    # Outer WHERE still filters `cap IS NOT NULL`.
    assert "WHERE cap IS NOT NULL" in body


def test_limit_breach_view_does_not_join_daily_balances() -> None:
    """v6's denormalized account_parent_role on transactions means the
    view reads parent role directly from the transaction row, no JOIN
    to daily_balances. Catches the bug M.2.6's first run surfaced:
    a JOIN-based view fails when no daily_balance row exists for the
    breach business_day."""
    sql = emit_schema(_instance_with_limits("nojoin"), prefix="nojoin")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW nojoin_limit_breach AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # No JOIN on daily_balances anywhere in the view body.
    assert "_current_daily_balances" not in body
    assert "_daily_balances" not in body


def test_limit_breach_view_ab1_per_direction_shape() -> None:
    """AB.1: the limit_breach matview now UNIONs Outbound + Inbound
    branches, each carrying an explicit `direction` literal column
    and its own amount_direction filter.
    """
    sql = emit_schema(_instance_with_limits("ab1"), prefix="ab1")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW ab1_limit_breach AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # Both literal direction column projections present.
    assert "'Outbound' AS direction" in body
    assert "'Inbound' AS direction" in body
    # Outbound branch filters Debit; Inbound branch filters Credit.
    assert "amount_direction = 'Debit'" in body
    assert "amount_direction = 'Credit'" in body
    # UNION ALL stitches them together.
    assert "UNION ALL" in body
    # Outer breach filter still gates `cap IS NOT NULL AND outbound_total > cap`.
    assert "WHERE cap IS NOT NULL" in body
    # New direction index on the matview.
    assert "idx_ab1_lb_direction" in sql


def test_chain_parent_disagreement_view_ab2_shape() -> None:
    """AB.2.3: the chain_parent_disagreement matview groups by
    (transfer_id, template_name) over current_transactions and surfaces
    any child Transfer where leg_rail firings claim ≥2 distinct
    parent_transfer_id values.

    Shape contract: filters keep rail-as-child chains out
    (`template_name IS NOT NULL`), exclude Failed legs (whose metadata
    is unreliable), and require ≥1 parent claim per row
    (`transfer_parent_id IS NOT NULL`). HAVING gates on distinct
    parent count > 1.
    """
    # Empty L2 is enough — the matview template is rendered regardless
    # of declared content; the body shape is what we assert.
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="ab2",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW ab2_chain_parent_disagreement AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None, (
        "chain_parent_disagreement matview missing from emit_schema output"
    )
    body = body_match.group(1)
    # Projection columns the dashboard reads.
    assert "tx.transfer_id" in body
    assert "tx.template_name AS child_template_name" in body
    assert "AS business_day" in body
    assert "COUNT(DISTINCT tx.transfer_parent_id) AS distinct_parent_count" in body
    assert "MIN(tx.transfer_parent_id) AS parent_transfer_id_min" in body
    assert "MAX(tx.transfer_parent_id) AS parent_transfer_id_max" in body
    # Filters keep rail-as-child chains + Failed legs out.
    assert "tx.transfer_parent_id IS NOT NULL" in body
    assert "tx.template_name IS NOT NULL" in body
    assert "tx.status <> 'Failed'" in body
    # GROUP BY (transfer_id, template_name) + HAVING > 1.
    assert "GROUP BY tx.transfer_id, tx.template_name" in body
    assert "HAVING COUNT(DISTINCT tx.transfer_parent_id) > 1" in body
    # Three indexes for dashboard hot paths.
    assert "idx_ab2_cpd_transfer" in sql
    assert "idx_ab2_cpd_template" in sql
    assert "idx_ab2_cpd_day" in sql


def test_todays_exceptions_includes_chain_parent_disagreement_branch() -> None:
    """AB.2.9: Today's Exceptions matview UNION ALLs a row category for
    chain_parent_disagreement violations, with check_type='chain_parent_disagreement'
    and magnitude = distinct_parent_count."""
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="te2",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW te2_todays_exceptions AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "'chain_parent_disagreement'" in body
    assert "FROM te2_chain_parent_disagreement" in body
    assert "child_template_name AS rail_name" in body
    assert "distinct_parent_count AS magnitude" in body


def _instance_with_pending_age(prefix: str) -> L2Instance:
    """L2 instance with one Rail carrying a `max_pending_age` so the
    stuck_pending view's CASE-branch lookup has data to match against.
    Uses a SingleLegRail; the case-branch helper walks both kinds.
    """
    from datetime import timedelta
    from recon_gen.common.l2 import SingleLegRail
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(
            SingleLegRail(
                name=Identifier("ach-credit"),
                metadata_keys=(),
                leg_role="DDAControl",
                leg_direction="Credit",
                origin="InternalInitiated",
                max_pending_age=timedelta(hours=24),
            ),
        ),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def test_stuck_pending_emits_with_status_and_age_filter() -> None:
    """`<prefix>_stuck_pending` returns transactions where status is
    Pending AND the live age exceeds the rail's `max_pending_age` cap.
    Both filters live in the view body."""
    sql = emit_schema(_instance_with_pending_age("sp"), prefix="sp")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW sp_stuck_pending AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "ct.status = 'Pending'" in body
    assert "max_pending_age_seconds IS NOT NULL" in body
    assert "age_seconds > tx.max_pending_age_seconds" in body
    # AW.2 (2026-05-23): age is computed against the owned temporal
    # frame `<prefix>_config.as_of`, not the matview engine's wall-clock
    # CURRENT_TIMESTAMP. Audit §6 "own the temporal frame."
    assert (
        "EXTRACT(EPOCH FROM ((SELECT as_of FROM "
    ) in body
    assert "_config) - ct.posting))" in body


def test_stuck_pending_reads_rail_max_pending_age_from_config() -> None:
    """AW.3 (2026-05-23): per-rail caps no longer baked as CASE branches.
    The matview body LEFT JOINs `<prefix>_config.l2_yaml` via the
    dialect-portable rails-iteration; the cap is extracted from the
    iteration row's JSON field. Matview is now persona-blind — same SQL
    across all L2 instances."""
    sql = emit_schema(_instance_with_pending_age("sp2"), prefix="sp2")
    # No per-rail literals baked.
    assert "WHEN ct.rail_name = 'ach-credit' THEN 86400" not in sql
    # The JOIN clause reads from the config table's l2_yaml.
    assert "FROM sp2_config" in sql
    # JSON_VALUE on PG/Oracle reads the field via SQL/JSON path.
    assert "JSON_VALUE(rail.value, '$.max_pending_age_seconds')" in sql


def test_stuck_pending_view_emits_uniform_body_with_no_aging_rails() -> None:
    """After AW.3, the matview body shape is the SAME whether or not
    any rails have `max_pending_age` set — the body iterates the
    `l2_yaml.rails` JSON array at refresh time and per-row evaluates
    the extracted cap. An L2 instance whose rails have NO
    `max_pending_age` set produces rows where `max_pending_age_seconds`
    is NULL (no field in the JSON) → outer WHERE filters them out →
    matview is inert. The matview's CREATE SQL is the same regardless
    of L2 contents."""
    sql = emit_schema(_instance("noage"), prefix="noage")
    assert "CREATE MATERIALIZED VIEW noage_stuck_pending" in sql
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW noage_stuck_pending AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # New shape: LEFT JOIN to config's l2_yaml rails iteration.
    assert "FROM noage_config" in body
    # Outer WHERE still filters NULL caps so an inert NULL excludes
    # every row.
    assert "max_pending_age_seconds IS NOT NULL" in body


def test_stuck_pending_view_indexes_emit() -> None:
    """Dashboard hot-path indexes on rail_name / account_id / transfer_id
    — same shape as the other L1 invariant matviews."""
    sql = emit_schema(_instance_with_pending_age("idx"), prefix="idx")
    assert "CREATE INDEX idx_idx_sp_rail ON idx_stuck_pending (rail_name);" in sql
    assert "CREATE INDEX idx_idx_sp_account ON idx_stuck_pending (account_id);" in sql
    assert "CREATE INDEX idx_idx_sp_transfer ON idx_stuck_pending (transfer_id);" in sql


def _instance_with_unbundled_age(prefix: str) -> L2Instance:
    """L2 instance with one Rail carrying a `max_unbundled_age` so the
    stuck_unbundled view's CASE-branch lookup has data to match against.
    Uses a SingleLegRail; the case-branch helper walks both kinds.
    """
    from datetime import timedelta
    from recon_gen.common.l2 import SingleLegRail
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(
            SingleLegRail(
                name=Identifier("ach-orig"),
                metadata_keys=(),
                leg_role="DDAControl",
                leg_direction="Credit",
                origin="InternalInitiated",
                max_unbundled_age=timedelta(days=1),
            ),
        ),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def test_stuck_unbundled_emits_with_bundle_status_and_age_filter() -> None:
    """`<prefix>_stuck_unbundled` returns Posted transactions where
    bundle_id IS NULL AND the live age exceeds the rail's
    `max_unbundled_age` cap. Status filter is 'Posted' (vs 'Pending'
    for stuck_pending) since AggregatingRails only bundle posted legs."""
    sql = emit_schema(_instance_with_unbundled_age("su"), prefix="su")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW su_stuck_unbundled AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "ct.bundle_id IS NULL" in body
    assert "ct.status = 'Posted'" in body
    assert "max_unbundled_age_seconds IS NOT NULL" in body
    assert "age_seconds > tx.max_unbundled_age_seconds" in body
    # AW.2 (2026-05-23): age reads from <prefix>_config.as_of.
    assert (
        "EXTRACT(EPOCH FROM ((SELECT as_of FROM "
    ) in body
    assert "_config) - ct.posting))" in body


def test_stuck_unbundled_reads_rail_max_unbundled_age_from_config() -> None:
    """AW.3 (2026-05-23): same as stuck_pending — caps read from
    `<prefix>_config.l2_yaml` via LEFT JOIN; matview body is
    persona-blind."""
    sql = emit_schema(_instance_with_unbundled_age("su2"), prefix="su2")
    assert "WHEN ct.rail_name = 'ach-orig' THEN 86400" not in sql
    assert "FROM su2_config" in sql
    assert "JSON_VALUE(rail.value, '$.max_unbundled_age_seconds')" in sql


def test_stuck_unbundled_view_emits_uniform_body_with_no_bundling_rails() -> None:
    """AW.3 (2026-05-23): the matview body is the same regardless of
    L2 rails contents. With no rails carrying `max_unbundled_age`, the
    JSON path returns NULL → outer WHERE filters → matview inert."""
    sql = emit_schema(_instance("nobun"), prefix="nobun")
    assert "CREATE MATERIALIZED VIEW nobun_stuck_unbundled" in sql
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW nobun_stuck_unbundled AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # New shape: LEFT JOIN to config's l2_yaml rails iteration.
    assert "FROM nobun_config" in body
    # Outer WHERE still filters NULL caps.
    assert "max_unbundled_age_seconds IS NOT NULL" in body


def test_stuck_unbundled_view_indexes_emit() -> None:
    """Same hot-path indexes (rail_name / account_id / transfer_id) as
    stuck_pending — the M.2b.11 Unbundled Aging sheet uses the same
    filter dropdowns."""
    sql = emit_schema(_instance_with_unbundled_age("ix"), prefix="ix")
    assert "CREATE INDEX idx_ix_su_rail ON ix_stuck_unbundled (rail_name);" in sql
    assert "CREATE INDEX idx_ix_su_account ON ix_stuck_unbundled (account_id);" in sql
    assert "CREATE INDEX idx_ix_su_transfer ON ix_stuck_unbundled (transfer_id);" in sql


def test_computed_subledger_balance_uses_current_transactions_view() -> None:
    """The helper view reads from Current* (technical-error supersession
    transparent) rather than the raw transactions base table."""
    sql = emit_schema(_instance("h"), prefix="h")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW h_computed_subledger_balance AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "FROM h_current_transactions" in body
    assert "tx.status = 'Posted'" in body


def test_computed_ledger_balance_unions_children_plus_direct_postings() -> None:
    """Per SPEC LedgerDrift: parent computed = Σ children stored + Σ
    direct ledger postings."""
    sql = emit_schema(_instance("clb"), prefix="clb")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW clb_computed_ledger_balance AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # Children sub-balance: SUM(child_db.money) grouped by parent role + day
    assert "SUM(child_db.money)" in body
    assert "child_db.account_parent_role" in body
    # Direct postings: SUM(tx.amount_money) per ledger account-day
    assert "SUM(tx.amount_money)" in body


# -- M.1a.9: matview refresh helper -----------------------------------------


def _baseline_instance() -> L2Instance:
    """Minimum L2Instance fixture for refresh-helper tests."""
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def test_xor_group_violation_view_empty_branch_when_no_groups_declared(
) -> None:
    """AB.3.3: when no TransferTemplate declares ``leg_rail_xor_groups``,
    the xor_group_violation matview is still emitted (every L1 invariant
    matview is unconditional — keeps the refresh DAG stable) but its
    body short-circuits with ``WHERE 1=0`` so it produces zero rows
    AND parses cleanly on all 3 dialects. The empty-branch is the
    pre-AB.3.5.spec path; no fixture in this test declares groups."""
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="xg",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW xg_xor_group_violation AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None, (
        "xor_group_violation matview missing from emit_schema output"
    )
    body = body_match.group(1)
    # Typed-NULL placeholder SELECT + WHERE 1=0 — see schema.py.
    assert "WHERE 1=0" in body
    # No xor_groups CTE in the empty path.
    assert "xor_groups" not in body


def test_xor_group_violation_view_populated_when_groups_declared() -> None:
    """AB.3.3 + AB.3.5.spec: when ≥1 TransferTemplate declares
    ``leg_rail_xor_groups``, the matview body carries:
    - a CTE ``xor_groups(template_name, xor_group_index,
      member_rail_name)`` populated by VALUES (one row per group
      member);
    - a LEFT JOIN against ``current_transactions`` per
      ``(template_transfer, group_member)`` so 0-firing rows surface;
    - ``HAVING COUNT(tx.transfer_id) <> 1`` catches both missed
      (count=0) and overlap (count>=2) cases.
    Body shape assertion under spec_example's
    ``SettlementTimingCycle`` group [SettlementAuto, SettlementStandard].
    """
    from pathlib import Path
    from recon_gen.common.l2.loader import load_instance
    fx = Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"
    inst = load_instance(fx)
    sql = emit_schema(inst, prefix="xs")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW xs_xor_group_violation AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # Populated path — no empty-branch placeholder.
    assert "WHERE 1=0" not in body
    # CTE column-list shape on PG/SQLite.
    assert (
        "xor_groups(template_name, xor_group_index, member_rail_name) "
        "AS (\n  VALUES" in body
    )
    # spec_example's group members appear as VALUES rows.
    assert "'SettlementTimingCycle'" in body
    assert "'SettlementAuto'" in body
    assert "'SettlementStandard'" in body
    # HAVING gates on count != 1 (catches both 0 and ≥2).
    assert "HAVING COUNT(tx.transfer_id) <> 1" in body


def test_todays_exceptions_includes_xor_group_violation_branch() -> None:
    """AB.3.3 wiring: Today's Exceptions matview UNION ALLs a row
    category for xor_group_violation violations, with
    check_type='xor_group_violation' and magnitude=firing_count."""
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="xt",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW xt_todays_exceptions AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "'xor_group_violation'" in body
    assert "FROM xt_xor_group_violation" in body
    assert "template_name AS rail_name" in body
    assert "firing_count AS magnitude" in body


def test_transfer_parents_view_ab4_shape() -> None:
    """AB.4.3: the transfer_parents matview is a per-(child, parent)
    DISTINCT projection over current_transactions. AB.4.7's
    fan_in_disagreement reads this; surfacing the long-form pair
    set lets the downstream matview JOIN instead of re-running
    DISTINCT every refresh.

    Reads the existing ``transfer_parent_id`` top-level column (not
    JSON metadata — that was the AB.4.3 PLAN lock's outdated
    phrasing; Schema_v6 promoted it to a real column).
    """
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="tp",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW tp_transfer_parents AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None, (
        "transfer_parents matview missing from emit_schema output"
    )
    body = body_match.group(1)
    # Projection: long form (one row per (child, parent) pair).
    assert "tx.transfer_id AS child_transfer_id" in body
    assert "tx.transfer_parent_id AS parent_transfer_id" in body
    # DISTINCT collapses multi-leg duplicates.
    assert "SELECT DISTINCT" in body
    # Filters: NULL parents + Failed legs out.
    assert "tx.transfer_parent_id IS NOT NULL" in body
    assert "tx.status <> 'Failed'" in body
    # Two indexes for the dashboard hot paths.
    assert "idx_tp_tp_child" in sql
    assert "idx_tp_tp_parent" in sql


def test_chain_parent_disagreement_excludes_fan_in_templates() -> None:
    """AB.4.4 — chain_parent_disagreement matview filters fan_in
    template children out of its violation set. Otherwise every
    legitimate fan_in firing (N parents per child by design) would
    false-positive into the AB.2.3 violation surface. Empty filter
    when no fan_in declared (pre-AB.4 behavior); inline NOT IN
    clause when ≥1 fan_in template exists."""
    from pathlib import Path
    from recon_gen.common.l2.loader import load_instance
    # spec_example declares BatchedPayoutBatch as a fan_in child.
    fx = Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"
    inst = load_instance(fx)
    sql = emit_schema(inst, prefix="cpdf")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW cpdf_chain_parent_disagreement "
        r"AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # NOT IN filter inlines the fan_in template name.
    assert "AND tx.template_name NOT IN ('BatchedPayoutBatch')" in body


def test_chain_parent_disagreement_filter_empty_when_no_fan_in() -> None:
    """AB.4.4 backwards-compat: when no chain declares fan_in,
    the filter resolves to empty — pre-AB.4 fixtures match byte-
    for-byte with AB.2.3's original matview shape."""
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="cpdn",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW cpdn_chain_parent_disagreement "
        r"AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # No NOT IN clause in the empty branch.
    assert "NOT IN (" not in body
    # The WHERE chain ends cleanly with status filter; no dangling AND.
    assert "tx.status <> 'Failed'\nGROUP BY" in body


def test_fan_in_disagreement_view_empty_branch_when_no_fan_in_chain(
) -> None:
    """AB.4.7: when no chain declares ``fan_in=True``, the
    fan_in_disagreement matview is still emitted (every L1 invariant
    matview is unconditional) but its body short-circuits with
    ``WHERE 1=0`` so it produces zero rows AND parses cleanly on all
    3 dialects. Mirrors AB.3.3's empty-XOR-groups fallback."""
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="fid",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW fid_fan_in_disagreement AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None, (
        "fan_in_disagreement matview missing from emit_schema output"
    )
    body = body_match.group(1)
    assert "WHERE 1=0" in body
    assert "fan_in_chains" not in body


def test_fan_in_disagreement_view_populated_when_fan_in_chain_declared(
) -> None:
    """AB.4.7 + AB.4.5.spec: when ≥1 chain declares ``fan_in=True``,
    the matview body carries:
    - a CTE ``fan_in_chains(chain_parent_name, child_template_name,
      expected_parent_count)`` populated by VALUES (one row per
      fan_in (parent, child) pair);
    - a JOIN against AB.4.3's ``_transfer_parents`` matview for the
      DISTINCT-parent-count derivation;
    - a CASE expression discriminating ``disagreement_kind`` across
      ``'orphan'`` / ``'missing'`` / ``'extra'`` rows.
    Body shape assertion under spec_example's
    ``BatchPayoutTrigger → BatchedPayoutBatch`` fan-in chain.
    """
    from pathlib import Path
    from recon_gen.common.l2.loader import load_instance
    fx = Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"
    inst = load_instance(fx)
    sql = emit_schema(inst, prefix="fp")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW fp_fan_in_disagreement AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # Populated branch — no empty-branch placeholder.
    assert "WHERE 1=0" not in body
    # CTE column-list shape on PG/SQLite.
    assert (
        "fan_in_chains(chain_parent_name, child_template_name, "
        "expected_parent_count) AS (\n  VALUES" in body
    )
    # spec_example's fan_in chain rows appear in the CTE.
    assert "'BatchPayoutTrigger'" in body
    assert "'BatchedPayoutBatch'" in body
    # CASE discriminates the three disagreement kinds.
    assert "'orphan'" in body
    assert "'missing'" in body
    assert "'extra'" in body
    # Joins _transfer_parents (AB.4.3 dependency).
    assert "fp_transfer_parents tp" in body


def test_todays_exceptions_includes_fan_in_disagreement_branch() -> None:
    """AB.4.7 wiring: Today's Exceptions matview UNION ALLs a row
    category for fan_in_disagreement violations, with
    check_type='fan_in_disagreement' and magnitude=parent_count."""
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="fit",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW fit_todays_exceptions AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "'fan_in_disagreement'" in body
    assert "FROM fit_fan_in_disagreement" in body
    assert "child_template_name AS rail_name" in body
    assert "parent_count AS magnitude" in body


def test_multi_xor_violation_view_empty_branch_when_no_qualifying_chain(
) -> None:
    """AB.6.5: when no chain qualifies (≥2 non-fan_in children with a
    Rail parent), the multi_xor_violation matview body short-circuits
    with ``WHERE 1=0`` (mirrors AB.3.3 + AB.4.7 empty-branch shape).
    Empty L2 + an L2 with only singleton chains both hit the empty path.
    """
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="mxv",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW mxv_multi_xor_violation AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None, (
        "multi_xor_violation matview missing from emit_schema output"
    )
    body = body_match.group(1)
    assert "WHERE 1=0" in body
    assert "multi_xor_chains" not in body


def test_multi_xor_violation_view_populated_when_multi_xor_chain_declared(
) -> None:
    """AB.6.5 + AB.6.5.spec: when ≥1 chain declares ≥2 non-fan_in
    children, the matview body carries:
    - a CTE ``multi_xor_chains(chain_parent_name, child_name)`` populated
      by VALUES (one row per (parent, non-fan_in child) pair);
    - a UNION-of-template_name + rail_name parent_firings CTE so the
      parent can be either a Rail or a Template;
    - a LEFT JOIN against _current_transactions for children matching
      the declared XOR siblings;
    - a CASE expression discriminating ``'missed'`` (count=0) vs
      ``'overlap'`` (count>=2).
    Body-shape assertion under spec_example's
    ``BulkAccrualSettlement → [BulkAccrualSettleACH, BulkAccrualSettleWire]``
    chain (AB.6.5.spec).
    """
    from pathlib import Path
    from recon_gen.common.l2.loader import load_instance
    fx = Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"
    inst = load_instance(fx)
    sql = emit_schema(inst, prefix="mxp")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW mxp_multi_xor_violation AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "WHERE 1=0" not in body
    assert (
        "multi_xor_chains(chain_parent_name, child_name) AS (\n  VALUES"
        in body
    )
    # spec_example's multi-XOR chain rows appear in the CTE — the
    # non-fan_in children only (fan_in children are filtered out per
    # AB.5 coupling).
    assert "'BulkAccrualSettlement'" in body
    assert "'BulkAccrualSettleACH'" in body
    assert "'BulkAccrualSettleWire'" in body
    # CASE discriminates the two disagreement kinds.
    assert "'missed'" in body
    assert "'overlap'" in body
    # Parent firings UNION both template + rail matches.
    assert "tx.template_name IN (SELECT name FROM parent_names)" in body
    assert "tx.rail_name IN (SELECT name FROM parent_names)" in body


def test_todays_exceptions_includes_multi_xor_violation_branch() -> None:
    """AB.6.5 wiring: Today's Exceptions matview UNION ALLs a row
    category for multi_xor_violation violations, with
    check_type='multi_xor_violation' and magnitude=child_count."""
    sql = emit_schema(
        L2Instance(
            accounts=(),
            account_templates=(),
            rails=(),
            transfer_templates=(),
            chains=(),
            limit_schedules=(),
        ),
        prefix="mxt",
    )
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW mxt_todays_exceptions AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "'multi_xor_violation'" in body
    assert "FROM mxt_multi_xor_violation" in body
    assert "parent_rail_or_template_name AS rail_name" in body
    assert "child_count AS magnitude" in body


def test_multi_xor_violation_skips_per_child_fan_in_entries() -> None:
    """AB.6.5 + AB.5 coupling: chains where ≥2 children are declared
    but one is fan_in get filtered to the non-fan_in subset. If the
    non-fan_in subset has <2 entries, the chain doesn't qualify for
    multi_xor_violation (its cardinality is _fan_in_disagreement's job).

    Sasquatch's MerchantSettlementCycle chain has 4 children (3 XOR
    payout vehicles + 1 fan_in MerchantWeeklyPayoutBatch). The 3 XOR
    children DO qualify for multi_xor_violation; the fan_in entry is
    excluded from the CTE. Inverse: a chain with [ChildA(fan_in),
    ChildB] has only 1 non-fan_in → doesn't qualify (the singleton
    1:1 contract belongs to AB.2.3).
    """
    from pathlib import Path
    from recon_gen.common.l2.loader import load_instance
    fx = Path(__file__).resolve().parent.parent / "l2" / "sasquatch_pr.yaml"
    inst = load_instance(fx)
    sql = emit_schema(inst, prefix="sqmx")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW sqmx_multi_xor_violation AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # MerchantSettlementCycle's 3 XOR children qualify (AB.6.6.sasq
    # mixed-cardinality fold). MerchantWeeklyPayoutBatch is fan_in →
    # excluded.
    assert "'MerchantPayoutACH'" in body
    assert "'MerchantPayoutWire'" in body
    assert "'MerchantPayoutCheck'" in body
    assert "'MerchantWeeklyPayoutBatch'" not in body


def test_refresh_matviews_sql_emits_one_per_view() -> None:
    """All 19 L1+inv matviews each get a REFRESH command + an
    ANALYZE follow-up: 2 current_* + 2 computed_* + 10 L1 invariants
    (drift + ledger_drift + overdraft + expected_eod_balance_breach +
    limit_breach + stuck_pending + stuck_unbundled +
    chain_parent_disagreement [AB.2.3] + xor_group_violation
    [AB.3.3] + fan_in_disagreement [AB.4.7]) + 1 derived
    (transfer_parents [AB.4.3]) + 2 dashboard-shape
    (daily_statement_summary + todays_exceptions) + 2 Investigation
    matviews (inv_pair_rolling_anomalies + inv_money_trail_edges,
    added in N.3.b) + AB.6.5's multi_xor_violation = 20 matviews ×
    2 statements each = 40 total."""
    sql = refresh_matviews_sql(_baseline_instance(), prefix="re")
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    refreshes = [s for s in statements if s.startswith("REFRESH ")]
    analyzes = [s for s in statements if s.startswith("ANALYZE ")]
    assert len(refreshes) == 20
    assert len(analyzes) == 20
    # Every REFRESHed matview gets a matching ANALYZE.
    refresh_names = {s.removeprefix("REFRESH MATERIALIZED VIEW ") for s in refreshes}
    analyze_names = {s.removeprefix("ANALYZE ") for s in analyzes}
    assert refresh_names == analyze_names


def test_refresh_matviews_sql_dependency_order() -> None:
    """current_* must REFRESH before computed_*; computed_* before
    L1 invariants. PostgreSQL refuses to refresh a downstream matview
    before its upstream is fresh, so order is load-bearing."""
    sql = refresh_matviews_sql(_baseline_instance(), prefix="re")

    def _idx(name: str) -> int:
        return sql.index(f"REFRESH MATERIALIZED VIEW re_{name};")

    # current_* are leaves (read base tables only).
    assert _idx("current_transactions") < _idx("computed_subledger_balance")
    assert _idx("current_daily_balances") < _idx("computed_subledger_balance")
    # computed_* helpers feed drift / ledger_drift.
    assert _idx("computed_subledger_balance") < _idx("drift")
    assert _idx("computed_ledger_balance") < _idx("ledger_drift")
    # All L1 invariants come after current_*.
    for inv in (
        "drift", "ledger_drift", "overdraft",
        "expected_eod_balance_breach", "limit_breach",
    ):
        assert _idx("current_transactions") < _idx(inv)
    # Dashboard-shape matviews (M.1a.9) refresh AFTER L1 invariants
    # because todays_exceptions UNIONs them.
    assert _idx("limit_breach") < _idx("todays_exceptions")
    assert _idx("current_daily_balances") < _idx("daily_statement_summary")


def test_refresh_matviews_sql_uses_instance_prefix() -> None:
    """Prefix is per-deployment (Z.C — cfg.db_table_prefix); switching prefixes switches table names."""
    inst = L2Instance(
        accounts=(), account_templates=(),
        rails=(), transfer_templates=(), chains=(), limit_schedules=(),
    )
    sql_a = refresh_matviews_sql(inst, prefix="alpha")
    sql_b = refresh_matviews_sql(inst, prefix="beta")
    assert "alpha_current_transactions" in sql_a
    assert "alpha_" in sql_a and "beta_" not in sql_a
    assert "beta_current_transactions" in sql_b
    assert "beta_" in sql_b and "alpha_" not in sql_b


# =====================================================================
# N.3.c — Investigation matview emitter tests.
# =====================================================================
#
# Both matviews are L2-instance-prefixed lifts of the K.4.4 + K.4.5
# bodies from the legacy ``schema.sql``. The emitter substitutes the
# matview names + every ``transactions`` reference with the prefixed
# version. Tests below assert the substitutions are total (no flat
# refs leak) and the body shape is preserved.

_INV_VIEW_NAMES = (
    "inv_pair_rolling_anomalies",
    "inv_money_trail_edges",
)


@pytest.mark.parametrize("view", _INV_VIEW_NAMES)
def test_inv_matview_emitted_per_instance(view: str) -> None:
    """Both Investigation matviews appear in emit_schema output, prefixed
    by the L2 instance name."""
    sql = emit_schema(_instance("v6"), prefix="v6")
    assert f"CREATE MATERIALIZED VIEW v6_{view} AS" in sql
    assert f"DROP MATERIALIZED VIEW IF EXISTS v6_{view}" in sql


@pytest.mark.parametrize("view", _INV_VIEW_NAMES)
def test_inv_matview_drops_before_creates(view: str) -> None:
    """Drop precedes create — idempotency holds for the inv matview block."""
    sql = emit_schema(_instance("v6"), prefix="v6")
    drop_idx = sql.index(f"DROP MATERIALIZED VIEW IF EXISTS v6_{view}")
    create_idx = sql.index(f"CREATE MATERIALIZED VIEW v6_{view}")
    assert drop_idx < create_idx


@pytest.mark.parametrize("view", _INV_VIEW_NAMES)
def test_inv_matview_drops_emit_before_base_table_create(view: str) -> None:
    """Inv matview drops sit at the top of the script, before the base
    CREATE TABLE — same dependency-ordering rule as L1 invariant views.
    Otherwise re-running ``apply schema`` against an existing instance
    would error because the matview depends on the base table.
    """
    sql = emit_schema(_instance("ord"), prefix="ord")
    first_create = sql.index("CREATE TABLE ord_transactions")
    drop_idx = sql.index(f"DROP MATERIALIZED VIEW IF EXISTS ord_{view}")
    assert drop_idx < first_create


def test_inv_pair_rolling_anomalies_uses_prefixed_transactions() -> None:
    """The pair-rolling matview body reads from the prefixed base table
    everywhere — no flat ``transactions`` refs leak through the
    substitution."""
    sql = emit_schema(_instance("ipra"), prefix="ipra")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW ipra_inv_pair_rolling_anomalies AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # Positive: prefixed refs present (the recipient + sender CTEs).
    assert "FROM ipra_transactions recipient" in body
    assert "JOIN ipra_transactions sender" in body
    # Negative: NO flat ``transactions`` ref survived.
    assert "FROM transactions" not in body
    assert "JOIN transactions" not in body


def test_inv_money_trail_edges_uses_prefixed_transactions() -> None:
    """The money-trail matview body — distinct_transfers CTE +
    chain CTE + final SELECT — all prefix-substituted. Also verifies
    the v6 column rename: parent linkage is ``transfer_parent_id``,
    not v5's ``parent_transfer_id``."""
    sql = emit_schema(_instance("imte"), prefix="imte")
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW imte_inv_money_trail_edges AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # Positive: prefixed refs present.
    assert "FROM imte_transactions" in body  # distinct_transfers CTE
    assert "JOIN imte_transactions tgt" in body
    assert "JOIN imte_transactions src" in body
    # v6 column name (legacy was parent_transfer_id).
    assert "transfer_parent_id" in body
    # Negative: no flat refs.
    assert "FROM transactions" not in body
    assert "JOIN transactions" not in body
    # Negative: no SQL ref to the legacy v5 column. Strip ``--`` comment
    # lines first — they're allowed to mention the v6 rename.
    sql_lines = [
        ln for ln in body.splitlines() if not ln.lstrip().startswith("--")
    ]
    sql_only = "\n".join(sql_lines)
    assert "parent_transfer_id" not in sql_only


def test_inv_matviews_independent_of_each_other() -> None:
    """Neither inv matview references the other — safe to refresh in
    any relative order. (L1 invariants have inter-view dependencies;
    inv matviews don't.)"""
    sql = emit_schema(_instance("ind"), prefix="ind")
    pair_match = re.search(
        r"CREATE MATERIALIZED VIEW ind_inv_pair_rolling_anomalies AS(.*?);",
        sql,
        re.DOTALL,
    )
    trail_match = re.search(
        r"CREATE MATERIALIZED VIEW ind_inv_money_trail_edges AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert pair_match is not None
    assert trail_match is not None
    assert "ind_inv_money_trail_edges" not in pair_match.group(1)
    assert "ind_inv_pair_rolling_anomalies" not in trail_match.group(1)


def test_inv_matviews_isolated_per_instance() -> None:
    """Two instances emit non-overlapping inv matview names — the
    storage-isolation contract holds for inv views same as L1 views."""
    sql_a = emit_schema(_instance("alpha"), prefix="alpha")
    sql_b = emit_schema(_instance("beta"), prefix="beta")
    assert "alpha_inv_pair_rolling_anomalies" in sql_a
    assert "alpha_inv_money_trail_edges" in sql_a
    assert "beta_" not in sql_a
    assert "beta_inv_pair_rolling_anomalies" in sql_b
    assert "beta_inv_money_trail_edges" in sql_b
    assert "alpha_" not in sql_b


# -- Dialect-aware drops (P.3.d.1) -------------------------------------------
#
# emit_schema still guards Postgres-only at the top while the schema +
# matview templates port to dialect-aware in P.3.d.2-4. The drop-block
# helpers (_emit_l1_invariant_drops / _emit_inv_matview_drops) are
# already dialect-aware though — exercise both branches directly so
# regressions in the Oracle branch surface before the guard comes off.


def test_l1_invariant_drops_postgres_native_form() -> None:
    """Postgres branch keeps the legacy ``DROP MATERIALIZED VIEW IF EXISTS``
    one-liner per matview — that's the byte-shape every existing test +
    every existing ETL depends on."""
    from recon_gen.common.sql import Dialect
    from recon_gen.common.l2.schema import (
        _L1_INVARIANT_DROP_NAMES,
        _emit_l1_invariant_drops,
    )

    out = _emit_l1_invariant_drops("pg", Dialect.POSTGRES)
    for name in _L1_INVARIANT_DROP_NAMES:
        assert f"DROP MATERIALIZED VIEW IF EXISTS pg_{name};" in out
    assert "BEGIN EXECUTE IMMEDIATE" not in out  # no PL/SQL on PG


def test_l1_invariant_drops_oracle_plsql_block() -> None:
    """Oracle branch wraps each DROP in a PL/SQL block that swallows
    ORA-12003 (matview not found) so the script stays idempotent."""
    from recon_gen.common.sql import Dialect
    from recon_gen.common.l2.schema import (
        _L1_INVARIANT_DROP_NAMES,
        _emit_l1_invariant_drops,
    )

    out = _emit_l1_invariant_drops("orcl", Dialect.ORACLE)
    # Oracle has no IF EXISTS clause — assert against stripped-comment
    # form (the PG-only header retains an "IF EXISTS" mention inside
    # an explanatory --comment line).
    assert "IF EXISTS" not in _strip_comments(out)
    for name in _L1_INVARIANT_DROP_NAMES:
        # The bare DROP statement is wrapped in EXECUTE IMMEDIATE — assert
        # the wrapped form for each matview name. Y.7-followup: each drop
        # now also chains a same-name DROP TABLE fallback to clean up a
        # half-dropped matview's orphan container table.
        assert f"DROP MATERIALIZED VIEW orcl_{name}" in out
        assert f"DROP TABLE orcl_{name} CASCADE CONSTRAINTS" in out
    # Every drop must use the PL/SQL exception-swallowing pattern — two
    # blocks per matview (the MV drop + the orphan-table fallback).
    block_count = out.count("BEGIN EXECUTE IMMEDIATE")
    assert block_count == 2 * len(_L1_INVARIANT_DROP_NAMES)
    assert "SQLCODE != -12003" in out  # ORA-12003 = matview not found
    assert "SQLCODE != -942" in out    # ORA-00942 = table not found
    assert "SQLCODE != -12083" in out  # ORA-12083 = must use DROP MATERIALIZED VIEW


def test_l1_invariant_drops_preserve_drop_order() -> None:
    """Drop order for the L1 invariant block is fixed: dashboard-shape
    matviews first, helpers last. Reordering would re-introduce the
    "dependent objects still exist" failure on re-runs."""
    from recon_gen.common.sql import Dialect
    from recon_gen.common.l2.schema import _emit_l1_invariant_drops

    out = _emit_l1_invariant_drops("ord", Dialect.POSTGRES)
    todays = out.index("ord_todays_exceptions")
    drift = out.index("ord_drift")
    computed = out.index("ord_computed_subledger_balance")
    assert todays < drift < computed


def test_inv_matview_drops_postgres_native_form() -> None:
    """Investigation matview drops use the native PG form on PG."""
    from recon_gen.common.sql import Dialect
    from recon_gen.common.l2.schema import (
        _INV_MATVIEW_DROP_NAMES,
        _emit_inv_matview_drops,
    )

    out = _emit_inv_matview_drops("pg", Dialect.POSTGRES)
    for name in _INV_MATVIEW_DROP_NAMES:
        assert f"DROP MATERIALIZED VIEW IF EXISTS pg_{name};" in out


def test_inv_matview_drops_oracle_plsql_block() -> None:
    """Investigation matview drops use the PL/SQL form on Oracle."""
    from recon_gen.common.sql import Dialect
    from recon_gen.common.l2.schema import (
        _INV_MATVIEW_DROP_NAMES,
        _emit_inv_matview_drops,
    )

    out = _emit_inv_matview_drops("orcl", Dialect.ORACLE)
    assert "IF EXISTS" not in _strip_comments(out)
    for name in _INV_MATVIEW_DROP_NAMES:
        assert f"DROP MATERIALIZED VIEW orcl_{name}" in out
        # Y.7-followup — same-name DROP TABLE fallback for orphan
        # container tables left by a killed CREATE MATERIALIZED VIEW.
        assert f"DROP TABLE orcl_{name} CASCADE CONSTRAINTS" in out
    # Two PL/SQL blocks per matview: the MV drop + the orphan-table fallback.
    assert out.count("BEGIN EXECUTE IMMEDIATE") == 2 * len(_INV_MATVIEW_DROP_NAMES)
