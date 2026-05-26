"""AY.2.b — unit tests for TransferTemplateGenerator.

Seed-color coverage generator: emits ONE shared Transfer firing of
an L2-declared TransferTemplate (M.3.10g + broad-mode). Two-leg-first
templates emit 2 legs summing to expected_net=0; single-leg-first
emit 1 leg in the rail's leg_direction. Both stamp template_name on
every leg so the L2 Flow Tracing Transfer Templates sheet reads
firings by template. `intended` returns a `CoverageObservation`.
"""

# pytest.approx() typeshed stubs are partial — kill the resulting noise here.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    ClaimedAccountsGenerator,
    CoverageObservation,
    TransferTemplateFactory,
    TransferTemplateGenerator,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
# Picked from spec_example.yaml's transfer_templates by first leg_rail
# kind. ExternalReconciliationCycle's first leg_rail is ReconciliationLeg
# (two-leg); MerchantSettlementCycle's first leg_rail is SubledgerCharge
# (single-leg debit).
_TWO_LEG_FIRST_TEMPLATE = "ExternalReconciliationCycle"
_SINGLE_LEG_FIRST_TEMPLATE = "MerchantSettlementCycle"


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()
    replace_config(
        conn, prefix=_PREFIX, cfg_json="{}",
        l2_json=json.dumps({"rails": []}),
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    return conn


# ---------------------------------------------------------------------------
# Factory — template + first-leg-rail kind resolution.
# ---------------------------------------------------------------------------


def test_factory_resolves_two_leg_first_template() -> None:
    gen = TransferTemplateFactory().scenario_for_template(
        _TWO_LEG_FIRST_TEMPLATE, anchor_day=date(2030, 1, 1),
    )
    assert isinstance(gen, TransferTemplateGenerator)
    assert gen.is_two_leg is True
    assert gen.destination_account_id is not None
    # rail_name resolves to the template's first leg_rail.
    assert gen.rail_name == "ReconciliationLeg"


def test_factory_resolves_single_leg_first_template() -> None:
    gen = TransferTemplateFactory().scenario_for_template(
        _SINGLE_LEG_FIRST_TEMPLATE, anchor_day=date(2030, 1, 1),
    )
    assert gen.is_two_leg is False
    assert gen.destination_account_id is None
    assert gen.rail_name == "SubledgerCharge"
    # SubledgerCharge has leg_direction='Debit' on the L2.
    assert gen.single_leg_direction == "Debit"


def test_factory_rejects_unknown_template_loudly() -> None:
    with pytest.raises(ValueError, match="not declared on the L2 instance"):
        TransferTemplateFactory().scenario_for_template(
            "DefinitelyNotATemplate",
        )


# ---------------------------------------------------------------------------
# intended subtype + identity.
# ---------------------------------------------------------------------------


def test_intended_is_a_coverage_observation() -> None:
    gen = TransferTemplateFactory().scenario_for_template(
        _TWO_LEG_FIRST_TEMPLATE, anchor_day=date(2030, 1, 1), firing_seq=3,
    )
    intended = gen.intended
    assert isinstance(intended, CoverageObservation), (
        f"TransferTemplateGenerator's intended should be a "
        f"CoverageObservation; got {type(intended).__name__}"
    )
    items = dict(intended.identity)
    assert intended.invariant == "transfer_template_firing"
    assert items["template_name"] == _TWO_LEG_FIRST_TEMPLATE
    assert items["transfer_id"] == gen.transfer_id
    assert items["firing_seq"] == 3
    assert items["leg_count"] == 2


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = TransferTemplateFactory().scenario_for_template(
        _TWO_LEG_FIRST_TEMPLATE, anchor_day=date(2030, 1, 1),
    )
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert gen.claimed_accounts == frozenset({
        gen.source_account_id, gen.destination_account_id,
    })


# ---------------------------------------------------------------------------
# Emit row shape — template_name stamped on every leg.
# ---------------------------------------------------------------------------


def test_two_leg_first_emit_writes_balanced_pair_stamped_with_template() -> None:
    gen = TransferTemplateFactory().scenario_for_template(
        _TWO_LEG_FIRST_TEMPLATE, anchor_day=date(2030, 1, 1), amount=300.0,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT transfer_id, amount_money, amount_direction, "
            f"rail_name, template_name "
            f"FROM {_PREFIX}_transactions ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    # All legs stamp template_name + the resolved first leg_rail name.
    assert {r[4] for r in rows} == {_TWO_LEG_FIRST_TEMPLATE}
    assert {r[3] for r in rows} == {"ReconciliationLeg"}
    assert {r[0] for r in rows} == {gen.transfer_id}
    assert sum(float(r[1]) for r in rows) == pytest.approx(0.0)
    assert {r[2] for r in rows} == {"Debit", "Credit"}


def test_single_leg_first_emit_writes_one_leg_stamped_with_template() -> None:
    gen = TransferTemplateFactory().scenario_for_template(
        _SINGLE_LEG_FIRST_TEMPLATE, anchor_day=date(2030, 1, 1), amount=50.0,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT amount_money, amount_direction, rail_name, template_name "
            f"FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    # AO.1: amount_money is BIGINT cents — $-50.00 → -5000.
    assert rows == [(-5000, "Debit", "SubledgerCharge", _SINGLE_LEG_FIRST_TEMPLATE)]


# ---------------------------------------------------------------------------
# AV.5 metadata tagging.
# ---------------------------------------------------------------------------


def test_tagged_emit_tags_every_leg() -> None:
    gen = TransferTemplateFactory().scenario_for_template(
        _TWO_LEG_FIRST_TEMPLATE, anchor_day=date(2030, 1, 1),
    )
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ay2b-transfer-template")
        conn.commit()
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE json_extract(metadata, '$.scenario_id') = ?",
            ("test-ay2b-transfer-template",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == 2
