"""DM.2 — App2 Role→Account cascade NARROWING through the options fetcher.

The DM.1/DM.2 tree-wiring tests (``test_dm_daily_statement_role_cascade.py``)
lock the SHAPE — the Account dropdown declares ``cascade_source`` = Role +
``cascade_match_column`` = ``account_role``, and the App2 spec carries the
source param name. But they DON'T exercise the runtime narrowing: they
walk the tree, not the fetcher. That gap let a real regression ship — the
picker rendered with the cascade source param present yet returned ALL
accounts for any picked Role, because CQ.4.a de-parameterized
``DS_L1_DS_ACCOUNTS`` (so there was no ``<<$pL1DsRole>>`` placeholder to
substitute) and nothing replaced the lost narrowing on the App2 side.

This test drives ``make_options_search_fetcher``'s ``fetch`` against a
seeded DuckDB with the REAL L1 Daily Statement datasets + cascade map, and
asserts the option universe NARROWS when a real Role is picked and stays
FULL on the sentinel / no-role case.

Hard constraint honored: ``DS_L1_DS_ACCOUNTS`` stays unparameterized (we
assert it has no ``<<$``-placeholder); the narrowing is applied dynamically
by the fetcher off the tree-derived cascade map (DM.2), not by re-adding a
dataset parameter (which would re-break QS's
``GetUniqueAttributeValuesSyncForAnalysis`` picker — the CQ.4.a regression).

Design lock: ``docs/audits/dm_0_daily_statement_app2_cascade.md``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator, Mapping
from typing import Any

import duckdb
import pytest

from recon_gen.apps.l1_dashboard.app import (
    P_L1_DS_ROLE,
    build_l1_dashboard_app,
)
from recon_gen.apps.l1_dashboard.datasets import (
    DS_L1_DS_ACCOUNTS,
    build_all_l1_dashboard_datasets,
)
from recon_gen.common.dataset_contract import get_sql
from recon_gen.common.db import AsyncConnectionPool, execute_script, make_connection_pool
from recon_gen.common.html._sql_executor import execute_visual_sql_async
from recon_gen.common.html._tree_fetcher import (
    CascadeMap,
    build_cascade_map,
    make_options_search_fetcher,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.seed import emit_full_seed
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config

_PREFIX = "spec_example"
_DIALECT = Dialect.DUCKDB
# The Daily Statement Role param's "match all" sentinel (DM.1). A pick of
# this value must NOT narrow.
_ROLE_SENTINEL = "__l1_no_role_selected__"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def seeded_l1_picker() -> Iterator[tuple[AsyncConnectionPool, Any, CascadeMap]]:
    """Full L1 schema + seed + matview refresh on a file-backed DuckDB,
    with the real Daily Statement datasets registered + the DM.2 cascade
    map built from the L1 tree.

    File-backed (not ``:memory:``) so every pool connection sees the same
    seeded data — the production multi-connection semantics. Yields
    ``(pool, cfg, cascade_map)``; tears down the pool + temp file at exit.
    """
    inst = default_l2_instance()

    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)

    # Seed synchronously via a plain DuckDB connection.
    conn = duckdb.connect(path)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(inst, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    scenario = default_scenario_for(inst).scenario
    execute_script(
        cur, emit_full_seed(inst, scenario, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    execute_script(
        cur, refresh_matviews_sql(inst, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(
        db_dialect=_DIALECT, db_table_prefix=_PREFIX, db_url=path,
    )
    # Registers DS_L1_DS_ACCOUNTS SQL + its PickerMatviewHint.
    build_all_l1_dashboard_datasets(cfg, inst)
    app = build_l1_dashboard_app(cfg, l2_instance=inst)
    app.resolve_auto_ids()
    cascade_map = build_cascade_map([app])

    pool = _run(make_connection_pool(cfg))
    try:
        yield pool, cfg, cascade_map
    finally:
        _run(pool.close())
        os.unlink(path)


def _options(
    pool: AsyncConnectionPool, cfg: Any, cascade_map: CascadeMap,
    *, role: str | None, query: str = "",
) -> tuple[str, ...]:
    """Drive ``make_options_search_fetcher`` for the Account dropdown.

    ``role=None`` → no ``param_pL1DsRole`` in the request (the page-load
    case). A string → that Role rides as the cascade source value.
    """
    fetcher = make_options_search_fetcher(cfg, pool=pool, cascade_map=cascade_map)
    url_params: Mapping[str, list[str]] = (
        {} if role is None else {f"param_{P_L1_DS_ROLE}": [role]}
    )

    async def _go() -> tuple[str, ...]:
        result = await fetcher(
            DS_L1_DS_ACCOUNTS, "account_display", query, url_params,
        )
        return result.options

    return _run(_go())


def _role_counts(pool: AsyncConnectionPool, cfg: Any, cascade_map: CascadeMap,
                 ) -> dict[str, int]:
    """Ground truth: distinct ``account_display`` per ``account_role`` in
    the (unparameterized) picker universe — derived from the dataset SQL,
    not hand-listed, so it tracks the seed."""
    sql = get_sql(DS_L1_DS_ACCOUNTS)
    count_sql = (
        f"SELECT account_role, COUNT(DISTINCT account_display) "
        f"FROM ({sql}) t GROUP BY account_role"
    )

    async def _go() -> dict[str, int]:
        rows, _cols = await execute_visual_sql_async(
            pool, count_sql, {}, dialect=cfg.db.dialect,
        )
        return {str(r[0]): int(r[1]) for r in rows}

    return _run(_go())


# ---------------------------------------------------------------------------
# DM.2 — the regression guard: the cascade NARROWS at fetch time.
# ---------------------------------------------------------------------------

def test_dm2_dataset_stays_unparameterized(
    seeded_l1_picker: tuple[AsyncConnectionPool, Any, CascadeMap],
) -> None:
    """Hard constraint: the Account picker dataset must NOT carry a
    ``<<$...>>`` placeholder (re-parameterizing it re-breaks QS's native
    picker — the exact CQ.4.a regression). The narrowing is App2-side."""
    sql = get_sql(DS_L1_DS_ACCOUNTS)
    assert "<<$" not in sql, (
        "DS_L1_DS_ACCOUNTS must stay unparameterized (CQ.4.a) — the DM.2 "
        f"cascade narrows in the App2 fetcher, not via a dataset param. SQL: {sql}"
    )


def test_dm2_cascade_map_keyed_on_account_display(
    seeded_l1_picker: tuple[AsyncConnectionPool, Any, CascadeMap],
) -> None:
    """The cascade map keys on the Account dropdown's own (options
    dataset, column) = ``(l1-ds-accounts-ds, account_display)`` and
    records the ``account_role`` match column + ``pL1DsRole`` source +
    its sentinel."""
    _pool, _cfg, cascade_map = seeded_l1_picker
    rule = cascade_map.get((DS_L1_DS_ACCOUNTS, "account_display"))
    assert rule is not None, (
        "DM.2 cascade map must carry a rule for the Account dropdown's "
        f"(dataset, column); got keys {list(cascade_map)}"
    )
    assert rule.match_column == "account_role"
    assert rule.source_param == str(P_L1_DS_ROLE)
    assert _ROLE_SENTINEL in rule.sentinels


def test_dm2_no_role_returns_full_universe(
    seeded_l1_picker: tuple[AsyncConnectionPool, Any, CascadeMap],
) -> None:
    """Page-load (no ``param_pL1DsRole``) returns the full internal-account
    universe — no narrowing."""
    pool, cfg, cascade_map = seeded_l1_picker
    counts = _role_counts(pool, cfg, cascade_map)
    total = sum(counts.values())
    opts = _options(pool, cfg, cascade_map, role=None)
    assert len(opts) == total, (
        f"no-role must return ALL {total} accounts; got {len(opts)}"
    )


def test_dm2_sentinel_role_returns_full_universe(
    seeded_l1_picker: tuple[AsyncConnectionPool, Any, CascadeMap],
) -> None:
    """The Role 'no selection' sentinel means match-all — the fetcher must
    NOT add a narrowing predicate for it."""
    pool, cfg, cascade_map = seeded_l1_picker
    counts = _role_counts(pool, cfg, cascade_map)
    total = sum(counts.values())
    opts = _options(pool, cfg, cascade_map, role=_ROLE_SENTINEL)
    assert len(opts) == total, (
        f"sentinel role must return ALL {total} accounts (match-all); "
        f"got {len(opts)}"
    )


def test_dm2_real_role_narrows_to_that_role(
    seeded_l1_picker: tuple[AsyncConnectionPool, Any, CascadeMap],
) -> None:
    """Picking a real Role narrows the Account options to EXACTLY that
    role's accounts — the core bug fix. Asserts both the narrowed count
    matches the ground-truth per-role count AND that it's strictly fewer
    than the full universe (so a no-op 'all accounts' answer fails)."""
    pool, cfg, cascade_map = seeded_l1_picker
    counts = _role_counts(pool, cfg, cascade_map)
    total = sum(counts.values())
    # Pick the most-populated role with >1 account so "narrows to < total"
    # is a real assertion (not vacuously true on a singleton-only seed).
    target_role = max(counts, key=lambda r: counts[r])
    expected = counts[target_role]
    assert expected < total, (
        "test fixture invariant: the busiest role must have fewer accounts "
        f"than the full universe ({expected} vs {total}) so narrowing is "
        f"observable; per-role counts {counts}"
    )
    opts = _options(pool, cfg, cascade_map, role=target_role)
    assert len(opts) == expected, (
        f"role {target_role!r} must narrow to its {expected} accounts; "
        f"got {len(opts)} (full universe is {total})"
    )
    assert len(opts) < total, (
        f"role {target_role!r} must NARROW below the full {total}-account "
        f"universe; got {len(opts)} (the cascade is a no-op — the DM.2 bug)"
    )


def test_dm2_singleton_role_narrows_to_one(
    seeded_l1_picker: tuple[AsyncConnectionPool, Any, CascadeMap],
) -> None:
    """A role with a single account narrows the picker to exactly 1
    option — the strongest narrowing signal (a no-op cascade would return
    the whole universe instead)."""
    pool, cfg, cascade_map = seeded_l1_picker
    counts = _role_counts(pool, cfg, cascade_map)
    singletons = [r for r, n in counts.items() if n == 1]
    assert singletons, (
        f"test fixture invariant: expected at least one singleton role; "
        f"counts {counts}"
    )
    role = singletons[0]
    opts = _options(pool, cfg, cascade_map, role=role)
    assert len(opts) == 1, (
        f"singleton role {role!r} must narrow to 1 account; got {len(opts)}"
    )


def test_dm2_typed_query_and_cascade_compose(
    seeded_l1_picker: tuple[AsyncConnectionPool, Any, CascadeMap],
) -> None:
    """The cascade narrowing applies on the typed-query (typeahead) path
    too, not only the empty-query seed path — every returned option must
    belong to the picked role's universe."""
    pool, cfg, cascade_map = seeded_l1_picker
    counts = _role_counts(pool, cfg, cascade_map)
    target_role = max(counts, key=lambda r: counts[r])
    role_universe = set(_options(pool, cfg, cascade_map, role=target_role))
    # A 1-char query the seed accounts contain (display strings are
    # "Name (id)" so any common letter matches a subset). Result must be a
    # subset of the role's narrowed universe.
    typed = _options(pool, cfg, cascade_map, role=target_role, query="e")
    assert typed, "expected at least one match for q='e' within the role"
    assert set(typed) <= role_universe, (
        "typed-query results must stay within the cascade-narrowed role "
        f"universe; leaked {set(typed) - role_universe}"
    )
