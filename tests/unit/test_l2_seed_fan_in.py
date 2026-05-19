"""AB.4.4: seed.py fan_in chain firings — shape contract tests.

Pins the unit-level shape of ``_emit_fan_in_chain_firings`` against
the AB.4.0 lock: N parent firings share one shared ``child_transfer_id``,
each leg carries its contributing parent's ``transfer_parent_id``,
and the AB.4.3 ``_transfer_parents`` matview will derive
``DISTINCT(child, parent) = batch_size`` rows per fan-in batch.

These tests build a minimal in-memory L2Instance + state, call the
helper directly, and parse the emitted SQL row strings to assert
shape — no DB round-trip needed for unit-level coverage.
"""

from __future__ import annotations

import random
import re
from datetime import date
from decimal import Decimal

from recon_gen.common.l2.primitives import (
    Account,
    Chain,
    ChainChildSpec,
    Identifier,
    L2Instance,
    Money,
    Name,
    SingleLegRail,
    TransferTemplate,
)
from recon_gen.common.l2.seed import (
    _BaselineState,
    _Counter,
    _emit_fan_in_chain_firings,
)
from recon_gen.common.sql.dialect import Dialect


def _toy_instance(
    *, fan_in: bool = True, expected_parent_count: int | None = 3,
) -> L2Instance:
    """A minimal 3-rail / 1-template / 1-fan_in-chain fixture."""
    leg_a = SingleLegRail(
        name=Identifier("LegA"),
        origin="InternalInitiated",
        metadata_keys=(Identifier("cycle_id"),),
        leg_role=(Identifier("R"),),
        leg_direction="Debit",
    )
    leg_b = SingleLegRail(
        name=Identifier("LegB"),
        origin="InternalInitiated",
        metadata_keys=(Identifier("cycle_id"),),
        leg_role=(Identifier("R"),),
        leg_direction="Credit",
    )
    parent_rail = SingleLegRail(
        name=Identifier("ParentRail"),
        origin="InternalInitiated",
        metadata_keys=(),
        leg_role=(Identifier("R"),),
        leg_direction="Debit",
    )
    tmpl = TransferTemplate(
        name=Identifier("BatchedPayout"),
        expected_net=Money(Decimal("0")),
        transfer_key=(Identifier("cycle_id"),),
        completion="business_day_end+1d",
        leg_rails=(Identifier("LegA"), Identifier("LegB")),
    )
    return L2Instance(
        accounts=(
            Account(
                id=Identifier("a"),
                role=Identifier("R"),
                scope="internal",
                name=Name("Acct"),
            ),
        ),
        account_templates=(),
        rails=(leg_a, leg_b, parent_rail),
        transfer_templates=(tmpl,),
        chains=(
            Chain(
                parent=Identifier("ParentRail"),
                children=(
                    ChainChildSpec(
                        name=Identifier("BatchedPayout"),
                        fan_in=fan_in,
                        expected_parent_count=expected_parent_count,
                    ),
                ),
            ),
        ),
        limit_schedules=(),
    )


def _make_parent_firings(n: int) -> list[tuple[str, date, Decimal]]:
    return [
        (f"tr-parent-{i:04d}", date(2030, 1, 1), Decimal("100.00"))
        for i in range(n)
    ]


def _toy_state() -> _BaselineState:
    return _BaselineState(
        anchor=date(2030, 1, 1),
        window_days=30,
        business_days=(date(2030, 1, 1),),
    )


def _extract_transfer_id_and_parent(row_sql: str) -> tuple[str, str]:
    """Parse the (transfer_id, transfer_parent_id) pair out of one
    emitted row's SQL. Both are quoted single-string values in the
    INSERT-style row literal."""
    # Crude but adequate: find the first two `tr-base-fanin-` /
    # `tr-parent-` IDs in the row.
    cand = re.findall(r"'(tr-[A-Za-z0-9_-]+)'", row_sql)
    transfer_id = next(c for c in cand if c.startswith("tr-base-fanin-"))
    parent_id = next(c for c in cand if c.startswith("tr-parent-"))
    return transfer_id, parent_id


