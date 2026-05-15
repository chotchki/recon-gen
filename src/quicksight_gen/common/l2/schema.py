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
- L2's Limits — projected into the ``daily_balances.limits`` Map column
  by integrator ETL; no separate limits table.
- L2's Chains, TransferTemplates — read by dashboard SQL at view-build
  time (the SQL string knows which TransferTypes can chain into which
  via L2 lookups), not materialized as tables.

The "minimum SQL surface" stance follows from the spike: M.2 (porting
AR CMS) will surface what L2 derived tables are actually needed beyond
the base layer. Add them then.
"""

from __future__ import annotations

from quicksight_gen.common.sql import (
    Dialect,
    analyze_table,
    cast,
    date_minus_days,
    date_trunc_day,
    decimal_type,
    drop_index_if_exists,
    drop_matview_if_exists,
    drop_table_if_exists,
    epoch_seconds_between,
    json_check,
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

from .primitives import L2Instance


def emit_schema(
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
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
    """
    p = instance.instance
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
    base = _emit_base_schema(p, dialect, instance)
    invariants = _emit_l1_invariant_views(instance, dialect=dialect)
    inv_views = _emit_inv_views(instance, dialect=dialect)
    return (
        l1_drops + "\n" + inv_drops + "\n" + base + "\n\n"
        + invariants + "\n\n" + inv_views
    )




