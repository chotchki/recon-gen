"""Prefix-aware SQL DDL emission for an ``L2Instance`` (M.1.4 + M.1.5).

Emits one idempotent (drop-then-create) DDL script per L2 instance,
prefixed per the SPEC's storage-isolation rule (F10):

  ``<prefix>transactions``           — base table; L1 ``Transaction``
                                       denormalized with Account +
                                       Transfer fields per the
                                       Implementation Entities section.
  ``<prefix>daily_balances``         — base table; L1 ``StoredBalance``
                                       denormalized with Account fields.
  ``<prefix>current_transactions``   — view; the L1 ``CurrentTransaction``
                                       theorem materialized as max-Entry-
                                       per-ID over the base table.
  ``<prefix>current_daily_balances`` — view; the L1 ``CurrentStoredBalance``
                                       theorem materialized as max-Entry-
                                       per-(account, business_day) over
                                       the base table.

Plus B-tree indexes for the dashboard's hot-path queries on the bases.

The dashboard SQL targets the ``current_*`` views, never the bases —
that way Entry-supersession (technical-error correction per the F1
principle) is transparent to dashboard consumers.

What is NOT emitted as SQL tables (per the M.0 spike's experience):
- L2's account topology (Roles, AccountTemplates, parent_role chains) —
  the relevant fields denormalize onto the transactions / daily_balances
  rows; no separate dim table needed for v1.
- L2's Limits — projected into ``daily_balances.metadata.limits`` (a
  nested JSON map keyed by Rail name) by integrator ETL; no separate
  limits table. AV (2026-05-23) renamed the column from ``limits`` to
  ``metadata`` and demoted the per-rail caps to a nested key so the
  column mirrors ``transactions.metadata`` and has room for siblings
  (scenario_id per AV.5, future per-day tags).
- L2's Chains, TransferTemplates — read by dashboard SQL at view-build
  time (the SQL string knows which TransferTypes can chain into which
  via L2 lookups), not materialized as tables.

The "minimum SQL surface" stance follows from the spike: M.2 (porting
AR CMS) will surface what L2 derived tables are actually needed beyond
the base layer. Add them then.
"""

from __future__ import annotations

from recon_gen.common.sql import (
    Dialect,
    analyze_table,
    bigint_type,
    cast,
    concat_agg,
    date_minus_days,
    date_trunc_day,
    drop_index_if_exists,
    drop_matview_if_exists,
    drop_table_if_exists,
    drop_view_if_exists,
    epoch_seconds_between,
    json_check,
    lob_substr,
    matview_create_keyword,
    matview_options,
    order_by_day_expr,
    range_interval_days,
    refresh_matview,
    serial_type,
    json_text_type,
    text_type,
    timestamp_type,
    to_date,
    typed_null,
    varchar_type,
    with_recursive,
)

from .config_table import (
    kv_as_of_as_timestamp_sql,
)
from .primitives import L2Instance


def emit_schema(
    instance: L2Instance, *, prefix: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Emit the full DDL script for an L2 instance's prefixed L1 schema.

    Three layers, all per L2 instance prefix:

    1. **Base tables** — ``<prefix>_transactions`` + ``<prefix>_daily_balances``,
       v6 column shape (entry BIGSERIAL, amount_money + amount_direction,
       transfer_parent_id, rail_name, template_name, bundle_id, supersedes,
       …).
    2. **Current\\* views** — ``<prefix>_current_transactions`` +
       ``<prefix>_current_daily_balances``, materializing L1's
       max-Entry-per-logical-key theorems so dashboard SQL is transparent
       to technical-error supersession.
    3. **L1 invariant views (M.1a.7)** — ``<prefix>_drift`` /
       ``<prefix>_ledger_drift`` / ``<prefix>_overdraft`` /
       ``<prefix>_expected_eod_balance_breach`` / ``<prefix>_limit_breach``
       (plus 2 helpers: ``<prefix>_computed_subledger_balance`` +
       ``<prefix>_computed_ledger_balance``). Each materializes one of
       the SPEC's L1 SHOULD-constraints as a queryable exception
       surface; rows in these views are the constraint violations.
       Caps for ``<prefix>_limit_breach`` are embedded inline from
       ``instance.limit_schedules`` at view-emit time (CASE branches
       per declared (parent_role, rail) pair) so the view DDL
       stays JSON-path-portable.

    Idempotent: every CREATE is preceded by a DROP IF EXISTS so
    re-running the same ``apply schema`` clears stale state. The
    returned string can be fed straight to ``psql`` or
    ``psycopg2.cursor.execute(sql)``.

    ``dialect`` selects the SQL flavor. P.3.d unblocked Oracle by
    threading dialect helpers through every template; both branches
    are now first-class. New dialects would need a new ``Dialect``
    enum value plus per-helper Oracle/Postgres-style branches in
    ``common.sql.dialect``.

    Z.C — ``prefix`` is the cfg.db_table_prefix (formerly read off
    the dropped ``L2Instance.instance`` field).
    """
    p = prefix
    # L1 invariant view DROPs MUST run before base DROPs — the L1 views
    # depend on the Current* views (which depend on the base tables),
    # so dropping current_* first would error with "dependent objects
    # still exist" on a re-run. Emit L1 drops at the top of the script,
    # then the base block (which drops Current* + tables + creates
    # everything), then the L1 view CREATE statements.
    #
    # Investigation matview DROPs (N.3.b) sit alongside the L1 drops at
    # the top — they read from the base ``{p}_transactions`` table, so
    # the same dependency-ordering rule applies.
    l1_drops = _emit_l1_invariant_drops(p, dialect)
    inv_drops = _emit_inv_matview_drops(p, dialect)
    # Phase AW: <prefix>_config_kv holds cfg + L2 yaml + as_of. BC.12
    # renamed from the pre-AW ``<prefix>_config`` 3-column table to the
    # flattened kv shape so matviews can JOIN typed projection views
    # (relational, Oracle-matview-safe) instead of JSON_TABLE-ing a CLOB
    # (ORA-32368). Drop the typed views FIRST (matviews depend on them);
    # then drop the kv table; then base block runs; then config_kv
    # CREATE; then typed views CREATE; then matviews.
    from recon_gen.common.l2.config_table import (
        emit_config_table_ddl,
        emit_config_table_drop,
    )
    typed_view_drops = _emit_typed_config_view_drops(p, dialect)
    config_drop = emit_config_table_drop(p, dialect)
    config_create = emit_config_table_ddl(p, dialect)
    typed_view_creates = _emit_typed_config_view_creates(p, dialect)
    base = _emit_base_schema(p, dialect, instance)
    invariants = _emit_l1_invariant_views(instance, prefix=p, dialect=dialect)
    inv_views = _emit_inv_views(instance, prefix=p, dialect=dialect)
    return (
        l1_drops + "\n" + inv_drops + "\n"
        + typed_view_drops + "\n"
        + config_drop + "\n" + base
        + "\n\n" + config_create + "\n\n"
        + typed_view_creates + "\n\n"
        + invariants + "\n\n" + inv_views
    )




def emit_schema_drop_sql(
    instance: L2Instance, *, prefix: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Emit DROP statements for every per-prefix object ``emit_schema`` creates.

    The teardown counterpart of ``emit_schema``. Composes the same
    private drop helpers that prelude every CREATE in the full schema
    output, plus the base-table and base-index drops.

    Order matters: matviews → views → tables, with Inv matviews and L1
    invariant matviews first (they depend on the Current* matviews
    which depend on the base tables). Indexes get dropped before the
    tables they index.

    Returns one SQL string suitable for piping to ``psql`` or
    splitting + executing per-statement. Idempotent — every DROP is
    ``IF EXISTS`` (or a swallow-already-gone PL/SQL block on Oracle).
    Use ``schema clean -o FILE`` for the CLI surface.

    Z.C — ``prefix`` is the cfg.db_table_prefix.
    """
    p = prefix
    l1_drops = _emit_l1_invariant_drops(p, dialect)
    inv_drops = _emit_inv_matview_drops(p, dialect)
    # BC.12: typed projection views depend on <prefix>_config_kv +
    # are depended on by the L1 invariant matviews. Drop AFTER the L1
    # matview drops (above) so the view exists when those drops run
    # against it (drop matview doesn't actually need its source, but
    # the dependency order keeps the model coherent), but BEFORE the
    # config_kv table drop.
    typed_view_drops = _emit_typed_config_view_drops(p, dialect)
    # Base layer: Current* matviews → indexes → base tables + config.
    from recon_gen.common.l2.config_table import emit_config_table_drop
    pieces = [
        drop_matview_if_exists(f"{p}_current_daily_balances", dialect),
        drop_matview_if_exists(f"{p}_current_transactions", dialect),
    ]
    for _, name_template in _BASE_INDEX_DROPS:
        pieces.append(drop_index_if_exists(name_template.format(p=p), dialect))
    pieces.extend([
        drop_table_if_exists(f"{p}_daily_balances", dialect),
        drop_table_if_exists(f"{p}_transactions", dialect),
        # BC.12 (was Phase AW): <prefix>_config_kv. Drop AFTER typed
        # views (above) to respect the view-on-table dependency.
        emit_config_table_drop(p, dialect),
    ])
    base_drops = "\n".join(pieces)
    header = (
        f"-- =====================================================================\n"
        f"-- L2 instance: {p} — full schema teardown\n"
        f"-- Generated by recon_gen.common.l2.schema.emit_schema_drop_sql\n"
        f"-- Drops every per-prefix object emit_schema creates, in dependency\n"
        f"-- order. Re-runnable: every DROP is IF EXISTS / swallow-on-missing.\n"
        f"-- =====================================================================\n"
    )
    return (
        header + "\n"
        + l1_drops + "\n"
        + inv_drops + "\n"
        + "-- BC.12 typed projection views (depend on <prefix>_config_kv).\n"
        + typed_view_drops + "\n"
        + "-- Base layer: Current* matviews, indexes, base tables.\n"
        + base_drops + "\n"
    )


# X.4.g.6 — Base-table column lists for the deploy pipeline's step 2
# pull. Excludes ``entry`` (auto-generated identity column on every
# dialect; the pull lets the destination assign its own). Order is
# the canonical order the pipeline uses for both SELECT (against the
# operator's etl_datasource) and INSERT (into the demo DB), so column
# positions stay aligned across dialects.
#
# Source of truth: the CREATE TABLE blocks in ``_SCHEMA_TEMPLATE``
# below — when those change, this constant must change too. The
# `tests/unit/test_deploy_pipeline.py` snapshot test guards drift
# by introspecting a fresh sqlite-applied schema and diffing.
BASE_TRANSACTIONS_COLUMNS: tuple[str, ...] = (
    "id", "account_id", "account_name", "account_role",
    "account_scope", "account_parent_role", "amount_money",
    "amount_direction", "status", "posting", "transfer_id",
    "transfer_completion", "transfer_parent_id",
    "rail_name", "template_name", "bundle_id", "supersedes",
    "origin", "metadata",
)

BASE_DAILY_BALANCES_COLUMNS: tuple[str, ...] = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money", "metadata", "supersedes",
)


def wipe_demo_data_sql(
    instance: L2Instance, *, prefix: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Emit DELETE statements that empty the per-prefix base tables.

    X.4.g.5 — step 2 of the deploy pipeline. After step 1's etl_hook
    succeeds, the demo DB's `<prefix>_transactions` +
    `<prefix>_daily_balances` are wiped so step 2's pull (when an
    etl_datasource is configured) and step 3's generator both write
    into clean state. Step 4's matview refresh then re-derives every
    Current* / L1 invariant / Inv matview from the new base data.

    Schema is preserved — this is row-level wipe, not DROP. The
    operator's dataset / dashboard ARNs stay intact, so their
    bookmarked URLs still resolve after the deploy.

    Returns one SQL string for ``execute_script(cur, sql, dialect=…)``.
    No FK between the two base tables (per Schema_v6), so order is
    irrelevant; daily_balances first matches the schema-emit order.

    Z.C — ``prefix`` is the cfg.db_table_prefix.
    """
    p = prefix
    return (
        f"-- =====================================================================\n"
        f"-- L2 instance: {p} — base-table data wipe (step 2 of deploy pipeline)\n"
        f"-- Generated by recon_gen.common.l2.schema.wipe_demo_data_sql\n"
        f"-- Empties <prefix>_transactions + <prefix>_daily_balances; matviews\n"
        f"-- are re-derived in step 4 (refresh_matviews_sql).\n"
        f"-- =====================================================================\n"
        f"DELETE FROM {p}_daily_balances;\n"
        f"DELETE FROM {p}_transactions;\n"
    )


def refresh_matviews_sql(
    instance: L2Instance, *, prefix: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Emit `REFRESH MATERIALIZED VIEW` commands in dependency order.

    M.1a.9 made every L1-pipeline view a MATERIALIZED VIEW (kills the
    correlated-subquery cost the deployed dashboard pays per visual on
    DIRECT_QUERY mode). Refresh contract for integrators: after every
    batch insert into the base tables, call this SQL to recompute
    every dependent matview. Order matters — leaves first, then
    helpers, then L1 invariants — because a downstream matview's
    REFRESH reads from upstream matview data.

    Returns one `REFRESH MATERIALIZED VIEW <name>;` per line on PG /
    Oracle. SQLite has no matviews — refresh becomes a per-table
    ``DELETE FROM <name>; INSERT INTO <name> <body>;`` pair, where
    ``<body>`` is the same SELECT the matview was originally created
    with. To avoid duplicating every matview body here, the SQLite
    branch uses ``DROP TABLE … CREATE TABLE … AS <body>`` — re-runs
    the schema's matview-create SQL by tearing down + rebuilding.

    Caller splits + executes (psycopg2's cursor.execute can't run
    multiple statements separated by `;` reliably; the verify script
    splits on `;\\n` and runs each per-statement).

    Z.C — ``prefix`` is the cfg.db_table_prefix.
    """
    p = prefix
    names = [
        # Leaves: reads from base tables only.
        f"{p}_current_transactions",
        f"{p}_current_daily_balances",
        # Helpers: read from current_*.
        f"{p}_computed_subledger_balance",
        f"{p}_computed_ledger_balance",
        # L1 invariants: read from current_* + helpers.
        f"{p}_drift",
        f"{p}_ledger_drift",
        f"{p}_overdraft",
        f"{p}_expected_eod_balance_breach",
        f"{p}_limit_breach",
        f"{p}_stuck_pending",
        f"{p}_stuck_unbundled",
        # AB.2.3 — Chain Parent Disagreement: two-template chains where
        # leg_rail firings of one child Transfer disagree on which
        # parent firing they belong to. Reads from current_transactions
        # (no dependency on other L1 matviews). Sits with the other
        # L1 invariants in dependency order.
        f"{p}_chain_parent_disagreement",
        # AB.3.3 — XOR group violations: per (Transfer, template, XOR
        # group) firing-cardinality check. Reads current_transactions
        # + the L2 declaration (inlined); independent of other L1
        # matviews. Refresh before todays_exceptions so its UNION ALL
        # branch reads fresh rows.
        f"{p}_xor_group_violation",
        # AB.4.3 — Per-child Transfer parent set (long form). Derived
        # from current_transactions; AB.4.7's fan_in_disagreement
        # JOINs against this. Refresh before fan_in_disagreement so
        # the downstream matview sees fresh rows.
        f"{p}_transfer_parents",
        # AB.4.7 — Fan-in disagreement L1 invariant. JOINs against
        # _transfer_parents (AB.4.3) for the parent-count derivation;
        # MUST refresh AFTER _transfer_parents.
        f"{p}_fan_in_disagreement",
        # AB.6.5 — Multi-XOR violation L1 invariant. Reads from
        # current_transactions + L2 declaration inlined; independent
        # of other L1 matviews. Refresh before todays_exceptions so
        # its UNION ALL branch reads fresh rows.
        f"{p}_multi_xor_violation",
        # Dashboard-shape matviews: read from current_* +
        # L1 invariants. MUST refresh AFTER all L1 invariants are
        # fresh so todays_exceptions's UNION reads up-to-date data.
        f"{p}_daily_statement_summary",
        f"{p}_todays_exceptions",
        # Investigation matviews (N.3.b): read directly from base
        # ``{p}_transactions``, so they're independent of every L1
        # matview. Order between the two doesn't matter — they don't
        # reference each other.
        f"{p}_inv_pair_rolling_anomalies",
        f"{p}_inv_money_trail_edges",
    ]
    if dialect is Dialect.SQLITE:
        # X.3.c — SQLite has no matviews. Refresh = DROP + re-emit
        # the matview-as-table CREATE. We re-run the schema
        # template, but only the matview block (drops + creates),
        # since the base tables stay untouched by a refresh.
        return _emit_sqlite_matview_refresh(instance, prefix=p)
    # REFRESH first, then ANALYZE — ANALYZE updates planner stats so
    # subsequent SELECTs use the indexes we ship on each matview
    # (without ANALYZE the planner doesn't know the post-REFRESH row
    # count + value distribution and may pick a sequential scan).
    refreshes = "\n".join(refresh_matview(n, dialect) for n in names)
    analyzes = "\n".join(analyze_table(n, dialect) for n in names)
    return f"{refreshes}\n{analyzes}"