def test_fan_in_emits_one_shared_transfer_per_batch() -> None:
    """AB.4.4: a batch of `expected_parent_count` parent firings
    produces one shared child_transfer_id. With 6 parents +
    expected_parent_count=3 ⇒ 2 batches ⇒ 2 distinct child_transfer_ids."""
    inst = _toy_instance(expected_parent_count=3)
    chain = inst.chains[0]
    state = _toy_state()
    rng = random.Random(0xC0FFEE)
    counter = _Counter()

    rows = _emit_fan_in_chain_firings(
        chain, _make_parent_firings(6), inst, state, counter, rng,
        Dialect.POSTGRES,
    )
    # Each batch emits batch_size (3) parents × 2 leg_rails = 6 leg
    # rows per batch. 2 batches → 12 rows total.
    assert len(rows) == 12
    # DISTINCT child_transfer_id count == 2 (one per batch).
    child_ids = {_extract_transfer_id_and_parent(r)[0] for r in rows}
    assert len(child_ids) == 2, (
        f"expected 2 distinct child Transfer ids (one per batch), "
        f"got {len(child_ids)}: {sorted(child_ids)}"
    )


def test_fan_in_batch_legs_carry_distinct_parent_ids() -> None:
    """AB.4.4: each leg in a fan_in batch carries ITS contributing
    parent's transfer_parent_id. DISTINCT(child_transfer_id,
    transfer_parent_id) per batch == batch_size — that's what the
    AB.4.3 _transfer_parents matview derives."""
    inst = _toy_instance(expected_parent_count=3)
    chain = inst.chains[0]
    state = _toy_state()
    rng = random.Random(0xC0FFEE)
    counter = _Counter()

    rows = _emit_fan_in_chain_firings(
        chain, _make_parent_firings(3), inst, state, counter, rng,
        Dialect.POSTGRES,
    )
    pairs = {_extract_transfer_id_and_parent(r) for r in rows}
    # 1 batch → 1 child_transfer_id × 3 parents = 3 distinct pairs.
    assert len(pairs) == 3
    child_ids = {p[0] for p in pairs}
    parent_ids = {p[1] for p in pairs}
    assert len(child_ids) == 1
    assert parent_ids == {
        "tr-parent-0000", "tr-parent-0001", "tr-parent-0002",
    }


def test_fan_in_partial_tail_batch_dropped() -> None:
    """AB.4.4: when n_parents % batch_size != 0, the partial tail
    is dropped from baseline emission (the orphan-shaped firings are
    the plant path's job — baseline emits healthy batches only).
    7 parents + batch_size=3 ⇒ 2 full batches × 6 leg rows = 12 rows."""
    inst = _toy_instance(expected_parent_count=3)
    chain = inst.chains[0]
    state = _toy_state()
    rng = random.Random(0xC0FFEE)
    counter = _Counter()

    rows = _emit_fan_in_chain_firings(
        chain, _make_parent_firings(7), inst, state, counter, rng,
        Dialect.POSTGRES,
    )
    # 2 full batches × 3 parents × 2 leg_rails = 12. The 7th parent
    # (index 6) is dropped.
    assert len(rows) == 12
    parent_ids = {_extract_transfer_id_and_parent(r)[1] for r in rows}
    assert "tr-parent-0006" not in parent_ids


def test_fan_in_unset_expected_count_defaults_to_two() -> None:
    """AB.4.0 lock: when expected_parent_count is None (variable-batch
    flow), batch size defaults to 2 — the minimum non-orphan."""
    inst = _toy_instance(expected_parent_count=None)
    chain = inst.chains[0]
    state = _toy_state()
    rng = random.Random(0xC0FFEE)
    counter = _Counter()

    rows = _emit_fan_in_chain_firings(
        chain, _make_parent_firings(4), inst, state, counter, rng,
        Dialect.POSTGRES,
    )
    # 4 parents / batch_size=2 = 2 batches × 2 parents × 2 leg_rails = 8.
    assert len(rows) == 8
    child_ids = {_extract_transfer_id_and_parent(r)[0] for r in rows}
    assert len(child_ids) == 2


def test_fan_in_zero_parents_emits_nothing() -> None:
    """Defensive: a fan_in chain with no parent firings emits no rows."""
    inst = _toy_instance(expected_parent_count=3)
    chain = inst.chains[0]
    state = _toy_state()
    rng = random.Random(0xC0FFEE)
    counter = _Counter()

    rows = _emit_fan_in_chain_firings(
        chain, [], inst, state, counter, rng, Dialect.POSTGRES,
    )
    assert rows == []
