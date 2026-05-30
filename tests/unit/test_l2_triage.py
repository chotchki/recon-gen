"""BT.4 — ``common.l2.triage`` gap detector unit tests.

Four gap kinds, four sections of tests. Each seeds a tiny
file-backed aiosqlite pool then asserts the detector surfaces the
right gaps with the right evidence.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest

from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
from recon_gen.common.l2 import (
    Identifier,
    L2Instance,
    LimitSchedule,
    RailName,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
    derive_column_contracts,
)
from recon_gen.common.l2.triage import (
    Gap,
    detect_gaps,
)
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


_PREFIX = "triage_test"


from collections.abc import Sequence

SeedRow = tuple[
    str, str | None, str | None, str | None, str | None,
    str, str | None, str, str | None,
]


def _seed_db(rows: Sequence[SeedRow]) -> str:
    """File-backed sqlite seeded with ``rows`` against ``triage_test_transactions``.

    Returns the file path; caller is responsible for ``os.unlink``.
    Each row: (id, rail_name, template_name, account_role,
    account_parent_role, amount_direction, transfer_parent_id,
    posting, metadata).
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        f"CREATE TABLE {_PREFIX}_transactions ("
        "id TEXT PRIMARY KEY, "
        "rail_name TEXT, "
        "template_name TEXT, "
        "account_role TEXT, "
        "account_parent_role TEXT, "
        "amount_direction TEXT NOT NULL, "
        "transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, "
        "metadata TEXT)"
    )
    conn.executemany(
        f"INSERT INTO {_PREFIX}_transactions "
        "(id, rail_name, template_name, account_role, account_parent_role, "
        " amount_direction, transfer_parent_id, posting, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def empty_instance() -> L2Instance:
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


PoolFactory = Callable[[str], AsyncConnectionPool]


@pytest.fixture
def pool_factory() -> Iterator[PoolFactory]:
    """Lifecycle-managed pool factory. Returns a function that creates
    a pool from a seeded DB path; auto-closes everything at teardown."""
    pools: list[AsyncConnectionPool] = []
    paths: list[str] = []

    def _build(path: str) -> AsyncConnectionPool:
        cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
        pool = asyncio.run(make_connection_pool(cfg))
        pools.append(pool)
        paths.append(path)
        return pool

    yield _build

    for pool in pools:
        asyncio.run(pool.close())
    for path in paths:
        if os.path.exists(path):
            os.unlink(path)


def _detect(
    pool: AsyncConnectionPool, instance: L2Instance,
) -> tuple[Gap, ...]:
    contracts = derive_column_contracts(instance)
    return asyncio.run(detect_gaps(
        pool, _PREFIX, instance, contracts, dialect=Dialect.SQLITE,
    ))


# -- Gap kind 1: unmatched_rail ----------------------------------------------


def test_detect_gaps_finds_unmatched_rail_name(
    pool_factory: PoolFactory, empty_instance: L2Instance,
) -> None:
    """A row whose rail_name isn't in L2.rails → 1 gap card per unmatched value."""
    rows = [
        ("tx-1", "phantom_rail", None, "CustomerLedger", None,
         "Credit", None, "2030-01-05 09:00:00", None),
        ("tx-2", "phantom_rail", None, "CustomerLedger", None,
         "Credit", None, "2030-01-06 09:00:00", None),
    ]
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, empty_instance)
    rail_gaps = [g for g in gaps if g.kind == "unmatched_rail"]
    assert len(rail_gaps) == 1
    g = rail_gaps[0]
    assert g.observed_value == "phantom_rail"
    assert g.evidence.row_count == 2
    # Sample id is one of the two rows.
    assert g.evidence.sample_transaction_id in {"tx-1", "tx-2"}
    # Link points at the rail editor list page.
    assert g.link_target == "/l2_shape/rail/"
    # Diagnosis names the offending value + the count.
    assert 'phantom_rail' in g.diagnosis
    assert '2 rows' in g.diagnosis