def _emit_sqlite_matview_refresh(instance: L2Instance, *, prefix: str) -> str:
    """X.3.c — SQLite refresh: tear down + re-emit every matview-as-table.

    The matview bodies live in the schema templates. Rather than
    duplicate them here, this helper re-runs the L1 invariant + Inv
    matview emission against the same instance / dialect so the
    refresh SQL is byte-equivalent to "drop then re-create" for every
    matview.

    The base tables (transactions / daily_balances) and base indexes
    are NOT in scope — a refresh leaves rows in place; only the
    derived matview tables get rebuilt.

    The dependency order is enforced by the templates' DROP block
    coming before the CREATE block (and the dashboard-shape /
    Investigation matviews living in templates that already encode
    the right order).

    Returns one SQL string. ANALYZE follows the rebuild so the
    planner picks up post-refresh row counts.

    Z.C — ``prefix`` is the cfg.db_table_prefix.
    """
    p = prefix
    drops_l1 = _emit_l1_invariant_drops(p, Dialect.SQLITE)
    drops_inv = _emit_inv_matview_drops(p, Dialect.SQLITE)
    drops_curr_tx = drop_matview_if_exists(
        f"{p}_current_transactions", Dialect.SQLITE,
    )
    drops_curr_db = drop_matview_if_exists(
        f"{p}_current_daily_balances", Dialect.SQLITE,
    )
    # Re-emit Current* matviews + all L1/Inv matviews. We extract
    # just the CREATE blocks from the schema templates by re-rendering
    # the inv + L1 invariant view sections (which depend on
    # current_*) plus the Current* matview CREATEs from the base
    # template.
    current_creates = _emit_sqlite_current_matview_creates(p)
    invariants = _emit_l1_invariant_views(instance, prefix=p, dialect=Dialect.SQLITE)
    inv_views = _emit_inv_views(instance, prefix=p, dialect=Dialect.SQLITE)
    names = [
        f"{p}_current_transactions",
        f"{p}_current_daily_balances",
        f"{p}_computed_subledger_balance",
        f"{p}_computed_ledger_balance",
        f"{p}_drift",
        f"{p}_ledger_drift",
        f"{p}_overdraft",
        f"{p}_expected_eod_balance_breach",
        f"{p}_limit_breach",
        f"{p}_stuck_pending",
        f"{p}_stuck_unbundled",
        f"{p}_chain_parent_disagreement",
        f"{p}_xor_group_violation",
        f"{p}_transfer_parents",
        f"{p}_fan_in_disagreement",
        f"{p}_multi_xor_violation",
        f"{p}_daily_statement_summary",
        f"{p}_todays_exceptions",
        f"{p}_inv_pair_rolling_anomalies",
        f"{p}_inv_money_trail_edges",
    ]
    analyzes = "\n".join(analyze_table(n, Dialect.SQLITE) for n in names)
    return (
        f"-- ===========================================================\n"
        f"-- SQLite matview refresh for L2 instance: {p}\n"
        f"-- Drops + re-emits every matview-as-table; base tables stay.\n"
        f"-- ===========================================================\n"
        + drops_l1 + "\n"
        + drops_inv + "\n"
        + drops_curr_tx + "\n"
        + drops_curr_db + "\n\n"
        + current_creates + "\n\n"
        + invariants + "\n\n"
        + inv_views + "\n\n"
        + analyzes + "\n"
    )


def _emit_sqlite_current_matview_creates(p: str) -> str:
    """Re-emit the Current* matview CREATE statements + their indexes
    for SQLite refresh.

    These live inside ``_SCHEMA_TEMPLATE`` for the regular schema
    emit; the refresh path can't invoke the full template (the base-
    table CREATE would conflict with existing data). This helper
    isolates just the Current* matview CREATEs + their indexes so
    the SQLite refresh path can rebuild them after a DROP.
    """
    matview_kw = matview_create_keyword(Dialect.SQLITE)
    matview_opts = matview_options(Dialect.SQLITE)
    return (
        f"{matview_kw} {p}_current_transactions{matview_opts} AS\n"
        f"SELECT * FROM {p}_transactions tx\n"
        f"WHERE tx.entry = (\n"
        f"    SELECT MAX(entry) FROM {p}_transactions WHERE id = tx.id\n"
        f");\n"
        f"CREATE INDEX idx_{p}_curr_tx_account_posting\n"
        f"    ON {p}_current_transactions (account_id, posting);\n"
        f"CREATE INDEX idx_{p}_curr_tx_transfer ON {p}_current_transactions (transfer_id);\n"
        f"CREATE INDEX idx_{p}_curr_tx_id ON {p}_current_transactions (id);\n"
        f"CREATE INDEX idx_{p}_curr_tx_status ON {p}_current_transactions (status);\n"
        f"CREATE INDEX idx_{p}_curr_tx_posting_rail_name\n"
        f"    ON {p}_current_transactions (posting, rail_name);\n"
        f"CREATE INDEX idx_{p}_curr_tx_template_name\n"
        f"    ON {p}_current_transactions (template_name);\n"
        f"CREATE INDEX idx_{p}_curr_tx_parent\n"
        f"    ON {p}_current_transactions (transfer_parent_id);\n"
        f"\n"
        f"{matview_kw} {p}_current_daily_balances{matview_opts} AS\n"
        f"SELECT * FROM {p}_daily_balances sb\n"
        f"WHERE sb.entry = (\n"
        f"    SELECT MAX(entry)\n"
        f"    FROM {p}_daily_balances\n"
        f"    WHERE account_id = sb.account_id\n"
        f"      AND business_day_start = sb.business_day_start\n"
        f");\n"
        f"CREATE INDEX idx_{p}_curr_db_account_day\n"
        f"    ON {p}_current_daily_balances (account_id, business_day_start);\n"
        f"CREATE INDEX idx_{p}_curr_db_scope_day\n"
        f"    ON {p}_current_daily_balances (account_scope, business_day_start);\n"
    )