def emit_schema_drop_sql(
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
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
    """
    p = instance.instance
    l1_drops = _emit_l1_invariant_drops(p, dialect)
    inv_drops = _emit_inv_matview_drops(p, dialect)
    # Base layer: Current* matviews → indexes → base tables.
    pieces = [
        drop_matview_if_exists(f"{p}_current_daily_balances", dialect),
        drop_matview_if_exists(f"{p}_current_transactions", dialect),
    ]
    for _, name_template in _BASE_INDEX_DROPS:
        pieces.append(drop_index_if_exists(name_template.format(p=p), dialect))
    pieces.extend([
        drop_table_if_exists(f"{p}_daily_balances", dialect),
        drop_table_if_exists(f"{p}_transactions", dialect),
    ])
    base_drops = "\n".join(pieces)
    header = (
        f"-- =====================================================================\n"
        f"-- L2 instance: {p} — full schema teardown\n"
        f"-- Generated by quicksight_gen.common.l2.schema.emit_schema_drop_sql\n"
        f"-- Drops every per-prefix object emit_schema creates, in dependency\n"
        f"-- order. Re-runnable: every DROP is IF EXISTS / swallow-on-missing.\n"
        f"-- =====================================================================\n"
    )
    return (
        header + "\n"
        + l1_drops + "\n"
        + inv_drops + "\n"
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
    "business_day_end", "money", "limits", "supersedes",
)


def wipe_demo_data_sql(
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
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
    """
    p = instance.instance
    return (
        f"-- =====================================================================\n"
        f"-- L2 instance: {p} — base-table data wipe (step 2 of deploy pipeline)\n"
        f"-- Generated by quicksight_gen.common.l2.schema.wipe_demo_data_sql\n"
        f"-- Empties <prefix>_transactions + <prefix>_daily_balances; matviews\n"
        f"-- are re-derived in step 4 (refresh_matviews_sql).\n"
        f"-- =====================================================================\n"
        f"DELETE FROM {p}_daily_balances;\n"
        f"DELETE FROM {p}_transactions;\n"
    )


def refresh_matviews_sql(
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
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
    """
    p = instance.instance
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
        return _emit_sqlite_matview_refresh(instance)
    # REFRESH first, then ANALYZE — ANALYZE updates planner stats so
    # subsequent SELECTs use the indexes we ship on each matview
    # (without ANALYZE the planner doesn't know the post-REFRESH row
    # count + value distribution and may pick a sequential scan).
    refreshes = "\n".join(refresh_matview(n, dialect) for n in names)
    analyzes = "\n".join(analyze_table(n, dialect) for n in names)
    return f"{refreshes}\n{analyzes}"


def _emit_sqlite_matview_refresh(instance: L2Instance) -> str:
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
    """
    p = instance.instance
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
    invariants = _emit_l1_invariant_views(instance, dialect=Dialect.SQLITE)
    inv_views = _emit_inv_views(instance, dialect=Dialect.SQLITE)
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
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
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
    p = instance.instance
    limit_cases = _render_limit_breach_cases(instance, p=p, dialect=dialect)
    pending_age_cases = _render_pending_age_cases(instance, dialect=dialect)
    unbundled_age_cases = _render_unbundled_age_cases(instance, dialect=dialect)
    return _L1_INVARIANT_VIEWS_TEMPLATE.format(
        p=p,
        limit_cases=limit_cases,
        pending_age_cases=pending_age_cases,
        unbundled_age_cases=unbundled_age_cases,
        matview_options=matview_options(dialect),
        matview_create_kw=matview_create_keyword(dialect),
        date_trunc_tx_posting=date_trunc_day("tx.posting", dialect),
        epoch_age_seconds=epoch_seconds_between(
            "CURRENT_TIMESTAMP", "ct.posting", dialect,
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
    )


def _render_limit_breach_cases(
    instance: L2Instance, *, p: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Build the CASE-WHEN body that the limit_breach view uses to look
    up a (parent_role, rail) cap from L2's LimitSchedules.

    Inline at view-emit time (not via JSON_VALUE on daily_balances.limits)
    because dynamic-key JSON path syntax isn't portable across the SQL
    targets we support. The L2 instance's LimitSchedules are static at
    schema-emit time anyway — re-emitting the schema picks up changes.

    Returns a multi-line SQL CASE expression. References ``tx.``-prefixed
    columns since the view reads parent_role + rail_name directly from
    the transaction row (denormalized in v6) — no JOIN to daily_balances
    needed. If the instance has no LimitSchedules, returns
    ``NULL::numeric`` (typed NULL) so the column has a concrete type —
    bare NULL infers as text in PostgreSQL and breaks the outer
    ``outbound_total > cap`` comparison with `numeric > text`.

    Z.B (2026-05-15): formerly matched on ``tx.transfer_type``; under
    the symmetric collapse the cap binds to a rail name directly.
    """
    if not instance.limit_schedules:
        return typed_null("numeric", dialect)
    branches: list[str] = []
    for ls in instance.limit_schedules:
        # Each LimitSchedule is keyed on (parent_role, rail) per
        # validator U5; the cap is the threshold value.
        branches.append(
            f"WHEN tx.account_parent_role = '{ls.parent_role}' "
            f"AND tx.rail_name = '{ls.rail}' "
            f"THEN {ls.cap}"
        )
    branches_sql = "\n        ".join(branches)
    return f"CASE\n        {branches_sql}\n        ELSE NULL\n    END"


def _render_pending_age_cases(
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Build the CASE-WHEN body the stuck_pending view uses to look up
    a Rail's `max_pending_age` (in seconds).

    Mirror of `_render_limit_breach_cases` — inline at view-emit time
    rather than via JSON_VALUE on a per-row config column, so the SQL
    stays JSON-path-portable. Walks both TwoLegRail + SingleLegRail
    instances; each Rail with a non-None `max_pending_age` becomes one
    CASE branch keyed on `rail_name`. Rails without an aging watch get
    no branch (the outer CASE returns NULL → outer WHERE excludes them).

    Empty result if no Rail has `max_pending_age` set: returns ``NULL``
    so the view emits valid SQL but surfaces zero rows.
    """
    branches: list[str] = []
    for rail in instance.rails:
        if rail.max_pending_age is None:
            continue
        seconds = int(rail.max_pending_age.total_seconds())
        branches.append(
            f"WHEN ct.rail_name = '{rail.name}' THEN {seconds}"
        )
    if not branches:
        # Typed NULL — bare NULL infers as text and breaks the outer
        # `tx.age_seconds > tx.max_pending_age_seconds` comparison.
        return typed_null("bigint", dialect)
    branches_sql = "\n        ".join(branches)
    return f"CASE\n        {branches_sql}\n        ELSE NULL\n    END"


def _emit_inv_views(
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
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
    """
    p = instance.instance
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


def _render_unbundled_age_cases(
    instance: L2Instance, *, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Build the CASE-WHEN body the stuck_unbundled view uses to look
    up a Rail's `max_unbundled_age` (in seconds).

    Same shape as `_render_pending_age_cases`, keyed on the same
    `ct.rail_name` column. Per validator R8, `max_unbundled_age` is
    only meaningful on rails that appear in some AggregatingRail's
    `bundles_activity` — the validator catches misconfigured rails at
    L2 load time, so by the time we render here every Rail with the
    field set is bundle-eligible. Rails without `max_unbundled_age`
    get no branch (no aging watch → NULL → excluded by outer WHERE).
    """
    branches: list[str] = []
    for rail in instance.rails:
        if rail.max_unbundled_age is None:
            continue
        seconds = int(rail.max_unbundled_age.total_seconds())
        branches.append(
            f"WHEN ct.rail_name = '{rail.name}' THEN {seconds}"
        )
    if not branches:
        # Typed NULL — same reason as `_render_pending_age_cases`.
        return typed_null("bigint", dialect)
    branches_sql = "\n        ".join(branches)
    return f"CASE\n        {branches_sql}\n        ELSE NULL\n    END"


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
    {dec202}) come from common/sql type helpers. DROP placeholders
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
        "dec202": decimal_type(20, 2, dialect),
        # Matview options suffix (Oracle BUILD IMMEDIATE REFRESH COMPLETE
        # ON DEMAND; empty on Postgres + SQLite).
        "matview_options": matview_options(dialect),
        # Matview CREATE keyword — PG/Oracle ``CREATE MATERIALIZED VIEW``,
        # SQLite ``CREATE TABLE`` (matviews land as plain tables).
        "matview_create_kw": matview_create_keyword(dialect),
        # JSON validity constraint — PG/Oracle ``IS JSON``,
        # SQLite ``json_valid()``.
        "metadata_json_check": json_check("metadata", dialect),
        "limits_json_check": json_check("limits", dialect),
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
        # Partial-index WHERE clause — PG only. Oracle + SQLite get
        # the full index.
        "bundler_partial_where": (
            "\n    WHERE bundle_id IS NULL"
            if dialect is Dialect.POSTGRES else ""
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
-- Generated by quicksight_gen.common.l2.schema.emit_schema
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
-- amount_money  — signed Decimal per L1 Amount's "money agrees with
--                 direction" invariant. Positive ⇔ Credit; negative
--                 ⇔ Debit. The CHECK enforces sign-direction agreement.
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
CREATE TABLE {p}_transactions (
    entry                {tx_entry_decl},
    id                   {vc100}   NOT NULL,
    account_id           {vc100}   NOT NULL,
    account_name         {vc255},
    account_role         {vc100},
    account_scope        {vc20}    NOT NULL
        CHECK (account_scope IN ('internal', 'external')),
    account_parent_role  {vc100},
    amount_money         {dec202}  NOT NULL,
    amount_direction     {vc20}    NOT NULL
        CHECK (amount_direction IN ('Debit', 'Credit')),
    status               {vc50}    NOT NULL,
    posting              {ts}    NOT NULL,
    transfer_id          {vc100}   NOT NULL,
    transfer_completion  {ts},
    transfer_parent_id   {vc100},
    rail_name            {vc100}   NOT NULL,
    template_name        {vc100},
    bundle_id            {vc100},
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
-- limits        — JSON map of TransferType → cap, projected from L2's
--                 LimitSchedule entries by the integrator's ETL. NULL
--                 means no limit enforcement on this account-day.
-- money         — signed; CAN go negative (overdraft is observable per
--                 L1's Non-negative Stored Balance SHOULD constraint).
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
    expected_eod_balance   {dec202},
    business_day_start     {ts}    NOT NULL,
    business_day_end       {ts}    NOT NULL,
    money                  {dec202}  NOT NULL,
    limits                 {json_text},
    supersedes             {vc50},
    {db_pk_decl},
    CHECK (business_day_end > business_day_start),
    {limits_json_check}
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
CREATE INDEX idx_{p}_transactions_bundler_eligibility
    ON {p}_transactions (rail_name, status){bundler_partial_where};
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
        + COALESCE(direct_totals.direct_balance, 0) AS computed_balance
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
LEFT JOIN (
    SELECT
        tx.account_id,
        {date_trunc_tx_posting} AS business_day,
        SUM(tx.amount_money) AS direct_balance
    FROM {p}_current_transactions tx
    WHERE tx.status = 'Posted'
    GROUP BY tx.account_id, {date_trunc_tx_posting}
) direct_totals
    ON direct_totals.account_id = parent_db.account_id
   AND direct_totals.business_day >= parent_db.business_day_start
   AND direct_totals.business_day < parent_db.business_day_end
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
-- L1 invariant: Limit breach.
-- SPEC: For every CurrentStoredBalance where Limits is set, for every
-- (TransferType, limit) in Limits, for every child Account whose
-- Parent = this account, OutboundFlow(child, type, businessDay)
-- SHOULD be ≤ limit.
-- Implementation: compute outbound debit totals per (account, day, type)
-- from CurrentTransaction, compare against the cap. Caps come from
-- L2's LimitSchedules — embedded inline as CASE branches at view-emit
-- time (dynamic JSON path lookup isn't portable across our SQL targets).
-- account_parent_role is denormalized on every transaction row in v6,
-- so no JOIN to daily_balances is needed (which also avoids the failure
-- mode where a breach business_day has no enclosing daily_balance row).
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
        SUM(ABS(tx.amount_money)) AS outbound_total,
        {limit_cases} AS cap
    FROM {p}_current_transactions tx
    WHERE tx.amount_direction = 'Debit'
      AND tx.status = 'Posted'
      AND tx.account_scope = 'internal'
      AND tx.account_parent_role IS NOT NULL
    GROUP BY
        tx.account_id, tx.account_name, tx.account_role,
        tx.account_parent_role,
        {date_trunc_tx_posting},
        tx.rail_name
) outbound_with_cap
WHERE cap IS NOT NULL
  AND outbound_total > cap;
CREATE INDEX idx_{p}_lb_account_day
    ON {p}_limit_breach (account_id, business_day);
CREATE INDEX idx_{p}_lb_rail ON {p}_limit_breach (rail_name);

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
        {pending_age_cases} AS max_pending_age_seconds,
        {epoch_age_seconds} AS age_seconds
    FROM {p}_current_transactions ct
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
        {unbundled_age_cases} AS max_unbundled_age_seconds,
        {epoch_age_seconds} AS age_seconds
    FROM {p}_current_transactions ct
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
           SUM(CASE WHEN tx.amount_direction = 'Credit'
                    THEN tx.amount_money
                    ELSE -tx.amount_money END) AS net_flow,
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
-- `magnitude` normalized per branch so sort-by-magnitude reads
-- consistently regardless of check_type.
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
       ABS(drift) AS magnitude
FROM {p}_drift, latest_day
WHERE business_day_start = latest_day.day
UNION ALL
SELECT 'ledger_drift', account_id, account_name, account_role,
       NULL, business_day_start, NULL, ABS(drift)
FROM {p}_ledger_drift, latest_day
WHERE business_day_start = latest_day.day
UNION ALL
SELECT 'overdraft', account_id, account_name, account_role,
       account_parent_role, business_day_start, NULL,
       ABS(stored_balance)
FROM {p}_overdraft, latest_day
WHERE business_day_start = latest_day.day
UNION ALL
SELECT 'limit_breach', account_id, account_name, account_role,
       account_parent_role, business_day, rail_name,
       (outbound_total - cap)
FROM {p}_limit_breach, latest_day
WHERE business_day = latest_day.day
UNION ALL
SELECT 'expected_eod_balance_breach', account_id, account_name,
       account_role, NULL, business_day_start, NULL, ABS(variance)
FROM {p}_expected_eod_balance_breach, latest_day
WHERE business_day_start = latest_day.day
-- Currently-open branches (M.4.4.12) — stuck_pending and stuck_unbundled
-- are matviews of legs whose age has exceeded a per-rail cap measured
-- against CURRENT_TIMESTAMP. By construction every row is "currently
-- stuck", so no per-day filter applies — include them all in the rollup.
UNION ALL
SELECT 'stuck_pending', account_id, account_name, account_role,
       account_parent_role, {posting_to_date} AS business_day,
       rail_name, amount_money AS magnitude
FROM {p}_stuck_pending
UNION ALL
SELECT 'stuck_unbundled', account_id, account_name, account_role,
       account_parent_role, {posting_to_date} AS business_day,
       rail_name, amount_money AS magnitude
FROM {p}_stuck_unbundled;
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