def test_detect_gaps_skips_known_rail_name(pool_factory: PoolFactory) -> None:
    """Rows whose rail_name IS in L2.rails contribute no gap."""
    rows = [
        ("tx-1", "ach_credit", None, "CustomerLedger", None,
         "Credit", None, "2030-01-05 09:00:00", None),
    ]
    inst = L2Instance(
        accounts=(),
        account_templates=(),
        rails=(TwoLegRail(
            name=Identifier("ach_credit"),
            metadata_keys=(),
            source_role=(Identifier("A"),),
            destination_role=(Identifier("B"),),
            origin="InternalInitiated",
            expected_net=Decimal("0"),
        ),),
        transfer_templates=(), chains=(), limit_schedules=(),
    )
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, inst)
    assert all(g.kind != "unmatched_rail" for g in gaps)


def test_detect_gaps_unmatched_rail_lists_declared_rails_in_extras(
    pool_factory: PoolFactory,
) -> None:
    """The card's extras carry the declared-rails list so the operator
    sees what they could rename the ETL's value to."""
    rows = [
        ("tx-1", "ach", None, "C", None, "Credit", None,
         "2030-01-05 09:00:00", None),
    ]
    inst = L2Instance(
        accounts=(),
        account_templates=(),
        rails=(
            TwoLegRail(
                name=Identifier("ach_credit"), metadata_keys=(),
                source_role=(Identifier("A"),),
                destination_role=(Identifier("B"),),
                origin="InternalInitiated", expected_net=Decimal("0"),
            ),
            TwoLegRail(
                name=Identifier("wire"), metadata_keys=(),
                source_role=(Identifier("A"),),
                destination_role=(Identifier("B"),),
                origin="InternalInitiated", expected_net=Decimal("0"),
            ),
        ),
        transfer_templates=(), chains=(), limit_schedules=(),
    )
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, inst)
    rail_gap = next(g for g in gaps if g.kind == "unmatched_rail")
    assert "ach_credit, wire" in rail_gap.evidence.extras["declared_rails"]


# -- Gap kind 2: unmatched_template ------------------------------------------


def test_detect_gaps_finds_unmatched_template_name(
    pool_factory: PoolFactory, empty_instance: L2Instance,
) -> None:
    rows = [
        ("tx-1", "ach_debit", "ReturnReversal", "C", None,
         "Debit", None, "2030-01-05 09:00:00", None),
    ]
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, empty_instance)
    tmpl_gaps = [g for g in gaps if g.kind == "unmatched_template"]
    assert len(tmpl_gaps) == 1
    g = tmpl_gaps[0]
    assert g.observed_value == "ReturnReversal"
    assert g.evidence.row_count == 1
    assert g.link_target == "/l2_shape/transfer_template/"


# -- Gap kind 3: missing_limit_schedule --------------------------------------


def test_detect_gaps_finds_missing_limit_schedule(pool_factory: PoolFactory) -> None:
    """Rows fire for (parent_role, rail) without a matching LimitSchedule."""
    rows = [
        ("tx-1", "wire", None, "DDA", "CustomerLedger",
         "Debit", None, "2030-01-05 09:00:00", None),
    ]
    inst = L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        # Has ach_debit limit for CustomerLedger; NOT wire.
        limit_schedules=(LimitSchedule(
            parent_role=Identifier("CustomerLedger"),
            rail=RailName("ach_debit"),
            cap=Decimal("5000"),
            direction="Outbound",
        ),),
    )
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, inst)
    limit_gaps = [g for g in gaps if g.kind == "missing_limit_schedule"]
    assert len(limit_gaps) == 1
    g = limit_gaps[0]
    assert "CustomerLedger" in g.diagnosis
    assert "wire" in g.diagnosis
    assert g.observed_value == "CustomerLedger::wire"
    assert g.evidence.row_count == 1
    # Sibling existing schedules surface in extras.
    siblings_key = next(
        k for k in g.evidence.extras if k.startswith("existing_schedules_for_")
    )
    assert "ach_debit" in g.evidence.extras[siblings_key]