def _emit_l1_invariant_views(
    instance: L2Instance, *, prefix: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Render the M.1a.7 L1-invariant view block for ``instance``.

    Each view drops + creates idempotently so repeated runs converge.
    Drop order is reverse of create order (no view depends on a later
    one).

    Dialect-specific patterns substituted into the template:
    - ``{matview_options}`` — Oracle's BUILD IMMEDIATE REFRESH COMPLETE
      ON DEMAND suffix (empty on Postgres).
    - ``{date_trunc_tx_posting}`` — DATE_TRUNC('day', tx.posting) on
      Postgres / CAST(TRUNC(tx.posting) AS TIMESTAMP) on Oracle.
    - ``{epoch_age_seconds}`` — EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP -
      ct.posting)) on Postgres / sum-of-EXTRACTs on Oracle.
    - ``{posting_to_date}`` — posting::date on Postgres / TRUNC(posting)
      on Oracle.
    - ``{null_text}`` — NULL::TEXT on Postgres / CAST(NULL AS CLOB) on
      Oracle (the typed NULL preserves the UNION ALL column type
      across mixed-NULL branches).
    """
    p = prefix
    # Phase BC.12 (2026-05-24): per-rail max_*_age caps + per-(parent_role,
    # rail, direction) limit-schedule caps no longer JSON_TABLE-iterate
    # `<prefix>_config.l2_yaml` inside the matview body. Oracle 19c+
    # rejects matviews built on JSON_TABLE-of-CLOB with ORA-32368; the
    # workaround is the typed projection views
    # (`<prefix>_v_config_limit_schedules` + `<prefix>_v_config_rails`)
    # emitted alongside the matviews. Each typed view body is a plain
    # relational walk of `<prefix>_config_kv` (parent_id self-join, no
    # JSON_TABLE); matviews JOIN against the views and the engine sees
    # a fully relational source. See `docs/audits/bc_12_config_kv_spike.md`
    # for the architecture + spike results.
    limit_join_outbound = (
        f"{p}_v_config_limit_schedules ls\n"
        f"      ON ls.parent_role = tx.account_parent_role\n"
        f"     AND ls.rail = tx.rail_name\n"
        f"     AND ls.direction = 'Outbound'"
    )
    limit_join_inbound = (
        f"{p}_v_config_limit_schedules ls\n"
        f"      ON ls.parent_role = tx.account_parent_role\n"
        f"     AND ls.rail = tx.rail_name\n"
        f"     AND ls.direction = 'Inbound'"
    )
    # AO.1: amount_money is BIGINT cents; L2's limit-schedule cap is
    # authored in dollars. Multiply the projected-view cap by 100 so
    # the matview's ``SUM(ABS(amount_money)) > cap`` compares
    # cents-vs-cents.
    limit_cap_value = "(ls.cap * 100)"
    # The shared rails-projection JOIN (same view for both stuck_pending
    # + stuck_unbundled since they project the same rail.name; the
    # difference is which field they read).
    pending_age_join = (
        f"{p}_v_config_rails rail ON rail.name = ct.rail_name"
    )
    pending_age_value = "rail.max_pending_age_seconds"
    unbundled_age_join = pending_age_join
    unbundled_age_value = "rail.max_unbundled_age_seconds"
    xor_group_violation_body = _render_xor_group_violation_body(
        instance, p=p, dialect=dialect,
    )
    chain_parent_disagreement_fan_in_filter = (
        _render_chain_parent_disagreement_fan_in_filter(instance)
    )
    fan_in_disagreement_body = _render_fan_in_disagreement_body(
        instance, p=p, dialect=dialect,
    )
    multi_xor_violation_body = _render_multi_xor_violation_body(
        instance, p=p, dialect=dialect,
    )
    return _L1_INVARIANT_VIEWS_TEMPLATE.format(
        p=p,
        limit_join_outbound=limit_join_outbound,
        limit_join_inbound=limit_join_inbound,
        limit_cap_value=limit_cap_value,
        pending_age_join=pending_age_join,
        pending_age_value=pending_age_value,
        unbundled_age_join=unbundled_age_join,
        unbundled_age_value=unbundled_age_value,
        xor_group_violation_body=xor_group_violation_body,
        chain_parent_disagreement_fan_in_filter=chain_parent_disagreement_fan_in_filter,
        fan_in_disagreement_body=fan_in_disagreement_body,
        multi_xor_violation_body=multi_xor_violation_body,
        matview_options=matview_options(dialect),
        matview_create_kw=matview_create_keyword(dialect),
        date_trunc_tx_posting=date_trunc_day("tx.posting", dialect),
        # Phase AW.2 (2026-05-23): age is computed against the owned
        # temporal frame in `<prefix>_config_kv`'s ``as_of`` scalar
        # row, not the matview engine's wall-clock CURRENT_TIMESTAMP.
        # Plant + matview read from one source — tests become
        # deterministic, prod refresh helper sets as_of=CURRENT_TIMESTAMP
        # per refresh. See audit §6 "own the temporal frame" + AW.0 spike.
        # BC.12 (2026-05-24): the storage shape moved from the typed
        # ``<prefix>_config.as_of`` column to a kv row at ``key='as_of'``.
        # ``kv_as_of_as_timestamp_sql`` handles the dialect-specific
        # text→TIMESTAMP coercion (Oracle needs TO_TIMESTAMP +
        # DBMS_LOB.SUBSTR; PG is plain CAST; SQLite passes text
        # straight through to julianday()).
        epoch_age_seconds=epoch_seconds_between(
            kv_as_of_as_timestamp_sql(p, dialect),
            "ct.posting", dialect,
        ),
        posting_to_date=to_date("posting", dialect),
        # Typed NULL for the UNION ALL rail_name column. Oracle
        # rejects ``CAST(NULL AS CLOB)`` here (ORA-00932) because the
        # subsequent UNION branches' rail_name values are
        # VARCHAR2(100) — Oracle won't UNION CLOB with VARCHAR2. Bind
        # to a VARCHAR-shaped NULL so the UNION column type matches
        # the actual data in every branch on both dialects. Z.B
        # (2026-05-15) renamed from transfer_type under the symmetric
        # collapse.
        null_text=cast("NULL", varchar_type(100, dialect), dialect),
        # BC.12 surfaced (after ORA-32368 unblocked stmt #91): the
        # transfer-keyed UNION ALL branches in todays_exceptions used
        # ``CAST(NULL AS BIGINT) AS magnitude_amount`` — Oracle has no
        # BIGINT, so the cast fails ORA-00902 "invalid datatype".
        # Route through ``typed_null`` so the alias maps to NUMBER(19)
        # on Oracle, BIGINT on PG, INTEGER on SQLite.
        null_bigint=typed_null("bigint", dialect),
    )


# Phase AW.4 (2026-05-23): `_render_limit_breach_cases` removed. Caps now
# read from `<prefix>_config.l2_yaml.$.limit_schedules` via LEFT JOIN
# (multi-key filter: parent_role + rail + direction); per-(parent_role,
# rail, direction) values are no longer baked at emit time. The matview
# is persona-blind.
#
# Phase AW.3 (2026-05-23): `_render_pending_age_cases` removed. Same
# pattern for per-rail max_pending_age; matview reads from
# `<prefix>_config.l2_yaml.$.rails`.


def _render_chain_parent_disagreement_fan_in_filter(
    instance: L2Instance,
) -> str:
    """AB.4.4 (AB.6 per-child): Render the optional
    ``AND tx.template_name NOT IN (...)`` clause that excludes
    fan_in-marked chain children from the ``chain_parent_disagreement``
    matview.

    Fan_in chain children are legitimately multi-parent (N parent
    firings share one child Transfer by design — the batched-payout
    pattern). AB.2.3's matview rule "COUNT(DISTINCT parent_transfer_id)
    > 1" would false-positive every fan_in firing as a violation; this
    filter excludes them at the source.

    AB.6 (2026-05-19) — fan_in moved per-child, so the source is now
    `[child.name for chain in chains for child in chain.children
    if child.fan_in]`. Mixed-cardinality chains (one fan_in child +
    other 1:1 children) contribute only the fan_in child to the
    NOT IN filter — siblings stay under the AB.2.3 1:1 contract.

    When no child declares ``fan_in=True``, the fan-in template set
    is empty and the filter resolves to ``""`` — behavior is
    byte-identical to AB.2.3.
    """
    fan_in_templates: set[str] = set()
    for chain in instance.chains:
        for child in chain.children:
            if not child.fan_in:
                continue
            # C8a guarantees every fan_in child is a TransferTemplate
            # (validator enforces); collect their names.
            fan_in_templates.add(str(child.name))
    if not fan_in_templates:
        return ""
    quoted = ", ".join(f"'{name}'" for name in sorted(fan_in_templates))
    return f"\n  AND tx.template_name NOT IN ({quoted})"


def _render_xor_group_violation_body(
    instance: L2Instance, *, p: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """AB.3.3: Render the body of ``{p}_xor_group_violation``.

    Per AB.3.0 lock: per-(transfer_id, template_name, xor_group_index)
    row carrying ``firing_count`` (HAVING ``<> 1`` to surface both
    overlap ≥2 and missed-firing 0 cases), ``fired_rails`` (dialect-
    specific concat aggregate), and ``business_day``. The 0-firings
    case requires a LEFT JOIN against the per-template-XOR-group
    expected set; the SELECT-list expressions COALESCE the LEFT-JOIN's
    NULLable transfer-side columns so the row still surfaces.

    Empty case: when no template in ``instance.transfer_templates``
    declares any ``leg_rail_xor_groups`` (the pre-AB.3.5.spec /
    pre-AB.3.6 state for every L2), the matview body falls back to
    a typed-NULL placeholder SELECT with ``WHERE 1=0`` so it parses
    + plans cleanly on all 3 dialects but contributes zero rows. The
    same shape is used by ``_render_pending_age_cases`` for the
    no-aging-rails case.

    Returns the SQL body (everything between ``... AS`` and the
    terminating ``;``) — caller's template wraps it in
    ``{matview_create_kw} {p}_xor_group_violation{matview_options} AS\\n``
    and the trailing ``;``.
    """
    # Collect per-template XOR group declarations.
    # Shape: [(template_name, group_index, member_rail_name), ...]
    triples: list[tuple[str, int, str]] = []
    for t in instance.transfer_templates:
        for gi, group in enumerate(t.leg_rail_xor_groups):
            for member in group:
                triples.append((str(t.name), gi, str(member)))

    if not triples:
        # Empty case — emit typed-NULL placeholder so the matview
        # parses + plans on all 3 dialects and produces zero rows.
        # Column type alignment mirrors the non-empty branch (varchar
        # for ids/names/rails, integer for firing_count + xor_group_index,
        # timestamp for business_day).
        null_varchar = typed_null("VARCHAR(100)", dialect)
        null_int = typed_null("INTEGER", dialect)
        null_ts = typed_null("TIMESTAMP", dialect)
        # SQLite + Postgres can SELECT without FROM but Oracle needs
        # FROM dual — wrap accordingly.
        if dialect is Dialect.ORACLE:
            from_clause = "FROM dual"
        else:
            from_clause = ""
        return (
            f"SELECT {null_varchar} AS transfer_id,\n"
            f"       {null_varchar} AS template_name,\n"
            f"       {null_int} AS xor_group_index,\n"
            f"       {null_int} AS firing_count,\n"
            f"       {null_varchar} AS fired_rails,\n"
            f"       {null_ts} AS business_day\n"
            f"{from_clause}\n"
            f"WHERE 1=0"
        )

    # Build the inline rowset of (template, group_index, member) triples.
    # SQLite doesn't accept the PG-style ``(VALUES ...) AS g(col, ...)``
    # column-list alias — moving the column-list onto the WITH clause
    # (``WITH g(col, ...) AS (VALUES ...)``) is portable across PG +
    # SQLite. Oracle has neither shape; use ``SELECT ... FROM dual
    # UNION ALL`` so the columns get their names from the SELECT-list.
    fired_rails_agg = concat_agg("tx.rail_name", ",", dialect)
    if dialect is Dialect.ORACLE:
        union_rows = "\n  UNION ALL ".join(
            f"SELECT '{t}' AS template_name, "
            f"{gi} AS xor_group_index, "
            f"'{m}' AS member_rail_name FROM dual"
            for t, gi, m in triples
        )
        xor_groups_cte = (
            f"xor_groups AS (\n"
            f"  {union_rows}\n"
            f")"
        )
    else:
        rows = ",\n    ".join(
            f"('{t}', {gi}, '{m}')" for t, gi, m in triples
        )
        xor_groups_cte = (
            f"xor_groups(template_name, xor_group_index, member_rail_name) AS (\n"
            f"  VALUES\n    {rows}\n"
            f")"
        )

    return (
        f"WITH {xor_groups_cte},\n"
        f"template_transfers AS (\n"
        f"  -- Every Transfer instance of a template that has at least\n"
        f"  -- one XOR group. We GROUP BY (transfer_id, template_name)\n"
        f"  -- via the cartesian below to get one row per (Transfer, group).\n"
        f"  SELECT DISTINCT tx.transfer_id, tx.template_name,\n"
        f"         MIN({date_trunc_day('tx.posting', dialect)})\n"
        f"           OVER (PARTITION BY tx.transfer_id, tx.template_name) AS business_day\n"
        f"  FROM {p}_current_transactions tx\n"
        f"  WHERE tx.status <> 'Failed'\n"
        f"    AND tx.template_name IN (SELECT DISTINCT template_name FROM xor_groups)\n"
        f"),\n"
        f"expected AS (\n"
        f"  -- Cartesian: every (Transfer-of-T, group-of-T) pair we need\n"
        f"  -- to check.\n"
        f"  SELECT tt.transfer_id, tt.template_name, g.xor_group_index,\n"
        f"         MIN(tt.business_day) AS business_day\n"
        f"  FROM template_transfers tt\n"
        f"  JOIN xor_groups g ON g.template_name = tt.template_name\n"
        f"  GROUP BY tt.transfer_id, tt.template_name, g.xor_group_index\n"
        f")\n"
        f"SELECT\n"
        f"  e.transfer_id,\n"
        f"  e.template_name,\n"
        f"  e.xor_group_index,\n"
        f"  COUNT(tx.transfer_id) AS firing_count,\n"
        f"  COALESCE({fired_rails_agg}, '') AS fired_rails,\n"
        f"  e.business_day\n"
        f"FROM expected e\n"
        f"JOIN xor_groups g\n"
        f"  ON g.template_name = e.template_name\n"
        f"  AND g.xor_group_index = e.xor_group_index\n"
        f"LEFT JOIN {p}_current_transactions tx\n"
        f"  ON tx.transfer_id = e.transfer_id\n"
        f"  AND tx.template_name = e.template_name\n"
        f"  AND tx.rail_name = g.member_rail_name\n"
        f"  AND tx.status <> 'Failed'\n"
        f"GROUP BY e.transfer_id, e.template_name, e.xor_group_index, e.business_day\n"
        f"HAVING COUNT(tx.transfer_id) <> 1"
    )


def _render_fan_in_disagreement_body(
    instance: L2Instance, *, p: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """AB.4.7 (AB.6 per-child): Render the body of
    ``{p}_fan_in_disagreement``.

    Per AB.4.0 lock: long-form, one row per (child_transfer_id,
    disagreement_kind) tuple where ``kind`` ∈ ``{'orphan', 'missing',
    'extra'}``. Columns: ``child_transfer_id``, ``chain_parent_name``,
    ``child_template_name``, ``parent_count`` (actual),
    ``expected_parent_count`` (NULL when entry leaves it unset),
    ``disagreement_kind``, ``business_day``.

    AB.6 (2026-05-19): the CTE source shifted from "one row per fan_in
    chain" to "one row per fan_in chain *child entry*" — so
    ``(chain.parent, child.name, child.expected_parent_count)`` is
    drawn from each per-child entry with ``fan_in=True``.
    Mixed-cardinality chains (some fan_in children, some not) only
    contribute their fan_in entries; the 1:1 siblings stay under
    AB.2.3's contract.

    Joins AB.4.3's ``_transfer_parents`` matview against an inline
    rowset of the L2's fan_in entries (``(chain_parent_name,
    child_template_name, expected_parent_count)``). For each child
    Transfer:

    - **healthy** (no row): ``parent_count == expected`` (when set)
      OR ``parent_count >= 2`` (when unset). The matview emits no row.
    - **missing** row: ``parent_count < expected`` (only when
      ``expected_parent_count`` is set on the entry).
    - **extra** row: ``parent_count > expected`` (same gate).
    - **orphan** row: ``parent_count < 2`` AND
      ``expected_parent_count`` is unset (variable-batch-flow
      fallback per AB.4.0 lock).

    Empty case: when no chain child declares ``fan_in=True``, the
    matview body falls back to a typed-NULL placeholder with
    ``WHERE 1=0``
    (mirrors AB.3.3's empty-XOR pattern) so it parses + plans
    cleanly on all 3 dialects and contributes zero rows.
    """
    fan_in_rows: list[tuple[str, str, int | None]] = []
    for chain in instance.chains:
        for child in chain.children:
            if not child.fan_in:
                continue
            # C8a guarantees every fan_in child is a TransferTemplate.
            fan_in_rows.append(
                (str(chain.parent), str(child.name), child.expected_parent_count),
            )

    if not fan_in_rows:
        null_varchar = typed_null("VARCHAR(100)", dialect)
        null_int = typed_null("INTEGER", dialect)
        null_ts = typed_null("TIMESTAMP", dialect)
        if dialect is Dialect.ORACLE:
            from_clause = "FROM dual"
        else:
            from_clause = ""
        return (
            f"SELECT {null_varchar} AS child_transfer_id,\n"
            f"       {null_varchar} AS chain_parent_name,\n"
            f"       {null_varchar} AS child_template_name,\n"
            f"       {null_int} AS parent_count,\n"
            f"       {null_int} AS expected_parent_count,\n"
            f"       {null_varchar} AS disagreement_kind,\n"
            f"       {null_ts} AS business_day\n"
            f"{from_clause}\n"
            f"WHERE 1=0"
        )

    # Inline the per-chain expected-set CTE. Oracle uses
    # UNION-ALL-of-SELECT-FROM-DUAL; PG + SQLite use VALUES under a
    # WITH column-list (the AB.3.3 portable shape). expected_parent_count
    # may be NULL — both forms encode that as a typed-NULL.
    null_int = typed_null("INTEGER", dialect)
    if dialect is Dialect.ORACLE:
        def fmt_expected(v: int | None) -> str:
            return str(v) if v is not None else null_int
        union_rows = "\n  UNION ALL ".join(
            f"SELECT '{cp}' AS chain_parent_name, "
            f"'{ct}' AS child_template_name, "
            f"{fmt_expected(ex)} AS expected_parent_count FROM dual"
            for cp, ct, ex in fan_in_rows
        )
        fan_in_chains_cte = (
            f"fan_in_chains AS (\n"
            f"  {union_rows}\n"
            f")"
        )
    else:
        def fmt_row(cp: str, ct: str, ex: int | None) -> str:
            ex_sql = str(ex) if ex is not None else "NULL"
            return f"('{cp}', '{ct}', {ex_sql})"
        rows = ",\n    ".join(
            fmt_row(cp, ct, ex) for cp, ct, ex in fan_in_rows
        )
        fan_in_chains_cte = (
            f"fan_in_chains(chain_parent_name, child_template_name, "
            f"expected_parent_count) AS (\n"
            f"  VALUES\n    {rows}\n"
            f")"
        )

    return (
        f"WITH {fan_in_chains_cte},\n"
        f"child_parent_counts AS (\n"
        f"  -- Per child Transfer: how many DISTINCT parents contributed\n"
        f"  -- (from AB.4.3's _transfer_parents) + the template_name +\n"
        f"  -- the earliest contributing leg's business day for the\n"
        f"  -- analyst drill axis.\n"
        f"  SELECT\n"
        f"    tp.child_transfer_id,\n"
        f"    MIN(tx.template_name) AS template_name,\n"
        f"    COUNT(DISTINCT tp.parent_transfer_id) AS parent_count,\n"
        f"    MIN({date_trunc_day('tx.posting', dialect)}) AS business_day\n"
        f"  FROM {p}_transfer_parents tp\n"
        f"  JOIN {p}_current_transactions tx\n"
        f"    ON tx.transfer_id = tp.child_transfer_id\n"
        f"   AND tx.template_name IS NOT NULL\n"
        f"   AND tx.status <> 'Failed'\n"
        f"  GROUP BY tp.child_transfer_id\n"
        f")\n"
        f"SELECT\n"
        f"  cpc.child_transfer_id,\n"
        f"  fic.chain_parent_name,\n"
        f"  cpc.template_name AS child_template_name,\n"
        f"  cpc.parent_count,\n"
        f"  fic.expected_parent_count,\n"
        f"  CASE\n"
        f"    WHEN fic.expected_parent_count IS NULL\n"
        f"         AND cpc.parent_count < 2 THEN 'orphan'\n"
        f"    WHEN fic.expected_parent_count IS NOT NULL\n"
        f"         AND cpc.parent_count < fic.expected_parent_count\n"
        f"         THEN 'missing'\n"
        f"    WHEN fic.expected_parent_count IS NOT NULL\n"
        f"         AND cpc.parent_count > fic.expected_parent_count\n"
        f"         THEN 'extra'\n"
        f"  END AS disagreement_kind,\n"
        f"  cpc.business_day\n"
        f"FROM child_parent_counts cpc\n"
        f"JOIN fan_in_chains fic\n"
        f"  ON fic.child_template_name = cpc.template_name\n"
        f"WHERE\n"
        f"  (fic.expected_parent_count IS NULL\n"
        f"   AND cpc.parent_count < 2)\n"
        f"  OR (fic.expected_parent_count IS NOT NULL\n"
        f"      AND cpc.parent_count <> fic.expected_parent_count)"
    )


def _render_multi_xor_violation_body(
    instance: L2Instance, *, p: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """AB.6.5: Render the body of ``{p}_multi_xor_violation``.

    Per AB.6.0 Lock 3: long-form, one row per (parent_transfer_id,
    disagreement_kind) tuple where ``kind`` ∈ ``{'missed', 'overlap'}``.
    Columns: ``parent_transfer_id``, ``parent_rail_or_template_name``,
    ``child_count`` (actual count of declared XOR siblings that fired),
    ``fired_children`` (comma-separated names), ``disagreement_kind``,
    ``business_day``.

    Sources rows from every multi-children chain (``len(children) >= 2``)
    after SKIPPING per-child fan_in entries (their cardinality is
    ``_fan_in_disagreement``'s job — AB.5 coupling per Lock 3). Mixed-
    cardinality chains contribute only their non-fan_in children to
    this matview; the fan_in entries are enforced by AB.4.7.

    For each parent firing under such a chain, LEFT JOIN against
    ``_current_transactions`` to count declared XOR siblings that
    fired (transfer_parent_id = parent.transfer_id AND
    name IN declared-siblings). HAVING ``COUNT(...) <> 1`` surfaces
    both the missed (count=0) and overlap (count≥2) cases.

    Empty case: when no chain qualifies (no multi-children chain
    without fan_in entries), the body falls back to a typed-NULL
    placeholder SELECT with ``WHERE 1=0`` (mirrors AB.3.3 + AB.4.7).
    """
    # Collect (chain_parent_name, child_name) pairs from every
    # multi-children chain, SKIPPING per-child fan_in entries.
    pairs: list[tuple[str, str]] = []
    for chain in instance.chains:
        # Skip singleton-children chains — those are 1:1 (required)
        # semantics, not XOR.
        if len(chain.children) < 2:
            continue
        # Skip per-child fan_in entries — their cardinality is the
        # AB.4.7 _fan_in_disagreement matview's job.
        non_fan_in_children = [c for c in chain.children if not c.fan_in]
        # Need ≥2 non-fan_in children to qualify as multi-XOR. (A chain
        # like [ChildA(1:1), ChildB(fan_in)] has only 1 non-fan_in child
        # → AB.2.3's 1:1 enforcement covers it.)
        if len(non_fan_in_children) < 2:
            continue
        for child in non_fan_in_children:
            pairs.append((str(chain.parent), str(child.name)))

    if not pairs:
        null_varchar = typed_null("VARCHAR(100)", dialect)
        null_int = typed_null("INTEGER", dialect)
        null_ts = typed_null("TIMESTAMP", dialect)
        from_clause = "FROM dual" if dialect is Dialect.ORACLE else ""
        return (
            f"SELECT {null_varchar} AS parent_transfer_id,\n"
            f"       {null_varchar} AS parent_rail_or_template_name,\n"
            f"       {null_int} AS child_count,\n"
            f"       {null_varchar} AS fired_children,\n"
            f"       {null_varchar} AS disagreement_kind,\n"
            f"       {null_ts} AS business_day\n"
            f"{from_clause}\n"
            f"WHERE 1=0"
        )

    # Inline the (chain_parent_name, child_name) rowset.
    if dialect is Dialect.ORACLE:
        union_rows = "\n  UNION ALL ".join(
            f"SELECT '{cp}' AS chain_parent_name, "
            f"'{cn}' AS child_name FROM dual"
            for cp, cn in pairs
        )
        multi_xor_chains_cte = (
            f"multi_xor_chains AS (\n"
            f"  {union_rows}\n"
            f")"
        )
    else:
        rows = ",\n    ".join(f"('{cp}', '{cn}')" for cp, cn in pairs)
        multi_xor_chains_cte = (
            f"multi_xor_chains(chain_parent_name, child_name) AS (\n"
            f"  VALUES\n    {rows}\n"
            f")"
        )

    fired_agg = concat_agg("fcd.matched_child_name", ",", dialect)
    return (
        f"WITH {multi_xor_chains_cte},\n"
        f"parent_names AS (\n"
        f"  SELECT DISTINCT chain_parent_name AS name FROM multi_xor_chains\n"
        f"),\n"
        f"parent_firings AS (\n"
        f"  -- Every transfer that fires under a multi-XOR chain parent.\n"
        f"  -- Chain.parent can be a rail OR a template name (UNION both).\n"
        f"  -- DISTINCT collapses multi-leg template firings to one row.\n"
        f"  SELECT DISTINCT tx.transfer_id AS parent_transfer_id,\n"
        f"         tx.template_name AS chain_parent_name,\n"
        f"         {date_trunc_day('tx.posting', dialect)} AS business_day\n"
        f"  FROM {p}_current_transactions tx\n"
        f"  WHERE tx.template_name IN (SELECT name FROM parent_names)\n"
        f"    AND tx.status <> 'Failed'\n"
        f"  UNION\n"
        f"  SELECT DISTINCT tx.transfer_id, tx.rail_name,\n"
        f"         {date_trunc_day('tx.posting', dialect)}\n"
        f"  FROM {p}_current_transactions tx\n"
        f"  WHERE tx.rail_name IN (SELECT name FROM parent_names)\n"
        f"    AND tx.status <> 'Failed'\n"
        f"),\n"
        f"fired_children_distinct AS (\n"
        f"  -- For each parent firing, which declared XOR siblings\n"
        f"  -- fired? LEFT JOIN preserves the missed (count=0) case\n"
        f"  -- (the DISTINCT collapses multi-leg child firings to one\n"
        f"  -- name per (parent, child)).\n"
        f"  SELECT DISTINCT\n"
        f"    pf.parent_transfer_id,\n"
        f"    pf.chain_parent_name,\n"
        f"    pf.business_day,\n"
        f"    CASE WHEN ch.rail_name IS NOT NULL\n"
        f"           AND EXISTS (SELECT 1 FROM multi_xor_chains m\n"
        f"                       WHERE m.chain_parent_name = pf.chain_parent_name\n"
        f"                         AND m.child_name = ch.rail_name)\n"
        f"         THEN ch.rail_name\n"
        f"         WHEN ch.template_name IS NOT NULL\n"
        f"           AND EXISTS (SELECT 1 FROM multi_xor_chains m\n"
        f"                       WHERE m.chain_parent_name = pf.chain_parent_name\n"
        f"                         AND m.child_name = ch.template_name)\n"
        f"         THEN ch.template_name\n"
        f"    END AS matched_child_name\n"
        f"  FROM parent_firings pf\n"
        f"  LEFT JOIN {p}_current_transactions ch\n"
        f"    ON ch.transfer_parent_id = pf.parent_transfer_id\n"
        f"   AND ch.status <> 'Failed'\n"
        f")\n"
        f"SELECT\n"
        f"  fcd.parent_transfer_id,\n"
        f"  fcd.chain_parent_name AS parent_rail_or_template_name,\n"
        f"  COUNT(fcd.matched_child_name) AS child_count,\n"
        f"  COALESCE({fired_agg}, '') AS fired_children,\n"
        f"  CASE WHEN COUNT(fcd.matched_child_name) = 0 THEN 'missed'\n"
        f"       ELSE 'overlap' END AS disagreement_kind,\n"
        f"  MIN(fcd.business_day) AS business_day\n"
        f"FROM fired_children_distinct fcd\n"
        f"GROUP BY fcd.parent_transfer_id, fcd.chain_parent_name\n"
        f"HAVING COUNT(fcd.matched_child_name) <> 1"
    )


def _emit_inv_views(
    instance: L2Instance, *, prefix: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Render the N.3.b Investigation matview block for ``instance``.

    Two matviews — both per-instance prefixed:

    - ``<prefix>_inv_pair_rolling_anomalies`` — rolling 2-day SUM per
      (sender, recipient) pair + population z-score + 5-band bucket.
      Volume Anomalies sheet reads from this.
    - ``<prefix>_inv_money_trail_edges`` — recursive-CTE walk over
      ``parent_transfer_id`` flattened to one row per multi-leg edge
      (with chain root + depth). Money Trail + Account Network sheets
      read from this.

    Both read only from ``<prefix>_transactions`` — no other matviews,
    no ``daily_balances``. Independent of each other; can refresh in
    any order.

    Dialect-specific patterns substituted into the template:
    - ``{matview_options}`` — Oracle BUILD IMMEDIATE REFRESH COMPLETE
      ON DEMAND suffix (empty on Postgres).
    - ``{recipient_posting_to_date}`` — ``recipient.posting::date`` on
      Postgres / ``TRUNC(recipient.posting)`` on Oracle.
    - ``{rolling_window}`` — full PARTITION BY / ORDER BY / RANGE
      BETWEEN clause for the rolling 2-day pair window, inlined into
      each ``OVER (...)`` because Oracle 19c doesn't support the named
      ``WINDOW w AS`` clause (added in 21c). The dialect-specific
      ``INTERVAL`` form ships inside the RANGE clause: PG ``INTERVAL
      '1 day'`` / Oracle ``INTERVAL '1' DAY``.
    - ``{cast_avg_numeric}`` / ``{cast_stddev_numeric}`` — ``::NUMERIC``
      on Postgres / ``CAST(... AS NUMBER)`` on Oracle.
    - ``{window_start_expr}`` / ``{window_end_expr}`` — date arithmetic
      + cast to TIMESTAMP, dialect-specific interval form.
    - ``{with_recursive_kw}`` — ``WITH RECURSIVE`` on Postgres / ``WITH``
      on Oracle (Oracle 19c infers recursion from self-reference).

    Refresh contract is unchanged across dialects: not auto-refreshed,
    ``demo apply`` runs ``REFRESH MATERIALIZED VIEW`` (Postgres) or
    ``DBMS_MVIEW.REFRESH`` (Oracle, via ``refresh_matview`` helper)
    after seed inserts.

    Z.C — ``prefix`` is the cfg.db_table_prefix.
    """
    p = prefix
    # Inline the rolling-2-day window definition. Oracle 19c doesn't
    # support the named ``WINDOW w AS (...)`` clause (added in 21c),
    # so each ``OVER (...)`` substitutes the full definition. PG accepts
    # the same inline form — slightly more verbose than the named-window
    # PG-only optimization but the planner produces the same plan.
    rolling_window = (
        "PARTITION BY recipient_account_id, sender_account_id "
        f"ORDER BY {order_by_day_expr('posted_day', dialect)} "
        f"RANGE BETWEEN {range_interval_days(1, dialect)} "
        f"PRECEDING AND CURRENT ROW"
    )
    return _INV_MATVIEWS_TEMPLATE.format(
        p=p,
        matview_options=matview_options(dialect),
        matview_create_kw=matview_create_keyword(dialect),
        recipient_posting_to_date=to_date("recipient.posting", dialect),
        rolling_window=rolling_window,
        cast_avg_numeric=cast("AVG(window_sum)", "NUMERIC", dialect),
        cast_stddev_numeric=cast(
            "COALESCE(STDDEV_SAMP(window_sum), 0)", "NUMERIC", dialect,
        ),
        window_start_expr=cast(
            date_minus_days("pw.posted_day", 1, dialect),
            "TIMESTAMP", dialect,
        ),
        window_end_expr=cast("pw.posted_day", "TIMESTAMP", dialect),
        with_recursive_kw=with_recursive(dialect),
    )


# Phase AW.3 (2026-05-23): `_render_unbundled_age_cases` removed. Caps
# now read from `<prefix>_config.l2_yaml` via LEFT JOIN; same pattern as
# stuck_pending. The matview is persona-blind.


# Base-schema indexes that need a DROP IF EXISTS in the preamble (the
# CREATE statements live inline in _SCHEMA_TEMPLATE further below).
# Order doesn't matter for indexes — they're independent objects.
_BASE_INDEX_DROPS: tuple[tuple[str, str], ...] = (
    # (placeholder_key, index_name_template)
    ("drop_idx_account_posting", "idx_{p}_transactions_account_posting"),
    ("drop_idx_transfer", "idx_{p}_transactions_transfer"),
    ("drop_idx_type_status", "idx_{p}_transactions_type_status"),
    ("drop_idx_business_day", "idx_{p}_transactions_business_day"),
    ("drop_idx_parent", "idx_{p}_transactions_parent"),
    ("drop_idx_bundler", "idx_{p}_transactions_bundler_eligibility"),
    ("drop_idx_db_business_day", "idx_{p}_daily_balances_business_day"),
)


def _declared_metadata_keys(instance: L2Instance) -> list[str]:
    """Sorted, distinct ``metadata_keys`` declared across every Rail.

    Mirrors ``apps.l2_flow_tracing.datasets.declared_metadata_keys``;
    duplicated here to avoid an apps→common reverse import for the
    schema-emit-time index enumeration. The list is the universe of
    keys the L2FT metadata cascade can filter on, so the functional
    index set must cover exactly these.
    """
    keys: set[str] = set()
    for r in instance.rails:
        for k in r.metadata_keys:
            keys.add(str(k))
    return sorted(keys)


def _metadata_index_name(p: str, key: str) -> str:
    """Index identifier for the per-key JSON_VALUE functional index.

    Keys can carry characters Postgres / Oracle don't accept in
    identifiers (``:``, ``-``, etc.); replace anything outside
    ``[A-Za-z0-9_]`` with ``_`` so the resulting identifier is always
    legal in both dialects. Oracle 19c+ supports 128-byte
    identifiers and PG 63 — long-prefix instances stay safely inside
    both limits even with a long key (e.g. ``sasquatch_pr`` (12) +
    ``_tx_meta_`` (9) + ``customer_id`` (11) = 32 chars).
    """
    import re
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", key)
    return f"idx_{p}_tx_meta_{sanitized}"


def _emit_metadata_index_creates(
    p: str, instance: L2Instance, dialect: Dialect,
) -> str:
    """``CREATE INDEX`` lines for the L2FT metadata cascade.

    One functional index per declared metadata key, on
    ``JSON_VALUE(metadata, '$.<key>')``. Speeds up the
    ``l2ft-postings-dataset`` filter
    (``WHERE JSON_VALUE(metadata, '$.<key>') IN (<<$pValues>>)``).

    Dialect note: Postgres requires double parens for expression
    indexes (``ON tbl ((expr))``) and accepts the bare functional
    index without further machinery. Oracle 19c rejects the same
    shape at INSERT time with ``ORA-40845: failed to create object
    (qjsn:engine)`` — its JSON Search Context Engine needs either
    a JSON Search Index or an explicit ``RETURNING VARCHAR2(N)``
    clause to evaluate the indexed expression deterministically
    (and even with the RETURNING clause the bare functional index
    appears unsupported in 19c). For now, Oracle skips the metadata
    indexes entirely; queries fall back to a sequential scan on
    ``metadata``. The L2FT cascade still works, just slower.

    Returns an empty string when the L2 declares no metadata keys
    (most spec-example-shaped instances) so the placeholder collapses
    cleanly in the template.
    """
    keys = _declared_metadata_keys(instance)
    if not keys:
        return ""
    if dialect is not Dialect.POSTGRES:
        # Oracle 19c — skip; see docstring rationale.
        return ""
    lines: list[str] = [
        "-- Functional indexes on JSON_VALUE(metadata, '$.<key>') —",
        "-- one per L2-declared metadata key. Speeds up the L2FT",
        "-- metadata cascade WHERE clause (postings dataset).",
    ]
    for k in keys:
        idx = _metadata_index_name(p, k)
        path = f"$.{k}"
        lines.append(
            f"CREATE INDEX {idx} ON {p}_transactions "
            f"((JSON_VALUE(metadata, '{path}')));"
        )
    return "\n".join(lines) + "\n"


def _emit_metadata_index_drops(
    p: str, instance: L2Instance, dialect: Dialect,
) -> str:
    """``DROP INDEX IF EXISTS`` lines paired with the CREATE block.

    Co-located so the create + drop name list stays in sync — adding
    a metadata key to an L2 rail YAML auto-extends both, no static
    drop tuple to maintain. Oracle skips both halves (see
    ``_emit_metadata_index_creates`` rationale).
    """
    keys = _declared_metadata_keys(instance)
    if not keys:
        return ""
    if dialect is not Dialect.POSTGRES:
        # Oracle 19c — index never created, nothing to drop.
        return ""
    lines = [
        drop_index_if_exists(_metadata_index_name(p, k), dialect)
        for k in keys
    ]
    return "\n".join(lines) + "\n"


def _emit_base_schema(
    p: str, dialect: Dialect, instance: L2Instance,
) -> str:
    """Render ``_SCHEMA_TEMPLATE`` with all dialect placeholders filled.

    Type-name placeholders ({serial}, {ts}, {text}, {vc20…vc255},
    {bigint_money}) come from common/sql type helpers. DROP placeholders
    come from drop_*_if_exists helpers (PG IF EXISTS / Oracle PL/SQL).
    The bundler-eligibility partial-index ``WHERE bundle_id IS NULL``
    is a Postgres-only optimization — Oracle gets a full index, which
    works correctly but is larger; converting to a function-based
    index for parity is a future optimization.

    ``instance`` is threaded for the L2FT JSON-cascade functional
    indexes — one per declared metadata key, scoped by the L2's
    ``rail.metadata_keys`` declarations.
    """
    fmt: dict[str, str] = {
        "p": p,
        # Type names
        "serial": serial_type(dialect),
        # P.9a — single TZ-naive TIMESTAMP type across both dialects;
        # the prior {ts} (TIMESTAMPTZ / TIMESTAMP WITH TIME ZONE)
        # + {ts} (TIMESTAMPTZ on PG, TIMESTAMP on Oracle for PK
        # eligibility) split was removed. Timezone normalization is
        # the integrator's contract — see Schema_v6.md.
        "ts": timestamp_type(dialect),
        "text": text_type(dialect),
        "json_text": json_text_type(dialect),
        "vc20": varchar_type(20, dialect),
        "vc50": varchar_type(50, dialect),
        "vc100": varchar_type(100, dialect),
        "vc255": varchar_type(255, dialect),
        # AO.1: money columns are BIGINT cents (was DECIMAL(20,2)) so
        # SQLite stores them as exact INTEGER (no REAL float dust).
        "bigint_money": bigint_type(dialect),
        # Matview options suffix (Oracle BUILD IMMEDIATE REFRESH COMPLETE
        # ON DEMAND; empty on Postgres + SQLite).
        "matview_options": matview_options(dialect),
        # Matview CREATE keyword — PG/Oracle ``CREATE MATERIALIZED VIEW``,
        # SQLite ``CREATE TABLE`` (matviews land as plain tables).
        "matview_create_kw": matview_create_keyword(dialect),
        # JSON validity constraint — PG/Oracle ``IS JSON``,
        # SQLite ``json_valid()``.
        "metadata_json_check": json_check("metadata", dialect),
        # AV (2026-05-23): daily_balances.limits → daily_balances.metadata.
        # The CHECK shape is identical to the transactions.metadata
        # check; both columns share the same JSON-validity contract
        # post-rename.
        "db_metadata_json_check": json_check("metadata", dialect),
        # Per-table ``entry`` column declaration. PG gets
        # ``BIGSERIAL NOT NULL`` (auto-incrementing). Oracle gets
        # ``NUMBER GENERATED ALWAYS AS IDENTITY NOT NULL``. SQLite
        # gets ``INTEGER PRIMARY KEY AUTOINCREMENT`` — single-column
        # PK is the only place SQLite supports auto-increment, so the
        # composite ``(id, entry)`` PG/Oracle PK collapses to a
        # ``UNIQUE`` constraint on SQLite (see ``tx_pk_decl`` /
        # ``db_pk_decl`` below).
        "tx_entry_decl": _entry_column_decl(dialect),
        "db_entry_decl": _entry_column_decl(dialect),
        # Per-table PK / UNIQUE declaration. PG/Oracle declare a
        # composite ``PRIMARY KEY (id, entry)`` /
        # ``PRIMARY KEY (account_id, business_day_start, entry)``.
        # SQLite already has a single-column PK on ``entry`` (the
        # AUTOINCREMENT half), so the composite shifts to ``UNIQUE``.
        "tx_pk_decl": _pk_decl(("id", "entry"), dialect),
        "db_pk_decl": _pk_decl(
            ("account_id", "business_day_start", "entry"), dialect,
        ),
        # Bundler index declaration — PG only emits a partial-WHERE
        # `(rail_name, status) WHERE bundle_id IS NULL` index that's
        # distinct from the full `(rail_name, status)` rail_status
        # index above. Oracle + SQLite (no partial-index support) get
        # nothing here (the full rail_status index above covers the
        # same lookup; ORA-01408 fires if we emit a duplicate). Z.B
        # (2026-05-15) made this matter — pre-Z.B the rail_status
        # index keyed on `transfer_type`, so the bundler index's
        # `(rail_name, status)` was unique on every dialect.
        "bundler_index_decl": (
            "CREATE INDEX idx_{p}_transactions_bundler_eligibility\n"
            "    ON {p}_transactions (rail_name, status)"
            "\n    WHERE bundle_id IS NULL;"
        ).replace("{p}", p) if dialect is Dialect.POSTGRES else (
            "-- Bundler index skipped on this dialect — see comment above."
        ),
        # Current* matview drops.
        "drop_curr_db": drop_matview_if_exists(
            f"{p}_current_daily_balances", dialect,
        ),
        "drop_curr_tx": drop_matview_if_exists(
            f"{p}_current_transactions", dialect,
        ),
        # Base table drops.
        "drop_table_db": drop_table_if_exists(
            f"{p}_daily_balances", dialect,
        ),
        "drop_table_tx": drop_table_if_exists(
            f"{p}_transactions", dialect,
        ),
    }
    # Index drops — name-template substitution for the prefix.
    for key, name_template in _BASE_INDEX_DROPS:
        fmt[key] = drop_index_if_exists(name_template.format(p=p), dialect)
    # Per-L2-key metadata functional indexes (drops + creates). When
    # the L2 declares no metadata keys, both placeholders collapse
    # to empty strings so the template stays well-formed.
    fmt["drop_metadata_indexes"] = _emit_metadata_index_drops(p, instance, dialect)
    fmt["metadata_indexes"] = _emit_metadata_index_creates(p, instance, dialect)
    return _SCHEMA_TEMPLATE.format(**fmt)


def _entry_column_decl(dialect: Dialect) -> str:
    """Per-dialect declaration for the ``entry`` column.

    PG: ``BIGSERIAL NOT NULL``. Oracle:
    ``NUMBER GENERATED ALWAYS AS IDENTITY NOT NULL``. SQLite:
    ``INTEGER PRIMARY KEY AUTOINCREMENT`` — SQLite only supports
    auto-increment on a single-column ``INTEGER PRIMARY KEY``, so the
    composite-PK shape PG/Oracle use can't apply. The composite
    ``(id, entry)`` uniqueness invariant gets enforced via a
    ``UNIQUE`` constraint instead (see ``_pk_decl``).
    """
    if dialect is Dialect.SQLITE:
        return "INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"{serial_type(dialect)}      NOT NULL"


def _pk_decl(cols: tuple[str, ...], dialect: Dialect) -> str:
    """Per-dialect PK / UNIQUE declaration for the base tables.

    PG/Oracle: ``PRIMARY KEY (cols)`` — composite key including
    ``entry`` per the L1 supersession contract. SQLite: ``UNIQUE
    (cols)`` — the ``entry`` column already carries the table's
    PRIMARY KEY (single-column AUTOINCREMENT, see
    ``_entry_column_decl``), so the composite uniqueness shifts to a
    UNIQUE constraint while preserving the same invariant.
    """
    cols_sql = ", ".join(cols)
    if dialect is Dialect.SQLITE:
        return f"UNIQUE ({cols_sql})"
    return f"PRIMARY KEY ({cols_sql})"


_SCHEMA_TEMPLATE = """\
-- =====================================================================
-- L2 instance: {p}
-- Generated by recon_gen.common.l2.schema.emit_schema
-- =====================================================================

-- Drop views first (they depend on the base tables). M.1a.9 made
-- these MATERIALIZED VIEWs.
{drop_curr_db}
{drop_curr_tx}
{drop_idx_account_posting}
{drop_idx_transfer}
{drop_idx_type_status}
{drop_idx_business_day}
{drop_idx_parent}
{drop_idx_bundler}
{drop_idx_db_business_day}
{drop_metadata_indexes}{drop_table_db}
{drop_table_tx}

-- ---------------------------------------------------------------------
-- L1 Transaction (denormalized with Transfer + Account fields per
-- Implementation Entities: StoredTransaction = Transaction + Transfer,
-- with Account fields also denormalized onto each leg).
--
-- entry         — BIGSERIAL append-only supersession key per L1 F's
--                 Entry primitive. Higher entry overrides lower for the
--                 same logical Transaction.id (Current* view in M.1.5).
-- amount_money  — signed BIGINT cents per L1 Amount's "money agrees
--                 with direction" invariant. Positive ⇔ Credit; negative
--                 ⇔ Debit. The CHECK enforces sign-direction agreement.
--                 AO.1: storage moved from DECIMAL(20,2) to BIGINT cents
--                 (SQLite stored DECIMAL as REAL → float dust). Dollars
--                 projection happens at read time via cents_to_dollars_sql.
-- transfer_parent_id — L1 Transfer.Parent recursive chain (the PR
--                 pipeline support added in Phase L's L1 spec work).
-- rail_name     — L2 Rail name that produced this leg. Required on every
--                 row so the bundler's eligibility query (M.1a / SPEC's
--                 BundleSelector RailName form) can filter without an
--                 expensive transfer→rail lookup. Denormalized at write
--                 time by integrator ETL.
-- template_name — L2 TransferTemplate name this leg belongs to (NULL for
--                 standalone-rail postings). Combined with rail_name
--                 this lets the bundler's "TransferTemplateName" and
--                 "TransferTemplateName.LegRailName" BundleSelector
--                 forms resolve to simple WHERE clauses.
-- bundle_id     — L1 Transaction.BundleId. Populated by AggregatingRail
--                 bundlers via a higher-Entry row (Supersedes =
--                 BundleAssignment); NULL on first-entry rows.
-- supersedes    — L1 Transaction.Supersedes; open enum per SPEC
--                 (no CHECK). Set on higher-Entry rows that supersede
--                 a prior row of the same id; NULL on first-entry rows.
--                 v1 categories: Inflight / BundleAssignment /
--                 TechnicalCorrection (see SPEC's "Higher-Entry rows"
--                 section for which category applies when).
-- origin        — open enum, no CHECK; integrators may extend.
-- metadata      — bounded VARCHAR(4000) / VARCHAR2(4000) + IS JSON
--                 (portability constraint: no JSONB, no GIN indexes;
--                 SQL/JSON path syntax for extraction). Bounded so the
--                 column behaves like a string on both dialects (Oracle
--                 CLOB rejects MIN/MAX/GROUP BY/ORDER BY/IN with
--                 ORA-00932); 4000 chars covers every JSON metadata
--                 document the L2 schema emits.
-- ---------------------------------------------------------------------
    -- BC.11 re-applied (2026-05-25): id / transfer_id / transfer_parent_id
    -- / bundle_id widened vc100 → vc255. The chain-completion plant
    -- adapter synthesizes IDs by concatenating parent IDs + rail names
    -- + account IDs (sasquatch_pr's CustomerInboundACHReturnNSF +
    -- tx-chainfill-xfer-limit-breach-... pattern hits 101 chars and
    -- Oracle rejects with ORA-12899). The original BC.11 fix was
    -- reverted at some point; this re-applies it. vc255 is the
    -- standard practical ceiling; widening is free (PG/Oracle/SQLite
    -- all handle it).
CREATE TABLE {p}_transactions (
    entry                {tx_entry_decl},
    id                   {vc255}   NOT NULL,
    account_id           {vc100}   NOT NULL,
    account_name         {vc255},
    account_role         {vc100},
    account_scope        {vc20}    NOT NULL
        CHECK (account_scope IN ('internal', 'external')),
    account_parent_role  {vc100},
    amount_money         {bigint_money}  NOT NULL,
    amount_direction     {vc20}    NOT NULL
        CHECK (amount_direction IN ('Debit', 'Credit')),
    status               {vc50}    NOT NULL,
    posting              {ts}    NOT NULL,
    transfer_id          {vc255}   NOT NULL,
    transfer_completion  {ts},
    transfer_parent_id   {vc255},
    rail_name            {vc100}   NOT NULL,
    template_name        {vc100},
    bundle_id            {vc255},
    supersedes           {vc50},
    origin               {vc50}    NOT NULL,
    metadata             {json_text},
    {tx_pk_decl},
    -- Sign-direction agreement (L1 Amount INVARIANT):
    --   money ≥ 0 if direction = Credit; money ≤ 0 if direction = Debit.
    CHECK (
        (amount_direction = 'Credit' AND amount_money >= 0)
        OR
        (amount_direction = 'Debit'  AND amount_money <= 0)
    ),
    {metadata_json_check}
);

-- ---------------------------------------------------------------------
-- L1 StoredBalance (denormalized with Account fields per Implementation
-- Entities: DailyBalance = StoredBalance + Account).
--
-- entry         — same supersession semantic as transactions.entry.
--                 Highest entry per (account_id, business_day_start)
--                 wins; older entries stay for audit.
-- expected_eod_balance — L1 ExpectedEODBalance. NULL means "no
--                 expectation" (the constraint doesn't apply).
-- metadata      — open JSON TEXT, symmetric with transactions.metadata.
--                 AV (2026-05-23) renamed from ``limits`` and demoted
--                 the per-rail caps to a nested ``metadata.limits``
--                 key so the column has room for siblings (scenario_id
--                 per AV.5, future per-day tags). Per-rail caps still
--                 carry the same JSON-map shape (rail_name -> cap)
--                 under the ``limits`` key; the integrator's ETL just
--                 wraps the same map one level deeper.
--                 NULL means no metadata on this account-day.
-- money         — signed BIGINT cents; CAN go negative (overdraft is
--                 observable per L1's Non-negative Stored Balance SHOULD
--                 constraint). AO.1: see amount_money comment above.
-- expected_eod_balance — BIGINT cents; NULL when no expectation set.
-- supersedes    — L1 StoredBalance.Supersedes; open enum per SPEC
--                 (no CHECK). Per the SPEC's "Higher-Entry rows"
--                 section, the only category applicable to StoredBalance
--                 is TechnicalCorrection — snapshots have no Pending
--                 lifecycle and aren't bundled. Any higher-Entry
--                 daily_balances row is by construction a correction.
-- ---------------------------------------------------------------------
CREATE TABLE {p}_daily_balances (
    entry                  {db_entry_decl},
    account_id             {vc100}   NOT NULL,
    account_name           {vc255},
    account_role           {vc100},
    account_scope          {vc20}    NOT NULL
        CHECK (account_scope IN ('internal', 'external')),
    account_parent_role    {vc100},
    expected_eod_balance   {bigint_money},
    business_day_start     {ts}    NOT NULL,
    business_day_end       {ts}    NOT NULL,
    money                  {bigint_money}  NOT NULL,
    metadata               {json_text},
    supersedes             {vc50},
    {db_pk_decl},
    CHECK (business_day_end > business_day_start),
    {db_metadata_json_check}
);

-- B-tree indexes for the dashboard's hot-path queries. No GIN on
-- TEXT/JSON columns per the SPEC's portability constraint.
CREATE INDEX idx_{p}_transactions_account_posting ON {p}_transactions (account_id, posting);
CREATE INDEX idx_{p}_transactions_transfer        ON {p}_transactions (transfer_id);
CREATE INDEX idx_{p}_transactions_rail_status     ON {p}_transactions (rail_name, status);
CREATE INDEX idx_{p}_transactions_parent          ON {p}_transactions (transfer_parent_id);
-- Bundler eligibility: AggregatingRails query for Posted, unbundled rows
-- by rail_name (matching their BundlesActivity selectors). Partial index
-- on `bundle_id IS NULL` keeps the index small as bundled-row count grows.
--
-- Z.B (2026-05-15): the column list `(rail_name, status)` now matches
-- idx_{p}_transactions_rail_status above (pre-Z.B the rail_status index
-- keyed on `transfer_type`, so the bundler-specific `(rail_name, status)`
-- was distinct). On dialects without partial-index support (Oracle,
-- SQLite < 3.8) the bundler index is degenerate — emit it ONLY when the
-- partial WHERE is non-empty (PG), otherwise the rail_status index above
-- covers the lookup (without the small-index optimization).
{bundler_index_decl}
-- V.3 — Standalone single-column posting index. The composite
-- (account_id, posting) above is account-leading, so MAX(posting)
-- against the whole table can't single-leaf scan it; the planner
-- has to read the last entry per account_id. The standalone index
-- gives the App Info "latest data" KPI a sub-millisecond MAX scan
-- on prod-scale tables.
CREATE INDEX idx_{p}_transactions_posting          ON {p}_transactions (posting);
CREATE INDEX idx_{p}_daily_balances_business_day  ON {p}_daily_balances (business_day_start);
{metadata_indexes}

-- ---------------------------------------------------------------------
-- Current* views (M.1.5) — materialize the L1 ``CurrentTransaction`` /
-- ``CurrentStoredBalance`` theorems as max-Entry-per-logical-key over
-- the base tables. Per the SPEC's set-comprehension definitions:
--
--   CurrentTransaction := {{ tx ∈ Transaction :
--     tx.Entry = max(Transaction(ID = tx.ID).Entry) }}
--   CurrentStoredBalance := {{ sb ∈ StoredBalance :
--     sb.Entry = max(StoredBalance(Account = sb.Account,
--                                  BusinessDay = sb.BusinessDay).Entry) }}
--
-- The dashboard SQL targets these views, NOT the base tables — that way
-- Entry-supersession (technical-error correction per L1's Immutability
-- principle) is transparent to dashboard consumers. A wrong row stays
-- visible in the base table for audit; the view returns the corrected one.
-- ---------------------------------------------------------------------
-- M.1a.9 — Materialized to eliminate the per-row correlated subquery
-- that every downstream view + dataset SQL pays through. Refresh
-- contract: integrators MUST `REFRESH MATERIALIZED VIEW` after every
-- batch insert into the base tables. The library ships
-- `refresh_matviews_sql(instance)` that emits the right REFRESH order.
{matview_create_kw} {p}_current_transactions{matview_options} AS
SELECT * FROM {p}_transactions tx
WHERE tx.entry = (
    SELECT MAX(entry) FROM {p}_transactions WHERE id = tx.id
);
-- Indexes targeting the dashboard's hot-path filters: per-account
-- date range (Daily Statement detail, Transactions sheet), per-transfer
-- (drill chain), per-status (filter dropdowns).
CREATE INDEX idx_{p}_curr_tx_account_posting
    ON {p}_current_transactions (account_id, posting);
CREATE INDEX idx_{p}_curr_tx_transfer ON {p}_current_transactions (transfer_id);
CREATE INDEX idx_{p}_curr_tx_id ON {p}_current_transactions (id);
CREATE INDEX idx_{p}_curr_tx_status ON {p}_current_transactions (status);
-- v8.5.6: date-leading composite for the Transactions sheet's filter
-- dropdown. Z.B (2026-05-15) renamed transfer_type → rail_name under the
-- symmetric collapse; this index moved with it. The dropdown's
-- ``SELECT DISTINCT rail_name WHERE posting BETWEEN start AND end``
-- query had no useful index — full scan of the matview, visible as a
-- spinning dropdown. Mirrors the v8.4.0 Drift dropdown fix
-- (``idx_<prefix>_drift_day_account_role``). Other per-sheet dropdowns
-- (account / transfer / status / origin) either already have an index
-- or land in a small enough cardinality bucket; revisit if the next
-- round of testing flags more.
CREATE INDEX idx_{p}_curr_tx_posting_rail_name
    ON {p}_current_transactions (posting, rail_name);
-- v8.6.8: tt-instances dataset SQL JOINs ``current_transactions`` ON
-- ``ct.template_name = t.template_name`` and runs EXISTS subqueries
-- keyed on ``transfer_parent_id`` for chain-child detection. Without
-- these, the L2FT Transfer Templates sheet's Template + Completion
-- dropdowns (which run DISTINCT against the tt-instances CTE) burn
-- through full matview scans per pick. Mirrors the v8.5.6 transfer_type
-- dropdown fix one layer further into the L2FT explorer.
CREATE INDEX idx_{p}_curr_tx_template_name
    ON {p}_current_transactions (template_name);
CREATE INDEX idx_{p}_curr_tx_parent
    ON {p}_current_transactions (transfer_parent_id);

{matview_create_kw} {p}_current_daily_balances{matview_options} AS
SELECT * FROM {p}_daily_balances sb
WHERE sb.entry = (
    SELECT MAX(entry)
    FROM {p}_daily_balances
    WHERE account_id = sb.account_id
      AND business_day_start = sb.business_day_start
);
-- Composite index covers (account_id, business_day_start) which every
-- downstream view JOINs / filters on. Scope index covers the WHERE
-- account_scope = 'internal' filter common in L1 invariants.
CREATE INDEX idx_{p}_curr_db_account_day
    ON {p}_current_daily_balances (account_id, business_day_start);
CREATE INDEX idx_{p}_curr_db_scope_day
    ON {p}_current_daily_balances (account_scope, business_day_start);
"""


# -- L1 invariant views (M.1a.7) ---------------------------------------------
#
# Per L2 instance, materialize the SPEC's L1 SHOULD-constraints as queryable
# exception surfaces. Each view's rows ARE the constraint violations:
# `<prefix>_drift` returns leaf-account-day cells where stored ≠ computed,
# `<prefix>_overdraft` returns rows where money < 0, etc. Dashboards
# (M.2.4 + later) just SELECT from these views — the L1 invariant SQL
# lives once per instance, not duplicated per app.
#
# All views read from the Current* views (M.1.5) so technical-error
# supersession is transparent. Drop order is reverse of create order
# (no view here depends on another in this block, but ordering is
# conservative).
# L1 invariant matview names in drop order: dashboard-shape matviews
# (todays_exceptions, daily_statement_summary) drop FIRST because they
# read from the L1 invariant matviews (which read from current_* +
# computed_*). The two helper matviews (computed_ledger_balance,
# computed_subledger_balance) drop last.
_L1_INVARIANT_DROP_NAMES: tuple[str, ...] = (
    "todays_exceptions",
    "daily_statement_summary",
    "multi_xor_violation",
    "fan_in_disagreement",
    "transfer_parents",
    "xor_group_violation",
    "chain_parent_disagreement",
    "stuck_unbundled",
    "stuck_pending",
    "limit_breach",
    "expected_eod_balance_breach",
    "overdraft",
    "ledger_drift",
    "drift",
    "computed_ledger_balance",
    "computed_subledger_balance",
)


_L1_INVARIANT_DROPS_HEADER = """\
-- L1 invariant view drops (M.1a.7 + M.1a.9) — MUST run before base
-- drops because the L1 views depend on the Current* matviews (which
-- depend on the base tables). Re-emitted at the top of the script so
-- re-runs converge. M.1a.9 made these MATERIALIZED VIEWs.
--
-- Drop order: dashboard-shape matviews (todays_exceptions,
-- daily_statement_summary) drop FIRST because they read from the L1
-- invariant matviews (which read from current_* + computed_*).
--
-- Migration note: pre-M.1a.9 these were regular VIEWs; the very first
-- M.1a.9 deploy on a stale instance needs to manually
-- `DROP VIEW IF EXISTS <name>;` for each before running the script
-- (PostgreSQL refuses `DROP MATERIALIZED VIEW` on a regular VIEW).
-- Steady state (post-migration) the matview-only DROP suffices."""


def _emit_l1_invariant_drops(p: str, dialect: Dialect) -> str:
    """Emit the L1 invariant matview DROP block per dialect.

    Postgres uses native ``DROP MATERIALIZED VIEW IF EXISTS``; Oracle
    uses a PL/SQL block per drop that swallows ORA-12003 / ORA-00942.
    Order is fixed by ``_L1_INVARIANT_DROP_NAMES`` (dashboard-shape
    first, helpers last).
    """
    drops = "\n".join(
        drop_matview_if_exists(f"{p}_{name}", dialect)
        for name in _L1_INVARIANT_DROP_NAMES
    )
    return f"{_L1_INVARIANT_DROPS_HEADER}\n{drops}\n"


# BC.12 typed projection views ------------------------------------------------
#
# Pay-as-you-go: only the two views that an existing matview consumes.
# Future L2-consuming matviews add their own typed view alongside the
# matview that needs it — no speculative views.
_TYPED_CONFIG_VIEW_NAMES: tuple[str, ...] = (
    "v_config_rails",
    "v_config_limit_schedules",
)


def _emit_typed_config_view_drops(p: str, dialect: Dialect) -> str:
    """``DROP VIEW IF EXISTS`` for every BC.12 typed projection view.

    Drop order: matviews that JOIN these views are dropped earlier (by
    ``_emit_l1_invariant_drops``), so dropping the views here is safe;
    no remaining dependents.
    """
    return "\n".join(
        drop_view_if_exists(f"{p}_{name}", dialect)
        for name in _TYPED_CONFIG_VIEW_NAMES
    ) + "\n"


def _emit_typed_config_view_creates(p: str, dialect: Dialect) -> str:
    """Emit the BC.12 typed projection views.

    Each view body is a plain self-join over ``<prefix>_config_kv``:
    anchor at the top-level container row (``parent_id IS NULL, key=
    'l2_yaml'``), descend one level to the named-array container,
    descend again to each object element, then to each field. The
    aggregate ``MAX(CASE WHEN field.key = '<X>' THEN <lob_substr>(value)
    END)`` pivots multi-field rows into one row per element.

    No JSON_TABLE anywhere. The matview engine on Oracle 19c+ sees a
    relational source (the kv table is relational; the self-joins
    yield relational rows), so matviews JOINing these views build
    cleanly without ORA-32368.

    ``lob_substr`` coerces the CLOB ``value`` column to VARCHAR2(n)
    inside the aggregate — Oracle's MAX rejects bare CLOB (ORA-22849).
    PG + SQLite resolve ``lob_substr`` to a plain SUBSTRING / SUBSTR
    so the same view body works on all dialects.

    See ``docs/audits/bc_12_config_kv_spike.md`` for the spike that
    locked these shapes against Oracle 23 (the local test container)
    and confirmed matview build-time compatibility.
    """
    return _render_v_config_rails(p, dialect) + "\n" + _render_v_config_limit_schedules(p, dialect)


def _pivot_field(
    field_key: str, *, project_n: int, cast_to: str | None, dialect: Dialect,
) -> str:
    """Render a ``MAX(CASE WHEN field.key = '<X>' THEN lob_substr(...) END)``
    pivot expression (optionally CAST to a target type).

    The aggregator collapses the multi-row "fields of one element"
    pattern into a single row per element-container; ``lob_substr``
    coerces CLOB→VARCHAR2 on Oracle so ``MAX`` accepts the value
    (ORA-22849 otherwise).
    """
    inner = (
        f"MAX(CASE WHEN field.key = '{field_key}' "
        f"THEN {lob_substr('field.value', project_n, dialect)} END)"
    )
    if cast_to is None:
        return inner
    return cast(inner, cast_to, dialect)


def _render_v_config_rails(p: str, dialect: Dialect) -> str:
    """``<prefix>_v_config_rails`` — one row per L2 rail with the
    matview-consumed scalar fields projected to typed columns.

    Columns: ``name VARCHAR(100)``, ``max_pending_age_seconds BIGINT``,
    ``max_unbundled_age_seconds BIGINT``. Per-row source: each element
    of the L2's ``rails`` array.

    Walk:
    1. ``root.key='l2_yaml' AND root.parent_id IS NULL`` — the L2 tree
       container.
    2. ``rails_arr.parent_id = root.node_id AND rails_arr.key='rails'`` —
       the rails-array container.
    3. ``rail_obj.parent_id = rails_arr.node_id`` — each array element
       (key is the stringified index, value is NULL).
    4. ``field.parent_id = rail_obj.node_id`` — each field on the rail
       object; ``key`` distinguishes ``name`` / ``max_pending_age_seconds``
       / ``max_unbundled_age_seconds``.
    """
    name_col = _pivot_field("name", project_n=100, cast_to=None, dialect=dialect)
    pending_col = _pivot_field(
        "max_pending_age_seconds", project_n=100, cast_to="bigint", dialect=dialect,
    )
    unbundled_col = _pivot_field(
        "max_unbundled_age_seconds", project_n=100, cast_to="bigint", dialect=dialect,
    )
    return (
        f"CREATE VIEW {p}_v_config_rails AS\n"
        f"SELECT\n"
        f"  {name_col} AS name,\n"
        f"  {pending_col} AS max_pending_age_seconds,\n"
        f"  {unbundled_col} AS max_unbundled_age_seconds\n"
        f"FROM {p}_config_kv root\n"
        f"JOIN {p}_config_kv rails_arr\n"
        f"  ON rails_arr.parent_id = root.node_id\n"
        f" AND rails_arr.key = 'rails'\n"
        f"JOIN {p}_config_kv rail_obj\n"
        f"  ON rail_obj.parent_id = rails_arr.node_id\n"
        f"JOIN {p}_config_kv field\n"
        f"  ON field.parent_id = rail_obj.node_id\n"
        f"WHERE root.parent_id IS NULL\n"
        f"  AND root.key = 'l2_yaml'\n"
        f"GROUP BY rail_obj.node_id;\n"
    )


def _render_v_config_limit_schedules(p: str, dialect: Dialect) -> str:
    """``<prefix>_v_config_limit_schedules`` — one row per L2 limit
    schedule entry with the matview-consumed scalar fields projected
    to typed columns.

    Columns: ``parent_role VARCHAR(100)``, ``rail VARCHAR(100)``,
    ``direction VARCHAR(20)``, ``cap NUMERIC``.

    Walk shape mirrors ``v_config_rails`` but rooted on the
    ``limit_schedules`` top-level array.
    """
    parent_col = _pivot_field(
        "parent_role", project_n=100, cast_to=None, dialect=dialect,
    )
    rail_col = _pivot_field(
        "rail", project_n=100, cast_to=None, dialect=dialect,
    )
    direction_col = _pivot_field(
        "direction", project_n=20, cast_to=None, dialect=dialect,
    )
    cap_col = _pivot_field(
        "cap", project_n=100, cast_to="numeric", dialect=dialect,
    )
    return (
        f"CREATE VIEW {p}_v_config_limit_schedules AS\n"
        f"SELECT\n"
        f"  {parent_col} AS parent_role,\n"
        f"  {rail_col} AS rail,\n"
        f"  {direction_col} AS direction,\n"
        f"  {cap_col} AS cap\n"
        f"FROM {p}_config_kv root\n"
        f"JOIN {p}_config_kv ls_arr\n"
        f"  ON ls_arr.parent_id = root.node_id\n"
        f" AND ls_arr.key = 'limit_schedules'\n"
        f"JOIN {p}_config_kv ls_obj\n"
        f"  ON ls_obj.parent_id = ls_arr.node_id\n"
        f"JOIN {p}_config_kv field\n"
        f"  ON field.parent_id = ls_obj.node_id\n"
        f"WHERE root.parent_id IS NULL\n"
        f"  AND root.key = 'l2_yaml'\n"
        f"GROUP BY ls_obj.node_id;\n"
    )


_L1_INVARIANT_VIEWS_TEMPLATE = """\
-- L1 invariant views per M.1a.7 (one set per L2 instance) ------------------
-- (DROPs moved to the top of the script so they run before the base
-- DROPs that would otherwise hit "dependent objects still exist".)

-- ---------------------------------------------------------------------
-- Helper view: ComputedBalance theorem for leaf accounts.
-- Per SPEC: ComputedBalance(account, businessDay) := Σ CurrentTransaction
-- (Account = inAccount, Status = Posted, Posting ≤ inBusinessDay.EndTime).
-- A "leaf" account is one with account_parent_role IS NOT NULL
-- (i.e., it's a child of a parent role).
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_computed_subledger_balance{matview_options} AS
SELECT
    sb.account_id,
    sb.business_day_start,
    sb.business_day_end,
    sb.account_parent_role,
    COALESCE((
        SELECT SUM(tx.amount_money)
        FROM {p}_current_transactions tx
        WHERE tx.account_id = sb.account_id
          AND tx.status = 'Posted'
          AND tx.posting <= sb.business_day_end
    ), 0) AS computed_balance
FROM {p}_current_daily_balances sb
WHERE sb.account_scope = 'internal'
  AND sb.account_parent_role IS NOT NULL;
-- JOIN key with current_daily_balances + drift's WHERE filter.
CREATE INDEX idx_{p}_csb_account_day
    ON {p}_computed_subledger_balance (account_id, business_day_start);

-- ---------------------------------------------------------------------
-- Helper view: ComputedBalance theorem for parent (ledger) accounts.
-- Per SPEC's LedgerDrift: stored ledger balance should equal
--   Σ child sub-ledger stored balances + Σ direct ledger postings.
-- A "parent" account is one whose role appears as account_parent_role
-- on at least one other account (resolved via subquery).
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_computed_ledger_balance{matview_options} AS
SELECT
    parent_db.account_id,
    parent_db.account_role,
    parent_db.business_day_start,
    parent_db.business_day_end,
    COALESCE(child_totals.child_balance, 0)
        + COALESCE((
            SELECT SUM(tx.amount_money)
            FROM {p}_current_transactions tx
            WHERE tx.account_id = parent_db.account_id
              AND tx.status = 'Posted'
              AND tx.posting <= parent_db.business_day_end
        ), 0) AS computed_balance
FROM {p}_current_daily_balances parent_db
LEFT JOIN (
    SELECT
        child_db.account_parent_role AS parent_role,
        child_db.business_day_start,
        SUM(child_db.money) AS child_balance
    FROM {p}_current_daily_balances child_db
    WHERE child_db.account_parent_role IS NOT NULL
    GROUP BY child_db.account_parent_role, child_db.business_day_start
) child_totals
    ON child_totals.parent_role = parent_db.account_role
   AND child_totals.business_day_start = parent_db.business_day_start
WHERE parent_db.account_scope = 'internal'
  AND parent_db.account_role IS NOT NULL
  -- Only emit for accounts whose role IS a parent role to some child.
  AND EXISTS (
      SELECT 1 FROM {p}_current_daily_balances child2
      WHERE child2.account_parent_role = parent_db.account_role
  );
-- JOIN key with current_daily_balances + ledger_drift's WHERE filter.
CREATE INDEX idx_{p}_clb_account_day
    ON {p}_computed_ledger_balance (account_id, business_day_start);

-- ---------------------------------------------------------------------
-- L1 invariant: Sub-ledger drift.
-- SPEC: For every CurrentStoredBalance where Account.Scope = Internal
-- and ¬IsParent(Account), Drift(Account, BusinessDay) SHOULD equal 0.
-- Rows in this view are the violations: stored ≠ computed.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_drift{matview_options} AS
SELECT
    sb.account_id,
    sb.account_name,
    sb.account_role,
    sb.account_parent_role,
    sb.business_day_start,
    sb.business_day_end,
    sb.money AS stored_balance,
    cb.computed_balance,
    sb.money - cb.computed_balance AS drift
FROM {p}_current_daily_balances sb
JOIN {p}_computed_subledger_balance cb
  ON cb.account_id = sb.account_id
 AND cb.business_day_start = sb.business_day_start
WHERE sb.account_scope = 'internal'
  AND sb.account_parent_role IS NOT NULL
  AND sb.money <> cb.computed_balance;
-- Dashboard hot-path: per-sheet account dropdown + date filter, plus
-- the universal-date-range filter from M.2b.1.
CREATE INDEX idx_{p}_drift_account_day
    ON {p}_drift (account_id, business_day_start);
CREATE INDEX idx_{p}_drift_role ON {p}_drift (account_role);
-- v8.4.0: date-leading composite covers QS's date-narrowed dropdown
-- + table queries. The Drift sheet's account / account_role
-- dropdowns spin on large matviews because the existing
-- account-leading indexes don't optimize ``WHERE business_day_start
-- BETWEEN x AND y`` plans well — the planner has to scan the full
-- account-leading index even when the date window is narrow. This
-- composite makes the planner's date-range scan trivial.
CREATE INDEX idx_{p}_drift_day_account_role
    ON {p}_drift (business_day_start, account_id, account_role);

-- ---------------------------------------------------------------------
-- L1 invariant: Ledger drift.
-- SPEC: For every CurrentStoredBalance where Account.Scope = Internal
-- and IsParent(Account), LedgerDrift(Account, BusinessDay) SHOULD equal 0.
-- Rows in this view are the violations.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_ledger_drift{matview_options} AS
SELECT
    sb.account_id,
    sb.account_name,
    sb.account_role,
    sb.business_day_start,
    sb.business_day_end,
    sb.money AS stored_balance,
    cb.computed_balance,
    sb.money - cb.computed_balance AS drift
FROM {p}_current_daily_balances sb
JOIN {p}_computed_ledger_balance cb
  ON cb.account_id = sb.account_id
 AND cb.business_day_start = sb.business_day_start
WHERE sb.money <> cb.computed_balance;
CREATE INDEX idx_{p}_ledger_drift_account_day
    ON {p}_ledger_drift (account_id, business_day_start);
CREATE INDEX idx_{p}_ledger_drift_role
    ON {p}_ledger_drift (account_role);
-- v8.4.0: parity with _drift's date-leading composite (same
-- spinning-dropdown class).
CREATE INDEX idx_{p}_ledger_drift_day_account_role
    ON {p}_ledger_drift (business_day_start, account_id, account_role);

-- ---------------------------------------------------------------------
-- L1 invariant: Non-negative stored balance.
-- SPEC: For every CurrentStoredBalance, money SHOULD be ≥ 0.
-- Rows in this view are accounts × days where the stored balance is
-- negative (overdraft).
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_overdraft{matview_options} AS
SELECT
    sb.account_id,
    sb.account_name,
    sb.account_role,
    sb.account_parent_role,
    sb.business_day_start,
    sb.business_day_end,
    sb.money AS stored_balance
FROM {p}_current_daily_balances sb
WHERE sb.account_scope = 'internal'
  AND sb.money < 0;
CREATE INDEX idx_{p}_overdraft_account_day
    ON {p}_overdraft (account_id, business_day_start);
CREATE INDEX idx_{p}_overdraft_role ON {p}_overdraft (account_role);

-- ---------------------------------------------------------------------
-- L1 invariant: Expected EOD balance.
-- SPEC: For every CurrentStoredBalance where ExpectedEODBalance is
-- set, money SHOULD equal expected_eod_balance.
-- Rows are violations.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_expected_eod_balance_breach{matview_options} AS
SELECT
    sb.account_id,
    sb.account_name,
    sb.account_role,
    sb.business_day_start,
    sb.business_day_end,
    sb.money AS stored_balance,
    sb.expected_eod_balance,
    sb.money - sb.expected_eod_balance AS variance
FROM {p}_current_daily_balances sb
WHERE sb.expected_eod_balance IS NOT NULL
  AND sb.money <> sb.expected_eod_balance;
CREATE INDEX idx_{p}_eod_breach_account_day
    ON {p}_expected_eod_balance_breach (account_id, business_day_start);

-- ---------------------------------------------------------------------
-- L1 invariant: Per-direction flow cap (Limit breach).
-- SPEC: For every CurrentStoredBalance where Limits is set, for every
-- (Rail, limit, direction) in Limits, for every child Account whose
-- Parent = this account, when direction=Outbound
-- OutboundFlow(child, rail, businessDay) SHOULD be ≤ limit; when
-- direction=Inbound InboundFlow(child, rail, businessDay) SHOULD be
-- ≤ limit.
-- Implementation: UNION ALL of two SELECT branches — one filters
-- amount_direction='Debit' (the Outbound branch, classic per-rail send
-- cap) and one filters amount_direction='Credit' (the Inbound branch,
-- typical AML / structuring threshold). Each branch carries its own
-- direction-filtered cap CASE and an explicit `direction` literal
-- column so downstream consumers (dashboard, audit PDF, agreement
-- test) can distinguish which kind of breach the row represents.
-- Caps come from L2's LimitSchedules — embedded inline as CASE
-- branches at view-emit time (dynamic JSON path lookup isn't portable
-- across our SQL targets). account_parent_role is denormalized on
-- every transaction row in v6, so no JOIN to daily_balances is needed
-- (which also avoids the failure mode where a breach business_day has
-- no enclosing daily_balance row). AB.1 (2026-05-19) added the
-- direction split — pre-AB.1 was an Outbound-only SELECT.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_limit_breach{matview_options} AS
SELECT *
FROM (
    SELECT
        tx.account_id,
        tx.account_name,
        tx.account_role,
        tx.account_parent_role,
        {date_trunc_tx_posting} AS business_day,
        tx.rail_name,
        'Outbound' AS direction,
        SUM(ABS(tx.amount_money)) AS outbound_total,
        MAX({limit_cap_value}) AS cap
    FROM {p}_current_transactions tx
    LEFT JOIN {limit_join_outbound}
    WHERE tx.amount_direction = 'Debit'
      AND tx.status = 'Posted'
      AND tx.account_scope = 'internal'
      AND tx.account_parent_role IS NOT NULL
    GROUP BY
        tx.account_id, tx.account_name, tx.account_role,
        tx.account_parent_role,
        {date_trunc_tx_posting},
        tx.rail_name
    UNION ALL
    SELECT
        tx.account_id,
        tx.account_name,
        tx.account_role,
        tx.account_parent_role,
        {date_trunc_tx_posting} AS business_day,
        tx.rail_name,
        'Inbound' AS direction,
        SUM(ABS(tx.amount_money)) AS outbound_total,
        MAX({limit_cap_value}) AS cap
    FROM {p}_current_transactions tx
    LEFT JOIN {limit_join_inbound}
    WHERE tx.amount_direction = 'Credit'
      AND tx.status = 'Posted'
      AND tx.account_scope = 'internal'
      AND tx.account_parent_role IS NOT NULL
    GROUP BY
        tx.account_id, tx.account_name, tx.account_role,
        tx.account_parent_role,
        {date_trunc_tx_posting},
        tx.rail_name
) flow_with_cap
WHERE cap IS NOT NULL
  AND outbound_total > cap;
CREATE INDEX idx_{p}_lb_account_day
    ON {p}_limit_breach (account_id, business_day);
CREATE INDEX idx_{p}_lb_rail ON {p}_limit_breach (rail_name);
CREATE INDEX idx_{p}_lb_direction ON {p}_limit_breach (direction);

-- ---------------------------------------------------------------------
-- L1 invariant: Stuck Pending (M.2b.8).
-- SPEC-derived: every Rail with `max_pending_age` SHOULD see its legs
-- transition Pending → Posted before `posting + max_pending_age`. Rows
-- here are the violations: `status = 'Pending'` AND posting age exceeds
-- the rail's configured threshold.
--
-- Caps come from L2's per-Rail `max_pending_age`; embedded inline as
-- CASE branches at view-emit time (mirror of limit_breach's pattern,
-- so JSON-path-portable across SQL targets). Rails without a
-- `max_pending_age` get NULL and are excluded by the outer WHERE.
--
-- `max_pending_age_seconds` is the resolved cap in seconds (timedelta
-- → integer). `age_seconds` is the live age at view-refresh time —
-- recomputed each REFRESH; the matview snapshots both numbers so the
-- dashboard can sort by staleness without re-evaluating CURRENT_TIMESTAMP
-- on every visual.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_stuck_pending{matview_options} AS
SELECT * FROM (
    SELECT
        ct.id AS transaction_id,
        ct.account_id,
        ct.account_name,
        ct.account_role,
        ct.account_parent_role,
        ct.transfer_id,
        ct.rail_name,
        ct.amount_money,
        ct.amount_direction,
        ct.posting,
        {pending_age_value} AS max_pending_age_seconds,
        {epoch_age_seconds} AS age_seconds
    FROM {p}_current_transactions ct
    LEFT JOIN {pending_age_join}
    WHERE ct.status = 'Pending'
) tx
WHERE tx.max_pending_age_seconds IS NOT NULL
  AND tx.age_seconds > tx.max_pending_age_seconds;
-- Dashboard hot-path indexes — per-rail filter, per-account dropdown,
-- and the per-transfer drill (via M.2b.7 drill-target filter group).
CREATE INDEX idx_{p}_sp_rail ON {p}_stuck_pending (rail_name);
CREATE INDEX idx_{p}_sp_account ON {p}_stuck_pending (account_id);
CREATE INDEX idx_{p}_sp_transfer ON {p}_stuck_pending (transfer_id);

-- ---------------------------------------------------------------------
-- L1 invariant: Stuck Unbundled (M.2b.9).
-- SPEC-derived: every Rail with `max_unbundled_age` SHOULD see its
-- Posted legs picked up by a bundler before `posting + max_unbundled_age`.
-- Per validator R8, `max_unbundled_age` is only meaningful on rails
-- whose `rail_name` appears in some AggregatingRail's
-- `bundles_activity`. Rows here are the violations: bundle_id IS NULL
-- AND status = 'Posted' AND posting age exceeds the per-rail cap.
--
-- Caps come from L2's per-Rail `max_unbundled_age`; embedded inline as
-- CASE branches at view-emit time (mirror of stuck_pending). Same
-- live-age computation via EXTRACT(EPOCH ...) so analysts can sort by
-- staleness without re-evaluating CURRENT_TIMESTAMP.
--
-- Status filter is `'Posted'` (vs `'Pending'` for stuck_pending) since
-- AggregatingRails only bundle posted legs — a Pending leg isn't
-- "stuck unbundled," it's just "stuck pending." The two views are
-- structurally similar but cover disjoint conditions.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_stuck_unbundled{matview_options} AS
SELECT * FROM (
    SELECT
        ct.id AS transaction_id,
        ct.account_id,
        ct.account_name,
        ct.account_role,
        ct.account_parent_role,
        ct.transfer_id,
        ct.rail_name,
        ct.amount_money,
        ct.amount_direction,
        ct.posting,
        {unbundled_age_value} AS max_unbundled_age_seconds,
        {epoch_age_seconds} AS age_seconds
    FROM {p}_current_transactions ct
    LEFT JOIN {unbundled_age_join}
    WHERE ct.bundle_id IS NULL
      AND ct.status = 'Posted'
) tx
WHERE tx.max_unbundled_age_seconds IS NOT NULL
  AND tx.age_seconds > tx.max_unbundled_age_seconds;
-- Dashboard hot-path indexes — same shape as stuck_pending so the
-- M.2b.11 Unbundled Aging sheet's filter dropdowns hit indexed lookups.
CREATE INDEX idx_{p}_su_rail ON {p}_stuck_unbundled (rail_name);
CREATE INDEX idx_{p}_su_account ON {p}_stuck_unbundled (account_id);
CREATE INDEX idx_{p}_su_transfer ON {p}_stuck_unbundled (transfer_id);

-- ---------------------------------------------------------------------
-- L1 invariant: Chain Parent Disagreement (AB.2.3).
-- SPEC-derived: two-template chains where chain.children=[TemplateB]
-- emit multiple leg_rail firings per child Transfer, each carrying
-- `transfer_parent_id` (the parent firing's id) in its row. The L1
-- invariant: every leg_rail firing of one child Transfer MUST agree on
-- which parent firing it descends from — the Parent is first-firing-wins
-- per gap doc §3, and subsequent disagreement = ETL bug / stale parent
-- reference / cross-cycle contamination. Rows here are the violations:
-- `COUNT(DISTINCT transfer_parent_id) > 1` for a given transfer_id.
--
-- The matview filters to `template_name IS NOT NULL` so rail-as-child
-- chains (which don't have a template-level identity to GROUP BY)
-- don't false-positive into this surface. Status filter excludes
-- 'Failed' legs — a failed leg's metadata is unreliable as a parent
-- claim. `parent_transfer_id_min` / `parent_transfer_id_max` carry
-- sample conflicting values so the analyst can drill into the
-- transactions sheet without re-running the GROUP BY.
--
-- AB.4.4 (2026-05-19): fan_in chain children are legitimately multi-
-- parent by design (N parent firings share one child Transfer — the
-- batched-payout pattern). A template-format placeholder (named
-- chain_parent_disagreement_fan_in_filter) inlines a NOT IN clause
-- excluding fan_in template names; when no chains declare fan_in
-- (pre-AB.4 fixtures), the placeholder resolves to the empty string
-- and behavior matches AB.2.3.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_chain_parent_disagreement{matview_options} AS
SELECT
    tx.transfer_id,
    tx.template_name AS child_template_name,
    MIN({date_trunc_tx_posting}) AS business_day,
    COUNT(DISTINCT tx.transfer_parent_id) AS distinct_parent_count,
    MIN(tx.transfer_parent_id) AS parent_transfer_id_min,
    MAX(tx.transfer_parent_id) AS parent_transfer_id_max
FROM {p}_current_transactions tx
WHERE tx.transfer_parent_id IS NOT NULL
  AND tx.template_name IS NOT NULL
  AND tx.status <> 'Failed'{chain_parent_disagreement_fan_in_filter}
GROUP BY tx.transfer_id, tx.template_name
HAVING COUNT(DISTINCT tx.transfer_parent_id) > 1;
-- Dashboard hot-path indexes — per-transfer drill (Today's Exceptions
-- → Transactions), per-template dropdown (analyst filter), per-day filter.
CREATE INDEX idx_{p}_cpd_transfer ON {p}_chain_parent_disagreement (transfer_id);
CREATE INDEX idx_{p}_cpd_template ON {p}_chain_parent_disagreement (child_template_name);
CREATE INDEX idx_{p}_cpd_day ON {p}_chain_parent_disagreement (business_day);

-- ---------------------------------------------------------------------
-- L1 invariant matview: XOR-group firing-cardinality violations.
-- AB.3.3 — Enforces the runtime side of the rewritten C1: for every
-- (Transfer, TransferTemplate, xor_group_index) tuple, exactly one
-- member of the XOR group SHOULD fire. Violations surface when:
--   - firing_count = 0 (XorVariantMissedFiringPlant): none of the
--     declared variant rails posted — Transfer never closes through
--     its declared Variable path.
--   - firing_count >= 2 (XorVariantOverlapPlant): two or more variants
--     posted for the same Transfer — closure is over-determined; the
--     reconciliation engine can't pick which variant's amount + role
--     to use as the Transfer's net.
--
-- Implementation: the XOR group members come from the L2 declaration
-- (inlined as a VALUES list / SELECT-FROM-DUAL UNION). The matview
-- LEFT JOINs that against `_current_transactions` per Transfer +
-- template + member, so the 0-firings case still surfaces a row.
-- `fired_rails` carries the comma-separated list of rails that fired
-- for analyst drill — empty string when firing_count=0.
--
-- When no template in the L2 declares any `leg_rail_xor_groups`, the
-- body becomes a typed-NULL placeholder with `WHERE 1=0` so the
-- matview parses on all 3 dialects but contributes zero rows.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_xor_group_violation{matview_options} AS
{xor_group_violation_body};
-- Dashboard hot-path indexes — per-transfer drill (Today's Exceptions
-- → Transactions), per-template dropdown filter, per-day filter.
CREATE INDEX idx_{p}_xgv_transfer ON {p}_xor_group_violation (transfer_id);
CREATE INDEX idx_{p}_xgv_template ON {p}_xor_group_violation (template_name);
CREATE INDEX idx_{p}_xgv_day ON {p}_xor_group_violation (business_day);

-- ---------------------------------------------------------------------
-- Derived matview: per-child Transfer parent set (long form).
-- AB.4.3 — Lifts the multi-parent set out of `_current_transactions`
-- so AB.4.7's `_fan_in_disagreement` can JOIN against (child_transfer,
-- parent_transfer) pairs without re-running a DISTINCT scan every
-- refresh. One row per (child_transfer_id, parent_transfer_id) pair;
-- DISTINCT collapses any cross-leg duplicates (multiple leg_rails of
-- one child Transfer claiming the same parent_transfer_id ⇒ one row).
--
-- Per AB.4.0 lock: matview-only storage, no `_transactions` schema
-- change, no ETL contract change. Reads from the existing
-- `transfer_parent_id` top-level column on `_current_transactions`
-- (NOT from JSON metadata — that was the AB.4.3 lock description's
-- outdated phrasing; the column was promoted from JSON to top-level
-- by Schema_v6 well before AB.4 landed).
--
-- Failed legs filtered out for the same reason AB.2.3 filters them:
-- a failed leg's parent claim is unreliable. NULL parent legs (rails
-- that are NOT chain children) filtered out — they're not part of
-- any chain's parent set by construction.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_transfer_parents{matview_options} AS
SELECT DISTINCT
    tx.transfer_id AS child_transfer_id,
    tx.transfer_parent_id AS parent_transfer_id
FROM {p}_current_transactions tx
WHERE tx.transfer_parent_id IS NOT NULL
  AND tx.status <> 'Failed';
-- Dashboard hot-path indexes — child-side drill (AB.4.7
-- fan_in_disagreement looks up "all parents of this child") + parent-
-- side drill (Investigation: "all children of this parent firing").
CREATE INDEX idx_{p}_tp_child ON {p}_transfer_parents (child_transfer_id);
CREATE INDEX idx_{p}_tp_parent ON {p}_transfer_parents (parent_transfer_id);

-- ---------------------------------------------------------------------
-- L1 invariant matview: Fan-In Disagreement (AB.4.7).
-- AB.4.7 — Enforces the runtime side of fan-in chain expectations:
-- for every fan_in chain (parent → child template, with optional
-- expected_parent_count), every child Transfer's parent_count
-- (derived from AB.4.3's _transfer_parents) SHOULD match the
-- expected count (or be >=2 when expected is unset). Violations
-- surface as rows with disagreement_kind in ('orphan', 'missing',
-- 'extra'):
--   - missing: parent_count < expected (a contribution never landed
--     → batch is incomplete);
--   - extra: parent_count > expected (stale or foreign parent
--     reference claimed batch membership it shouldn't have);
--   - orphan: parent_count < 2 AND expected is unset (variable-
--     batch-flow fallback per AB.4.0 lock — a single-parent fan-in
--     child Transfer is degenerate).
--
-- The body is rendered dialect-aware by _render_fan_in_disagreement_body;
-- when no chain declares fan_in (pre-AB.4 fixtures), the body
-- short-circuits with WHERE 1=0 so the matview parses on all 3
-- dialects and contributes zero rows.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_fan_in_disagreement{matview_options} AS
{fan_in_disagreement_body};
-- Dashboard hot-path indexes — per-transfer drill (Today's Exceptions
-- → Transactions), per-template dropdown filter, per-kind triage filter.
CREATE INDEX idx_{p}_fid_transfer ON {p}_fan_in_disagreement (child_transfer_id);
CREATE INDEX idx_{p}_fid_template ON {p}_fan_in_disagreement (child_template_name);
CREATE INDEX idx_{p}_fid_kind ON {p}_fan_in_disagreement (disagreement_kind);

-- ---------------------------------------------------------------------
-- L1 invariant matview: Multi-XOR Violation (AB.6.5).
-- AB.6 (2026-05-19) — Enforces the runtime side of chain.md's
-- "multi-children = exactly one MUST fire" contract. For every chain
-- declaring ≥2 children that are NOT per-child fan_in (their
-- cardinality is `_fan_in_disagreement`'s job — AB.5 coupling), each
-- parent firing's child set should contain exactly one fired child
-- from the declared XOR siblings. Violations surface with
-- disagreement_kind ∈ ('missed', 'overlap'):
--   - missed: 0 declared children fired under this parent (the
--     chain's XOR contract was not honored);
--   - overlap: ≥2 declared children fired under this parent (the
--     XOR alternation collapsed into a duplicate firing).
--
-- The body is rendered dialect-aware by _render_multi_xor_violation_body;
-- when no chain qualifies (no multi-children chain after stripping
-- per-child fan_in entries), the body short-circuits with WHERE 1=0.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_multi_xor_violation{matview_options} AS
{multi_xor_violation_body};
-- Dashboard hot-path indexes — per-parent drill (Today's Exceptions
-- → Transactions on the parent firing), per-chain-parent filter,
-- per-kind triage filter.
CREATE INDEX idx_{p}_mxv_parent ON {p}_multi_xor_violation (parent_transfer_id);
CREATE INDEX idx_{p}_mxv_name ON {p}_multi_xor_violation (parent_rail_or_template_name);
CREATE INDEX idx_{p}_mxv_kind ON {p}_multi_xor_violation (disagreement_kind);

-- ---------------------------------------------------------------------
-- Dashboard-shape matview: Daily Statement Summary.
-- M.1a.9 — moved from `apps/l1_dashboard/datasets.py` CustomSql into
-- a per-instance MATERIALIZED VIEW so QS Direct Query mode doesn't
-- re-evaluate the LAG window + GROUP BY + LEFT JOIN once per visual
-- (5 KPIs on the Daily Statement sheet = 5 re-evaluations otherwise).
-- One row per (account_id, business_day_start). Sheet-local filters
-- narrow to a single (account, day) at render time.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_daily_statement_summary{matview_options} AS
WITH account_days AS (
    SELECT db.account_id, db.account_name, db.account_role,
           db.account_parent_role, db.account_scope,
           db.business_day_start, db.business_day_end,
           db.money AS closing_balance_stored,
           LAG(db.money) OVER (
             PARTITION BY db.account_id
             ORDER BY db.business_day_start
           ) AS opening_balance
    FROM {p}_current_daily_balances db
),
today_flows AS (
    SELECT tx.account_id,
           {date_trunc_tx_posting} AS business_day_start,
           SUM(CASE WHEN tx.amount_direction = 'Debit'
                    THEN tx.amount_money ELSE 0 END) AS total_debits,
           SUM(CASE WHEN tx.amount_direction = 'Credit'
                    THEN tx.amount_money ELSE 0 END) AS total_credits,
           -- BH.1 (2026-05-25): v5→v6 sign-convention regression fix.
           -- Pre-BH.1 used CASE Direction='Credit' THEN amount_money
           -- ELSE -amount_money to derive a signed net from v5's
           -- *unsigned* amount column. v6 made amount_money already
           -- SIGNED (Credit positive, Debit negative — see the
           -- `transactions` table SIGN check constraint), so the
           -- -amount_money branch over-flipped Debit rows back to
           -- positive: net_flow became `credits + abs(debits)` =
           -- gross magnitude, NOT signed net. Downstream `drift`
           -- column (= closing_stored − (opening + net_flow)) inherited
           -- the error — surfaced by BG.2's strengthened narrative-
           -- formula assertion against an independent SUM(amount_money)
           -- on cust-0011-snb 2026-02-24: matview drift -$46,986.60
           -- vs correct $0.00. Plain SUM(amount_money) IS the signed
           -- net by construction in v6.
           SUM(tx.amount_money) AS net_flow,
           COUNT(*) AS leg_count
    FROM {p}_current_transactions tx
    WHERE tx.status <> 'Failed'
    GROUP BY tx.account_id, {date_trunc_tx_posting}
)
SELECT ad.account_id, ad.account_name, ad.account_role,
       ad.account_parent_role, ad.account_scope,
       ad.business_day_start, ad.business_day_end,
       COALESCE(ad.opening_balance, 0) AS opening_balance,
       COALESCE(tf.total_debits, 0) AS total_debits,
       COALESCE(tf.total_credits, 0) AS total_credits,
       COALESCE(tf.net_flow, 0) AS net_flow,
       COALESCE(tf.leg_count, 0) AS leg_count,
       ad.closing_balance_stored,
       COALESCE(ad.opening_balance, 0)
         + COALESCE(tf.net_flow, 0) AS closing_balance_recomputed,
       ad.closing_balance_stored
         - (COALESCE(ad.opening_balance, 0)
            + COALESCE(tf.net_flow, 0)) AS drift
FROM account_days ad
LEFT JOIN today_flows tf
  ON tf.account_id = ad.account_id
  AND tf.business_day_start = ad.business_day_start;
-- Daily Statement sheet's per-(account, day) parameter filter — both
-- columns participate in the WHERE so a composite index covers the
-- KPIs + detail table at once.
CREATE INDEX idx_{p}_dss_account_day
    ON {p}_daily_statement_summary (account_id, business_day_start);

-- ---------------------------------------------------------------------
-- Dashboard-shape matview: Today's Exceptions UNION.
-- M.1a.9 — moved from `apps/l1_dashboard/datasets.py` CustomSql into
-- a per-instance MATERIALIZED VIEW so each visual on Today's
-- Exceptions queries a precomputed table instead of re-running the
-- 5-branch UNION ALL (each branch with its own MAX subquery).
-- One row per L1 invariant violation on the most recent business day.
-- AO.4 — split magnitude into ``magnitude_amount`` (BIGINT cents, money
-- branches) + ``magnitude_count`` (INT, transfer-keyed cardinality
-- branches). Exactly one populated per row; the other NULL. Eliminates
-- the dual-unit "is this $ or count?" UX confusion the operator flagged.
-- ---------------------------------------------------------------------
{matview_create_kw} {p}_todays_exceptions{matview_options} AS
WITH latest_day AS (
    SELECT MAX(business_day_start) AS day
    FROM {p}_current_daily_balances
)
-- Per-day branches (drift / ledger_drift / overdraft / limit_breach /
-- expected_eod_balance_breach) — each is a per-(account, day) cell, so
-- "today's exception" filters to MAX(business_day) from current_daily_balances.
SELECT 'drift' AS check_type, account_id, account_name,
       account_role, account_parent_role,
       business_day_start AS business_day,
       {null_text} AS rail_name,
       ABS(drift) AS magnitude_amount,
       CAST(NULL AS INTEGER) AS magnitude_count
FROM {p}_drift, latest_day
WHERE business_day_start = latest_day.day
UNION ALL
SELECT 'ledger_drift', account_id, account_name, account_role,
       NULL, business_day_start, NULL, ABS(drift),
       CAST(NULL AS INTEGER)
FROM {p}_ledger_drift, latest_day
WHERE business_day_start = latest_day.day
UNION ALL
SELECT 'overdraft', account_id, account_name, account_role,
       account_parent_role, business_day_start, NULL,
       ABS(stored_balance),
       CAST(NULL AS INTEGER)
FROM {p}_overdraft, latest_day
WHERE business_day_start = latest_day.day
UNION ALL
SELECT 'limit_breach', account_id, account_name, account_role,
       account_parent_role, business_day, rail_name,
       (outbound_total - cap),
       CAST(NULL AS INTEGER)
FROM {p}_limit_breach, latest_day
WHERE business_day = latest_day.day
UNION ALL
SELECT 'expected_eod_balance_breach', account_id, account_name,
       account_role, NULL, business_day_start, NULL, ABS(variance),
       CAST(NULL AS INTEGER)
FROM {p}_expected_eod_balance_breach, latest_day
WHERE business_day_start = latest_day.day
-- Currently-open branches (M.4.4.12) — stuck_pending and stuck_unbundled
-- are matviews of legs whose age has exceeded a per-rail cap measured
-- against CURRENT_TIMESTAMP. By construction every row is "currently
-- stuck", so no per-day filter applies — include them all in the rollup.
UNION ALL
SELECT 'stuck_pending', account_id, account_name, account_role,
       account_parent_role, {posting_to_date} AS business_day,
       rail_name, amount_money AS magnitude_amount,
       CAST(NULL AS INTEGER)
FROM {p}_stuck_pending
UNION ALL
SELECT 'stuck_unbundled', account_id, account_name, account_role,
       account_parent_role, {posting_to_date} AS business_day,
       rail_name, amount_money AS magnitude_amount,
       CAST(NULL AS INTEGER)
FROM {p}_stuck_unbundled
-- AB.2.3 — Chain Parent Disagreement: surfaces per child Transfer (not
-- per (account, day)), so no per-day filter applies. The matview's
-- business_day comes from MIN(posting day) of the conflicting leg
-- rows. magnitude_count = the cardinality of the parent_transfer_id
-- set (>= 2 = violation). account_id / account_role default to NULL
-- since the violation is keyed on transfer_id, not account.
UNION ALL
SELECT 'chain_parent_disagreement',
       {null_text} AS account_id,
       {null_text} AS account_name,
       {null_text} AS account_role,
       NULL AS account_parent_role,
       business_day,
       child_template_name AS rail_name,
       {null_bigint} AS magnitude_amount,
       distinct_parent_count AS magnitude_count
FROM {p}_chain_parent_disagreement
-- AB.3.3 — XOR Group Violation: surfaces per (Transfer, template, XOR
-- group) when firing_count != 1. Like chain_parent_disagreement this
-- is keyed on transfer_id, not account, so account columns default
-- NULL. magnitude_count = firing_count (0 = missed, >=2 = overlap).
UNION ALL
SELECT 'xor_group_violation',
       {null_text} AS account_id,
       {null_text} AS account_name,
       {null_text} AS account_role,
       NULL AS account_parent_role,
       business_day,
       template_name AS rail_name,
       {null_bigint} AS magnitude_amount,
       firing_count AS magnitude_count
FROM {p}_xor_group_violation
-- AB.4.7 — Fan-In Disagreement: surfaces per child Transfer when the
-- contributing parent set doesn't match the chain's
-- expected_parent_count (or has cardinality < 2 when expected is
-- unset). Like the other transfer-keyed branches, account columns
-- default NULL. magnitude_count = actual parent_count.
UNION ALL
SELECT 'fan_in_disagreement',
       {null_text} AS account_id,
       {null_text} AS account_name,
       {null_text} AS account_role,
       NULL AS account_parent_role,
       business_day,
       child_template_name AS rail_name,
       {null_bigint} AS magnitude_amount,
       parent_count AS magnitude_count
FROM {p}_fan_in_disagreement
-- AB.6.5 — Multi-XOR Violation: surfaces per parent firing when the
-- declared XOR-sibling set wasn't honored (0 fired = missed,
-- ≥2 fired = overlap). Transfer-keyed like the chain_parent /
-- xor_group branches, so account columns default NULL.
-- magnitude_count = child_count (0 or ≥2).
UNION ALL
SELECT 'multi_xor_violation',
       {null_text} AS account_id,
       {null_text} AS account_name,
       {null_text} AS account_role,
       NULL AS account_parent_role,
       business_day,
       parent_rail_or_template_name AS rail_name,
       {null_bigint} AS magnitude_amount,
       child_count AS magnitude_count
FROM {p}_multi_xor_violation;
-- Today's Exceptions sheet has 3 dropdowns (check_type, account,
-- rail_name); each WHERE filter benefits from its own index.
CREATE INDEX idx_{p}_te_check_type
    ON {p}_todays_exceptions (check_type);
CREATE INDEX idx_{p}_te_account ON {p}_todays_exceptions (account_id);
CREATE INDEX idx_{p}_te_rail ON {p}_todays_exceptions (rail_name);
"""


# Investigation matview names in drop order. Both read from the base
# ``{p}_transactions`` only — order between the two doesn't matter, but
# fixing it keeps emit output deterministic.
_INV_MATVIEW_DROP_NAMES: tuple[str, ...] = (
    "inv_money_trail_edges",
    "inv_pair_rolling_anomalies",
)


_INV_MATVIEW_DROPS_HEADER = """\
-- Investigation matview drops (N.3.b) — like the L1 invariant matview
-- drops, these MUST run before the base ``{p}_transactions`` table is
-- dropped, so we emit them at the top of the script."""


def _emit_inv_matview_drops(p: str, dialect: Dialect) -> str:
    """Emit the Investigation matview DROP block per dialect.

    Same shape as ``_emit_l1_invariant_drops`` — Postgres native /
    Oracle PL/SQL block. Header carries the literal ``{p}_transactions``
    placeholder so the comment stays meaningful regardless of the
    instance prefix; no ``.format()`` substitution on the body.
    """
    drops = "\n".join(
        drop_matview_if_exists(f"{p}_{name}", dialect)
        for name in _INV_MATVIEW_DROP_NAMES
    )
    return f"{_INV_MATVIEW_DROPS_HEADER}\n{drops}\n"


_INV_MATVIEWS_TEMPLATE = """\
-- =====================================================================
-- Investigation matviews per N.3.b (one set per L2 instance) -----------
-- =====================================================================
-- These are the K.4.4 + K.4.5 matviews lifted out of the legacy
-- schema.sql and prefixed for per-instance storage isolation. Read
-- only from {p}_transactions; refresh contract is unchanged
-- (``demo apply`` runs ``REFRESH MATERIALIZED VIEW`` after seed).

-- Investigation: pair-grain rolling-window anomaly matview.
-- Volume Anomalies sheet flags (sender, recipient) pairs whose 2-day
-- rolling SUM crosses the sigma-threshold parameter. Computing the
-- rolling window + population z-score on every dataset load was slow
-- enough at realistic transaction volumes to wedge QuickSight Direct
-- Query, so the work happens at refresh time instead.
--
-- Window semantics: for each (sender, recipient) day with activity,
-- the row's window covers [posted_day - 1, posted_day] (today +
-- yesterday). The 2-day length is hardcoded — a window-length
-- slider would require either multiple matviews or a generate_series
-- scan at dataset time.
--
-- Recipient filter mirrors the recipient-fanout dataset: only `dda`
-- and `merchant_dda` recipients qualify, so administrative sweeps
-- into GL control / concentration master accounts don't dominate the
-- population distribution and crowd out genuine signal.
--
-- IMPORTANT — refresh contract: this matview is NOT auto-refreshed.
-- Operators must run
--     REFRESH MATERIALIZED VIEW {p}_inv_pair_rolling_anomalies;
-- after each ETL load.
{matview_create_kw} {p}_inv_pair_rolling_anomalies{matview_options} AS
WITH pair_legs AS (
    -- v6 column rename. signed_amount becomes amount_money (signed,
    -- where positive is Credit/inflow and negative is Debit/outflow).
    -- posted_at becomes posting. account_type becomes account_role
    -- (L2 Role names from the institution YAML, not the v5 generic
    -- 'dda'/'merchant_dda' enum). The recipient filter that used to
    -- narrow to retail-customer DDAs is now
    -- ``account_scope = 'internal' AND account_parent_role IS NOT NULL``
    -- (leaf internal accounts under a declared parent Role,
    -- structurally equivalent to the v5 intent of real customer
    -- accounts vs control accounts).
    SELECT
        recipient.account_id          AS recipient_account_id,
        recipient.account_name        AS recipient_account_name,
        recipient.account_role        AS recipient_account_type,
        sender.account_id             AS sender_account_id,
        sender.account_name           AS sender_account_name,
        sender.account_role           AS sender_account_type,
        {recipient_posting_to_date}       AS posted_day,
        recipient.transfer_id,
        recipient.amount_money        AS amount
    FROM {p}_transactions recipient
    JOIN {p}_transactions sender
      ON sender.transfer_id = recipient.transfer_id
     AND sender.amount_money < 0
    WHERE recipient.amount_money > 0
      AND recipient.status = 'Posted'
      AND sender.status = 'Posted'
      AND recipient.account_scope = 'internal'
      AND recipient.account_parent_role IS NOT NULL
),
pair_daily AS (
    -- Collapse to one row per (pair, day) before windowing so the
    -- rolling SUM ranges over distinct days rather than individual legs.
    SELECT
        recipient_account_id,
        recipient_account_name,
        recipient_account_type,
        sender_account_id,
        sender_account_name,
        sender_account_type,
        posted_day,
        SUM(amount)                 AS day_sum,
        COUNT(DISTINCT transfer_id) AS day_transfer_count
    FROM pair_legs
    GROUP BY
        recipient_account_id, recipient_account_name, recipient_account_type,
        sender_account_id, sender_account_name, sender_account_type,
        posted_day
),
pair_windows AS (
    -- Rolling 2-day SUM per pair, anchored on each active day. RANGE
    -- INTERVAL handles sparse days correctly: a pair with activity on
    -- day N but not N-1 gets a 1-day window — semantically a single
    -- spike — rather than a phantom zero contribution.
    SELECT
        recipient_account_id,
        recipient_account_name,
        recipient_account_type,
        sender_account_id,
        sender_account_name,
        sender_account_type,
        posted_day,
        SUM(day_sum) OVER ({rolling_window})            AS window_sum,
        SUM(day_transfer_count) OVER ({rolling_window}) AS transfer_count
    FROM pair_daily
),
population AS (
    -- Single-row scalar: mean + sample stddev across every pair-window.
    -- Sample stddev (STDDEV_SAMP) matches the analyst convention of
    -- "this window vs. the rest of the population".
    SELECT
        {cast_avg_numeric}                       AS pop_mean,
        {cast_stddev_numeric}  AS pop_stddev
    FROM pair_windows
)
SELECT
    pw.recipient_account_id,
    pw.recipient_account_name,
    pw.recipient_account_type,
    pw.sender_account_id,
    pw.sender_account_name,
    pw.sender_account_type,
    {window_start_expr}   AS window_start,
    {window_end_expr}                        AS window_end,
    pw.window_sum,
    pw.transfer_count,
    pop.pop_mean,
    pop.pop_stddev,
    CASE
        WHEN pop.pop_stddev = 0 THEN 0
        ELSE (pw.window_sum - pop.pop_mean) / pop.pop_stddev
    END                                             AS z_score,
    CASE
        WHEN pop.pop_stddev = 0 THEN '0-1 sigma'
        WHEN ABS((pw.window_sum - pop.pop_mean) / pop.pop_stddev) < 1 THEN '0-1 sigma'
        WHEN ABS((pw.window_sum - pop.pop_mean) / pop.pop_stddev) < 2 THEN '1-2 sigma'
        WHEN ABS((pw.window_sum - pop.pop_mean) / pop.pop_stddev) < 3 THEN '2-3 sigma'
        WHEN ABS((pw.window_sum - pop.pop_mean) / pop.pop_stddev) < 4 THEN '3-4 sigma'
        ELSE '4+ sigma'
    END                                             AS z_bucket
FROM pair_windows pw
CROSS JOIN population pop;


-- Investigation: money-trail recursive-CTE matview.
-- Money Trail sheet walks `parent_transfer_id` chains from a given
-- root, flattening each hop to a (source_account, target_account,
-- hop_amount) edge so a Sankey can render the chain. Computing the
-- recursive walk + leg pairing on every dataset query was a
-- non-starter for QuickSight Direct Query at chain depths > 2.
--
-- Two-step structure:
--   1. WITH RECURSIVE walks `parent_transfer_id` from each root
--      (transfer with NULL parent) down through descendants, tagging
--      every member with its `root_transfer_id` and `depth`.
--   2. Each chain member is then joined back to {p}_transactions and
--      split into source-leg (signed_amount < 0) x target-leg
--      (signed_amount > 0) pairs sharing the transfer_id, producing
--      one row per edge.
--
-- Multi-leg-only semantics: single-leg transfers (sale records,
-- inflow-only `external_txn` arrival rows) have no source or no
-- target leg by themselves and are dropped from the trail. They
-- still appear as chain members (counted by depth) -- they just
-- don't contribute visible edges. The chain ancestry is preserved
-- because the recursive walk operates on `transfer_id` /
-- `parent_transfer_id` directly, not on legs.
--
-- IMPORTANT — refresh contract: this matview is NOT auto-refreshed.
-- Operators must run
--     REFRESH MATERIALIZED VIEW {p}_inv_money_trail_edges;
-- after each ETL load.
{matview_create_kw} {p}_inv_money_trail_edges{matview_options} AS
{with_recursive_kw}
distinct_transfers AS (
    -- One row per transfer_id with its parent. {p}_transactions has
    -- one row per leg, so we deduplicate before walking — the parent
    -- linkage is transfer-level, not leg-level. Note: {p}_transactions
    -- carries the parent linkage in ``transfer_parent_id`` (v6 column).
    -- The legacy global matview read ``parent_transfer_id`` from the
    -- v5 base table.
    SELECT DISTINCT transfer_id, transfer_parent_id
    FROM {p}_transactions
),
-- Oracle 19c requires recursive CTEs to declare their column alias
-- list inline (ORA-32039). Postgres accepts the same syntax — both
-- dialects emit the explicit list.
chain (transfer_id, root_transfer_id, depth) AS (
    -- Roots: transfers with no parent. Each root labels itself.
    SELECT
        transfer_id,
        transfer_id AS root_transfer_id,
        0           AS depth
    FROM distinct_transfers
    WHERE transfer_parent_id IS NULL

    UNION ALL

    -- Descendants inherit the root and bump depth.
    SELECT
        d.transfer_id,
        c.root_transfer_id,
        c.depth + 1
    FROM distinct_transfers d
    JOIN chain c ON d.transfer_parent_id = c.transfer_id
)
-- v6 column rename. signed_amount becomes amount_money (signed),
-- posted_at becomes posting, and account_type becomes account_role.
-- Output column names kept the v5 names so dashboard-side consumers
-- (datasets, visuals) don't need to follow this rename — only the
-- internal SELECT does.
SELECT
    c.root_transfer_id,
    c.transfer_id,
    c.depth,
    src.account_id           AS source_account_id,
    src.account_name         AS source_account_name,
    src.account_role         AS source_account_type,
    tgt.account_id           AS target_account_id,
    tgt.account_name         AS target_account_name,
    tgt.account_role         AS target_account_type,
    tgt.amount_money         AS hop_amount,
    tgt.posting              AS posted_at,
    tgt.rail_name            AS rail_name
FROM chain c
JOIN {p}_transactions tgt
  ON tgt.transfer_id = c.transfer_id
 AND tgt.amount_money > 0
 AND tgt.status = 'Posted'
JOIN {p}_transactions src
  ON src.transfer_id = c.transfer_id
 AND src.amount_money < 0
 AND src.status = 'Posted';
"""
