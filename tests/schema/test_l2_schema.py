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

from quicksight_gen.common.l2 import (
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
        instance=Identifier(prefix),
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
    sql = emit_schema(_instance("ksk"))
    assert "CREATE TABLE ksk_transactions" in sql
    assert "CREATE TABLE ksk_daily_balances" in sql
    assert "CREATE INDEX idx_ksk_transactions_account_posting" in sql
    assert "CREATE INDEX idx_ksk_daily_balances_business_day" in sql


def test_two_instances_emit_isolated_table_names() -> None:
    """Two L2 instances coexist in one DB by using distinct prefixes."""
    a = emit_schema(_instance("aaa"))
    b = emit_schema(_instance("bbb"))
    assert "aaa_transactions" in a and "aaa_transactions" not in b
    assert "bbb_transactions" in b and "bbb_transactions" not in a


# -- Idempotency -------------------------------------------------------------


def test_emits_drop_before_create() -> None:
    """Every CREATE has a DROP IF EXISTS for the same object before it."""
    sql = emit_schema(_instance("idem"))
    drop_idx = sql.index("DROP TABLE IF EXISTS idem_transactions")
    create_idx = sql.index("CREATE TABLE idem_transactions")
    assert drop_idx < create_idx, "DROP must precede CREATE"

    drop_db_idx = sql.index("DROP TABLE IF EXISTS idem_daily_balances")
    create_db_idx = sql.index("CREATE TABLE idem_daily_balances")
    assert drop_db_idx < create_db_idx


def test_drops_daily_balances_before_transactions_for_fk_safety() -> None:
    """Drop daily_balances first so any future FKs from it to transactions
    don't block the drop. Order matters in idempotent DDL."""
    sql = emit_schema(_instance("ord"))
    db_drop = sql.index("DROP TABLE IF EXISTS ord_daily_balances")
    tx_drop = sql.index("DROP TABLE IF EXISTS ord_transactions ")
    assert db_drop < tx_drop


# -- v6 column shape per L1 SPEC ---------------------------------------------


def test_emits_entry_column_on_both_tables() -> None:
    """L1 F's Entry primitive — BIGSERIAL on both transactions + daily_balances."""
    sql = emit_schema(_instance("ent"))
    # Both tables have entry BIGSERIAL
    assert "entry                BIGSERIAL      NOT NULL" in sql
    assert "entry                  BIGSERIAL      NOT NULL" in sql


def test_transactions_includes_amount_money_and_direction() -> None:
    """Per L1 SPEC: Amount = (Money, Direction); both columns present."""
    sql = emit_schema(_instance("amt"))
    assert "amount_money         DECIMAL(20,2)  NOT NULL" in sql
    assert "amount_direction     VARCHAR(20)    NOT NULL" in sql
    assert "amount_direction IN ('Debit', 'Credit')" in sql


def test_transactions_includes_amount_invariant_check() -> None:
    """L1 Amount INVARIANT: money agrees with direction.

    money ≥ 0 if direction = Credit; money ≤ 0 if direction = Debit.
    Encoded as a Postgres CHECK so the DB rejects rows that violate it.
    """
    sql = emit_schema(_instance("inv"))
    assert "amount_direction = 'Credit' AND amount_money >= 0" in sql
    assert "amount_direction = 'Debit'  AND amount_money <= 0" in sql


def test_transactions_includes_transfer_parent_id() -> None:
    """L1 SPEC: Transfer.Parent recursive chain (Phase L addition)."""
    sql = emit_schema(_instance("tp"))
    assert "transfer_parent_id   VARCHAR(100)" in sql


def test_transactions_includes_transfer_completion_and_origin() -> None:
    """L1 SPEC: Transfer.Completion + Transaction.Origin both denormalized."""
    sql = emit_schema(_instance("co"))
    assert "transfer_completion  TIMESTAMP" in sql
    assert "origin               VARCHAR(50)    NOT NULL" in sql
    # Origin is open enum — no CHECK so integrators can extend.
    assert "origin IN" not in sql


def test_transactions_status_is_open_enum() -> None:
    """L1 SPEC says Status ⊇ {Posted}. No closed CHECK on status."""
    sql = emit_schema(_instance("st"))
    assert "status               VARCHAR(50)    NOT NULL" in sql
    # No CHECK constraint on status (would close the enum).
    assert "status IN" not in sql


def test_transactions_rail_name_is_open_enum() -> None:
    """Z.B (2026-05-15): rail_name carries the per-leg type identifier
    (Rail.transfer_type and the legacy column collapsed away). The
    column stays open-set — integrators add new rails by extending the
    L2 instance, no CHECK constraint."""
    sql = emit_schema(_instance("tt"))
    assert "rail_name            VARCHAR(100)   NOT NULL" in sql
    # No CHECK on rail_name — keeps the column extensible.
    assert "rail_name IN" not in sql
    # Z.B: no transfer_type column at all (collapsed into rail_name).
    # Narrative SQL comments may still reference the old name historically;
    # the column declaration is what matters.
    assert "transfer_type        VARCHAR" not in sql
    assert "transfer_type INTEGER" not in sql
    assert "transfer_type IN" not in sql


def test_daily_balances_includes_expected_eod_and_limits() -> None:
    """L1 SPEC: ExpectedEODBalance + Limits map both denormalized onto the row."""
    sql = emit_schema(_instance("eb"))
    assert "expected_eod_balance   DECIMAL(20,2)" in sql
    # Limits is the Map[TransferType, Money] serialized as JSON, bounded
    # so the column behaves like a string on both dialects (CLOB
    # cannot be aggregated; bounded VARCHAR can).
    assert "limits                 VARCHAR(4000)" in sql


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
    sql = emit_schema(_instance("sg"))
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
    sql = emit_schema(_instance("bd"))
    assert "business_day_end > business_day_start" in sql


# -- Portability constraint --------------------------------------------------


def test_metadata_uses_text_with_is_json_check() -> None:
    """SPEC's portability constraint: bounded VARCHAR + IS JSON, not JSONB.

    Bounded so the column behaves like a string on both dialects —
    Oracle CLOB can't be aggregated, ordered, or compared with VARCHAR
    literals (ORA-00932). 4000 chars covers every JSON metadata
    document the L2 schema emits in practice.
    """
    sql = emit_schema(_instance("p"))
    assert "metadata             VARCHAR(4000)" in sql
    assert "metadata IS NULL OR metadata IS JSON" in sql
    # Limits column same pattern
    assert "limits                 VARCHAR(4000)" in sql
    assert "limits IS NULL OR limits IS JSON" in sql
    # No JSONB type used in any actual SQL statement (comments allowed).
    assert "JSONB" not in _strip_comments(sql).upper()


def test_no_gin_indexes_per_portability_constraint() -> None:
    """SPEC: no GIN indexes on JSON; B-tree only.

    Checks for the actual GIN-index syntax (``USING GIN``) rather than
    the bare substring 'GIN' — which would match ``ORIGIN`` (the
    Transaction.Origin column name) and produce false positives.
    """
    sql = emit_schema(_instance("g"))
    no_comments = _strip_comments(sql)
    assert "USING GIN" not in no_comments.upper()
    # B-tree is the default and only allowed; assert at least one B-tree
    # index actually got emitted to make the negative meaningful.
    assert "CREATE INDEX" in no_comments


# -- Primary keys -----------------------------------------------------------


def test_transactions_pk_includes_entry() -> None:
    """Per L1 F: physical row key is (id, entry); logical key is id."""
    sql = emit_schema(_instance("pk1"))
    assert "PRIMARY KEY (id, entry)" in sql


def test_daily_balances_pk_includes_entry() -> None:
    """Per L1 F: physical row key is (account_id, business_day_start, entry)."""
    sql = emit_schema(_instance("pk2"))
    assert "PRIMARY KEY (account_id, business_day_start, entry)" in sql


# -- Account denormalization (per Implementation Entities) ------------------


@pytest.mark.parametrize("col", [
    "account_id", "account_name", "account_role",
    "account_scope", "account_parent_role",
])
def test_transactions_denormalizes_account(col: str) -> None:
    """SPEC's StoredTransaction = Transaction + Transfer + Account fields."""
    sql = emit_schema(_instance("a"))
    # Both tables carry the account fields.
    assert f"  {col}" in sql or f"  {col} " in sql


@pytest.mark.parametrize("col", [
    "account_id", "account_name", "account_role",
    "account_scope", "account_parent_role",
])
def test_daily_balances_denormalizes_account(col: str) -> None:
    """SPEC's DailyBalance = StoredBalance + Account fields."""
    sql = emit_schema(_instance("a"))
    # Just verify the column name appears (we already assert on transactions
    # via the same parametrize; here we trust same-string-in-template).
    assert col in sql


# -- Current* views (M.1.5) --------------------------------------------------


def test_emits_current_transactions_view() -> None:
    """L1 CurrentTransaction theorem materialized as max-Entry-per-ID view."""
    sql = emit_schema(_instance("v"))
    assert "CREATE MATERIALIZED VIEW v_current_transactions AS" in sql
    # Per L1 CurrentTransaction set-comprehension definition: the view
    # selects rows whose entry equals the max entry for the same logical id.
    assert "WHERE tx.entry = (" in sql
    assert "SELECT MAX(entry) FROM v_transactions WHERE id = tx.id" in sql


def test_emits_current_daily_balances_view() -> None:
    """L1 CurrentStoredBalance theorem materialized as max-Entry-per-(account,day) view."""
    sql = emit_schema(_instance("v"))
    assert "CREATE MATERIALIZED VIEW v_current_daily_balances AS" in sql
    # Per L1 CurrentStoredBalance: max-Entry per (Account, BusinessDay).
    assert "WHERE sb.entry = (" in sql
    assert "WHERE account_id = sb.account_id" in sql
    assert "AND business_day_start = sb.business_day_start" in sql


def test_view_drops_precede_table_drops() -> None:
    """Views must drop before tables they depend on (Postgres dependency)."""
    sql = emit_schema(_instance("ord"))
    view_drop = sql.index("DROP MATERIALIZED VIEW IF EXISTS ord_current_transactions")
    table_drop = sql.index("DROP TABLE IF EXISTS ord_transactions ")
    assert view_drop < table_drop


def test_view_creates_after_table_creates() -> None:
    """Views must be created after the tables they reference."""
    sql = emit_schema(_instance("ord"))
    table_create = sql.index("CREATE TABLE ord_transactions")
    view_create = sql.index("CREATE MATERIALIZED VIEW ord_current_transactions")
    assert table_create < view_create


def test_views_use_l2_instance_prefix() -> None:
    """View names + their referenced base tables share the prefix."""
    sql = emit_schema(_instance("aaa"))
    assert "aaa_current_transactions" in sql
    assert "aaa_current_daily_balances" in sql
    # The view's body references the prefixed base tables, not bare names.
    assert "FROM aaa_transactions tx" in sql
    assert "FROM aaa_daily_balances sb" in sql


# -- v1.1 columns (M.1a.1 — SPEC catch-up) -----------------------------------


def test_transactions_includes_rail_name_not_null() -> None:
    """SPEC: every leg's L2 Rail name denormalized so BundleSelector by
    RailName resolves to a simple WHERE without a transfer→rail join."""
    sql = emit_schema(_instance("v11"))
    assert "rail_name            VARCHAR(100)   NOT NULL" in sql


def test_transactions_includes_template_name_nullable() -> None:
    """SPEC: TransferTemplate name; NULL when the leg posts standalone."""
    sql = emit_schema(_instance("v11"))
    # NULL allowed: just the column declaration, no NOT NULL.
    assert re.search(
        r"\btemplate_name\s+VARCHAR\(100\)(?!\s+NOT NULL)",
        sql,
    ), "template_name should be nullable"


def test_transactions_includes_bundle_id_nullable() -> None:
    """SPEC: L1 Transaction.BundleId — populated by AggregatingRail bundlers."""
    sql = emit_schema(_instance("v11"))
    assert re.search(
        r"\bbundle_id\s+VARCHAR\(100\)(?!\s+NOT NULL)",
        sql,
    ), "bundle_id should be nullable"


def test_transactions_includes_supersedes_open_enum() -> None:
    """SPEC: L1 Transaction.Supersedes — open enum, no CHECK so integrators
    may extend the v1 set (Inflight / BundleAssignment / TechnicalCorrection)."""
    sql = emit_schema(_instance("v11"))
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
    sql = emit_schema(_instance("v11"))
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
    sql = emit_schema(_instance("be"))
    assert "CREATE INDEX idx_be_transactions_bundler_eligibility" in sql
    assert "ON be_transactions (rail_name, status)" in sql
    assert "WHERE bundle_id IS NULL" in sql


def test_bundler_eligibility_index_drops_before_create() -> None:
    """The new bundler index participates in the same DROP-before-CREATE
    idempotency the other indexes follow."""
    sql = emit_schema(_instance("be"))
    drop_idx = sql.index("DROP INDEX IF EXISTS idx_be_transactions_bundler_eligibility")
    create_idx = sql.index("CREATE INDEX idx_be_transactions_bundler_eligibility")
    assert drop_idx < create_idx


# -- L1 invariant views (M.1a.7) --------------------------------------------


def _instance_with_limits(prefix: str) -> L2Instance:
    """L2 instance with one LimitSchedule entry — exercises the
    inline-CASE-branch lookup the limit_breach view uses."""
    from decimal import Decimal
    from quicksight_gen.common.l2 import LimitSchedule
    return L2Instance(
        instance=Identifier(prefix),
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
    sql = emit_schema(_instance("v6"))
    assert f"CREATE MATERIALIZED VIEW v6_{view}" in sql
    assert f"DROP MATERIALIZED VIEW IF EXISTS v6_{view}" in sql


@pytest.mark.parametrize("view", _L1_VIEW_NAMES)
def test_l1_invariant_view_drops_before_creates(view: str) -> None:
    """Drop precedes create — idempotency holds for the L1 invariant view block."""
    sql = emit_schema(_instance("v6"))
    drop_idx = sql.index(f"DROP MATERIALIZED VIEW IF EXISTS v6_{view}")
    create_idx = sql.index(f"CREATE MATERIALIZED VIEW v6_{view}")
    assert drop_idx < create_idx


def test_l1_invariant_views_drop_before_base_table_drops() -> None:
    """L1 views depend on the Current* views (which depend on the base
    tables). DROPing Current* before L1 views fails with "dependent
    objects still exist" on a re-run, so L1 view drops MUST emit
    before the base block's drops. Surfaced by M.2.6's first run
    against real Postgres."""
    sql = emit_schema(_instance("ord"))
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
    sql = emit_schema(_instance("top"))
    first_create = sql.index("CREATE TABLE top_transactions")
    for view in _L1_VIEW_NAMES:
        drop_idx = sql.index(f"DROP MATERIALIZED VIEW IF EXISTS top_{view}")
        assert drop_idx < first_create, (
            f"top_{view} drop must come before any CREATE TABLE"
        )


def test_drift_view_filters_to_leaf_accounts_with_nonzero_drift() -> None:
    """`<prefix>_drift` is a leaf-account view (account_parent_role IS NOT NULL)
    that returns only rows where stored ≠ computed."""
    sql = emit_schema(_instance("dr"))
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
    sql = emit_schema(_instance("ld"))
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
    sql = emit_schema(_instance("ov"))
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
    sql = emit_schema(_instance("eod"))
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW eod_expected_eod_balance_breach AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "expected_eod_balance IS NOT NULL" in body
    assert "money <> sb.expected_eod_balance" in body


def test_limit_breach_embeds_limit_schedule_caps_inline() -> None:
    """L2's LimitSchedules become CASE branches in the limit_breach view;
    JSON-path-portable across SQL targets. Branches reference `tx.`
    aliases since the view reads parent_role + rail_name from the
    transaction row directly (no JOIN to daily_balances needed —
    parent_role is denormalized on every v6 transaction)."""
    sql = emit_schema(_instance_with_limits("lb"))
    assert (
        "WHEN tx.account_parent_role = 'DDAControl' "
        "AND tx.rail_name = 'ach' THEN 12000"
    ) in sql


def test_limit_breach_view_with_no_limit_schedules_is_inert() -> None:
    """An L2 instance with no LimitSchedules emits a syntactically valid
    limit_breach view that surfaces no rows (cap is NULL → outer WHERE
    `cap IS NOT NULL` excludes everything)."""
    sql = emit_schema(_instance("nolim"))  # _instance has no limit_schedules
    assert "CREATE MATERIALIZED VIEW nolim_limit_breach" in sql
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW nolim_limit_breach AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # `NULL::numeric AS cap` appears in the inner SELECT — typed NULL
    # so `outbound_total > cap` doesn't fail with `numeric > text`
    # when no LimitSchedules are declared (M.4.4.6 fix).
    assert "NULL::numeric AS cap" in body
    # Outer WHERE filters `cap IS NOT NULL` so an inert NULL cap excludes
    # every row.
    assert "WHERE cap IS NOT NULL" in body


def test_limit_breach_view_does_not_join_daily_balances() -> None:
    """v6's denormalized account_parent_role on transactions means the
    view reads parent role directly from the transaction row, no JOIN
    to daily_balances. Catches the bug M.2.6's first run surfaced:
    a JOIN-based view fails when no daily_balance row exists for the
    breach business_day."""
    sql = emit_schema(_instance_with_limits("nojoin"))
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


def _instance_with_pending_age(prefix: str) -> L2Instance:
    """L2 instance with one Rail carrying a `max_pending_age` so the
    stuck_pending view's CASE-branch lookup has data to match against.
    Uses a SingleLegRail; the case-branch helper walks both kinds.
    """
    from datetime import timedelta
    from quicksight_gen.common.l2 import SingleLegRail
    return L2Instance(
        instance=Identifier(prefix),
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
    sql = emit_schema(_instance_with_pending_age("sp"))
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
    # CURRENT_TIMESTAMP-driven age computation lives in the inner SELECT.
    assert "EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - ct.posting))" in body


def test_stuck_pending_embeds_rail_max_pending_age_inline() -> None:
    """Each Rail with `max_pending_age` becomes one CASE branch keyed
    on `ct.rail_name`. Cap renders as integer seconds (timedelta
    → total_seconds())."""
    sql = emit_schema(_instance_with_pending_age("sp2"))
    # 24 hours = 86400 seconds.
    assert (
        "WHEN ct.rail_name = 'ach-credit' THEN 86400"
    ) in sql


def test_stuck_pending_view_with_no_aging_rails_is_inert() -> None:
    """An L2 instance whose Rails all leave `max_pending_age` unset
    emits a syntactically valid stuck_pending view that surfaces no
    rows (max_pending_age_seconds is NULL → outer WHERE excludes
    everything)."""
    sql = emit_schema(_instance("noage"))
    assert "CREATE MATERIALIZED VIEW noage_stuck_pending" in sql
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW noage_stuck_pending AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    # `NULL::bigint AS max_pending_age_seconds` from the helper's
    # no-rails branch (M.4.4.6 — typed NULL so the outer `age_seconds
    # > max_pending_age_seconds` doesn't fail with `numeric > text`).
    assert "NULL::bigint AS max_pending_age_seconds" in body
    # Outer WHERE filters NULL caps so an inert NULL excludes every row.
    assert "max_pending_age_seconds IS NOT NULL" in body


def test_stuck_pending_view_indexes_emit() -> None:
    """Dashboard hot-path indexes on rail_name / account_id / transfer_id
    — same shape as the other L1 invariant matviews."""
    sql = emit_schema(_instance_with_pending_age("idx"))
    assert "CREATE INDEX idx_idx_sp_rail ON idx_stuck_pending (rail_name);" in sql
    assert "CREATE INDEX idx_idx_sp_account ON idx_stuck_pending (account_id);" in sql
    assert "CREATE INDEX idx_idx_sp_transfer ON idx_stuck_pending (transfer_id);" in sql


def _instance_with_unbundled_age(prefix: str) -> L2Instance:
    """L2 instance with one Rail carrying a `max_unbundled_age` so the
    stuck_unbundled view's CASE-branch lookup has data to match against.
    Uses a SingleLegRail; the case-branch helper walks both kinds.
    """
    from datetime import timedelta
    from quicksight_gen.common.l2 import SingleLegRail
    return L2Instance(
        instance=Identifier(prefix),
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
    sql = emit_schema(_instance_with_unbundled_age("su"))
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
    assert "EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - ct.posting))" in body


def test_stuck_unbundled_embeds_rail_max_unbundled_age_inline() -> None:
    """Each Rail with `max_unbundled_age` becomes one CASE branch keyed
    on `ct.rail_name`. Cap renders as integer seconds (timedelta
    → total_seconds())."""
    sql = emit_schema(_instance_with_unbundled_age("su2"))
    # 1 day = 86400 seconds.
    assert (
        "WHEN ct.rail_name = 'ach-orig' THEN 86400"
    ) in sql


def test_stuck_unbundled_view_with_no_bundling_rails_is_inert() -> None:
    """An L2 instance whose Rails all leave `max_unbundled_age` unset
    emits a syntactically valid stuck_unbundled view that surfaces no
    rows."""
    sql = emit_schema(_instance("nobun"))
    assert "CREATE MATERIALIZED VIEW nobun_stuck_unbundled" in sql
    body_match = re.search(
        r"CREATE MATERIALIZED VIEW nobun_stuck_unbundled AS(.*?);",
        sql,
        re.DOTALL,
    )
    assert body_match is not None
    body = body_match.group(1)
    assert "NULL::bigint AS max_unbundled_age_seconds" in body  # M.4.4.6
    assert "max_unbundled_age_seconds IS NOT NULL" in body


def test_stuck_unbundled_view_indexes_emit() -> None:
    """Same hot-path indexes (rail_name / account_id / transfer_id) as
    stuck_pending — the M.2b.11 Unbundled Aging sheet uses the same
    filter dropdowns."""
    sql = emit_schema(_instance_with_unbundled_age("ix"))
    assert "CREATE INDEX idx_ix_su_rail ON ix_stuck_unbundled (rail_name);" in sql
    assert "CREATE INDEX idx_ix_su_account ON ix_stuck_unbundled (account_id);" in sql
    assert "CREATE INDEX idx_ix_su_transfer ON ix_stuck_unbundled (transfer_id);" in sql


def test_computed_subledger_balance_uses_current_transactions_view() -> None:
    """The helper view reads from Current* (technical-error supersession
    transparent) rather than the raw transactions base table."""
    sql = emit_schema(_instance("h"))
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
    sql = emit_schema(_instance("clb"))
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
        instance=Identifier("re"),
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def test_refresh_matviews_sql_emits_one_per_view() -> None:
    """All 15 L1+inv matviews each get a REFRESH command + an
    ANALYZE follow-up: 2 current_* + 2 computed_* + 7 L1 invariants
    (drift + ledger_drift + overdraft + expected_eod_balance_breach +
    limit_breach + stuck_pending + stuck_unbundled) + 2 dashboard-shape
    (daily_statement_summary + todays_exceptions) + 2 Investigation
    matviews (inv_pair_rolling_anomalies + inv_money_trail_edges,
    added in N.3.b) = 15 matviews × 2 statements each = 30 total."""
    sql = refresh_matviews_sql(_baseline_instance())
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    refreshes = [s for s in statements if s.startswith("REFRESH ")]
    analyzes = [s for s in statements if s.startswith("ANALYZE ")]
    assert len(refreshes) == 15
    assert len(analyzes) == 15
    # Every REFRESHed matview gets a matching ANALYZE.
    refresh_names = {s.removeprefix("REFRESH MATERIALIZED VIEW ") for s in refreshes}
    analyze_names = {s.removeprefix("ANALYZE ") for s in analyzes}
    assert refresh_names == analyze_names


def test_refresh_matviews_sql_dependency_order() -> None:
    """current_* must REFRESH before computed_*; computed_* before
    L1 invariants. PostgreSQL refuses to refresh a downstream matview
    before its upstream is fresh, so order is load-bearing."""
    sql = refresh_matviews_sql(_baseline_instance())

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
    """Prefix is per-L2-instance; switching instances switches prefixes."""
    inst_a = L2Instance(
        instance=Identifier("alpha"), accounts=(), account_templates=(),
        rails=(), transfer_templates=(), chains=(), limit_schedules=(),
    )
    inst_b = L2Instance(
        instance=Identifier("beta"), accounts=(), account_templates=(),
        rails=(), transfer_templates=(), chains=(), limit_schedules=(),
    )
    sql_a = refresh_matviews_sql(inst_a)
    sql_b = refresh_matviews_sql(inst_b)
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
    sql = emit_schema(_instance("v6"))
    assert f"CREATE MATERIALIZED VIEW v6_{view} AS" in sql
    assert f"DROP MATERIALIZED VIEW IF EXISTS v6_{view}" in sql


@pytest.mark.parametrize("view", _INV_VIEW_NAMES)
def test_inv_matview_drops_before_creates(view: str) -> None:
    """Drop precedes create — idempotency holds for the inv matview block."""
    sql = emit_schema(_instance("v6"))
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
    sql = emit_schema(_instance("ord"))
    first_create = sql.index("CREATE TABLE ord_transactions")
    drop_idx = sql.index(f"DROP MATERIALIZED VIEW IF EXISTS ord_{view}")
    assert drop_idx < first_create


def test_inv_pair_rolling_anomalies_uses_prefixed_transactions() -> None:
    """The pair-rolling matview body reads from the prefixed base table
    everywhere — no flat ``transactions`` refs leak through the
    substitution."""
    sql = emit_schema(_instance("ipra"))
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
    sql = emit_schema(_instance("imte"))
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
    sql = emit_schema(_instance("ind"))
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
    sql_a = emit_schema(_instance("alpha"))
    sql_b = emit_schema(_instance("beta"))
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
    from quicksight_gen.common.sql import Dialect
    from quicksight_gen.common.l2.schema import (
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
    from quicksight_gen.common.sql import Dialect
    from quicksight_gen.common.l2.schema import (
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
    from quicksight_gen.common.sql import Dialect
    from quicksight_gen.common.l2.schema import _emit_l1_invariant_drops

    out = _emit_l1_invariant_drops("ord", Dialect.POSTGRES)
    todays = out.index("ord_todays_exceptions")
    drift = out.index("ord_drift")
    computed = out.index("ord_computed_subledger_balance")
    assert todays < drift < computed


def test_inv_matview_drops_postgres_native_form() -> None:
    """Investigation matview drops use the native PG form on PG."""
    from quicksight_gen.common.sql import Dialect
    from quicksight_gen.common.l2.schema import (
        _INV_MATVIEW_DROP_NAMES,
        _emit_inv_matview_drops,
    )

    out = _emit_inv_matview_drops("pg", Dialect.POSTGRES)
    for name in _INV_MATVIEW_DROP_NAMES:
        assert f"DROP MATERIALIZED VIEW IF EXISTS pg_{name};" in out


def test_inv_matview_drops_oracle_plsql_block() -> None:
    """Investigation matview drops use the PL/SQL form on Oracle."""
    from quicksight_gen.common.sql import Dialect
    from quicksight_gen.common.l2.schema import (
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