def test_detect_gaps_skips_when_limit_schedule_exists(pool_factory: PoolFactory) -> None:
    rows = [
        ("tx-1", "ach", None, "DDA", "CustomerLedger",
         "Debit", None, "2030-01-05 09:00:00", None),
    ]
    inst = L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(LimitSchedule(
            parent_role=Identifier("CustomerLedger"),
            rail=RailName("ach"),
            cap=Decimal("1000"),
            direction="Outbound",
        ),),
    )
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, inst)
    assert all(g.kind != "missing_limit_schedule" for g in gaps)


# -- Gap kind 4: missing_metadata_key ----------------------------------------


def test_detect_gaps_finds_missing_required_metadata_key(pool_factory: PoolFactory) -> None:
    """A row tagged with a template but missing the template's
    transfer_key metadata key → 1 gap per missing key."""
    rows = [
        # Row 1: template tag, metadata has the key.
        ("tx-1", "leg", "T", "C", None, "Credit", None,
         "2030-01-05 09:00:00", '{"required_key":"x"}'),
        # Row 2: template tag, metadata MISSING the key.
        ("tx-2", "leg", "T", "C", None, "Credit", None,
         "2030-01-06 09:00:00", '{"other":"y"}'),
        # Row 3: template tag, NULL metadata.
        ("tx-3", "leg", "T", "C", None, "Credit", None,
         "2030-01-07 09:00:00", None),
    ]
    inst = L2Instance(
        accounts=(),
        account_templates=(),
        rails=(SingleLegRail(
            name=Identifier("leg"), metadata_keys=(),
            leg_role=(Identifier("C"),),
            leg_direction="Credit",
            origin="InternalInitiated",
        ),),
        transfer_templates=(TransferTemplate(
            name=Identifier("T"),
            expected_net=Decimal("0"),
            transfer_key=(Identifier("required_key"),),
            completion="business_day_end",
            leg_rails=(Identifier("leg"),),
        ),),
        chains=(), limit_schedules=(),
    )
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, inst)
    md_gaps = [g for g in gaps if g.kind == "missing_metadata_key"]
    assert len(md_gaps) == 1
    g = md_gaps[0]
    assert g.observed_value == "T::required_key"
    # 2 of 3 rows missing the key (tx-2 + tx-3).
    assert g.evidence.row_count == 2
    assert g.evidence.extras["template_total_rows"] == "3"
    assert g.evidence.extras["missing_key"] == "required_key"
    # Link points at the template's editor page (BT.5's editor_path).
    assert g.link_target == "/l2_shape/transfer_template/T/edit"


def test_detect_gaps_no_gap_when_all_rows_carry_required_key(pool_factory: PoolFactory) -> None:
    rows = [
        ("tx-1", "leg", "T", "C", None, "Credit", None,
         "2030-01-05 09:00:00", '{"k":"x"}'),
    ]
    inst = L2Instance(
        accounts=(),
        account_templates=(),
        rails=(SingleLegRail(
            name=Identifier("leg"), metadata_keys=(),
            leg_role=(Identifier("C"),),
            leg_direction="Credit",
            origin="InternalInitiated",
        ),),
        transfer_templates=(TransferTemplate(
            name=Identifier("T"),
            expected_net=Decimal("0"),
            transfer_key=(Identifier("k"),),
            completion="business_day_end",
            leg_rails=(Identifier("leg"),),
        ),),
        chains=(), limit_schedules=(),
    )
    pool = pool_factory(_seed_db(rows))
    gaps = _detect(pool, inst)
    assert all(g.kind != "missing_metadata_key" for g in gaps)


# -- All-clear -----------------------------------------------------------------


def test_detect_gaps_returns_empty_when_no_violations(pool_factory: PoolFactory) -> None:
    """Empty DB + empty L2 → zero gaps."""
    pool = pool_factory(_seed_db([]))
    inst = L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )
    gaps = _detect(pool, inst)
    assert gaps == ()
