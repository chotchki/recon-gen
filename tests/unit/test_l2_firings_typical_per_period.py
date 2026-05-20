"""AF (E8): firings_typical_per_period — loader + validator + serializer
+ seed-generator coverage.

The soft per-period firing-COUNT bound (complement to AB.5's per-firing
``amount_typical_range``). Covers AF.2 (parse/validate/serialize) +
AF.3 (generator picks count-per-period from the declared range).
"""

from __future__ import annotations

import random
import re
from datetime import date
from decimal import Decimal
from typing import Literal

import pytest

from recon_gen.common.l2.loader import (
    L2LoaderError,
    _load_firings_typical_per_period,
)
from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    FiringsTypicalPerPeriod,
    Identifier,
    L2Instance,
    Money,
    Name,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)
from recon_gen.common.l2.seed import (
    _periods_in_window,
    _pick_firings_count,
    emit_baseline_seed,
)
from recon_gen.common.l2.serializer import _dump_firings_typical_per_period
from recon_gen.common.l2.validate import (
    L2ValidationError,
    _check_firings_typical_per_period_shape,
)
from recon_gen.common.sql.dialect import Dialect


# ---------------------------------------------------------------------------
# AF.2 — loader (heterogeneous: compact list OR {period, range} mapping)
# ---------------------------------------------------------------------------


def test_loader_compact_form_defaults_business_day() -> None:
    ftp = _load_firings_typical_per_period([50, 500], path="r.f")
    assert ftp == FiringsTypicalPerPeriod(
        period="business_day", count_range=(50, 500),
    )


def test_loader_mapping_form_explicit_period() -> None:
    ftp = _load_firings_typical_per_period(
        {"period": "month", "range": [80, 120]}, path="r.f",
    )
    assert ftp == FiringsTypicalPerPeriod(period="month", count_range=(80, 120))


def test_loader_mapping_form_period_defaults_when_omitted() -> None:
    ftp = _load_firings_typical_per_period({"range": [1, 3]}, path="r.f")
    assert ftp == FiringsTypicalPerPeriod(
        period="business_day", count_range=(1, 3),
    )


def test_loader_none_returns_none() -> None:
    assert _load_firings_typical_per_period(None, path="r.f") is None


@pytest.mark.parametrize(
    "bad,frag",
    [
        ([50], "exactly 2 elements"),
        ([1, 2, 3], "exactly 2 elements"),
        ([1, "x"], "must be an integer"),
        ([True, 2], "must be an integer"),  # bool is not a valid count
        ("50,500", "compact"),
        ({"period": "fortnight", "range": [1, 2]}, "period must be one of"),
        ({"period": "week"}, "requires a "),
    ],
)
def test_loader_bad_shapes_raise(bad: object, frag: str) -> None:
    with pytest.raises(L2LoaderError, match=re.escape(frag)):
        _load_firings_typical_per_period(bad, path="r.f")


# ---------------------------------------------------------------------------
# AF.2 — serializer (compact when business_day, mapping otherwise)
# ---------------------------------------------------------------------------


def test_serializer_business_day_emits_compact() -> None:
    out = _dump_firings_typical_per_period(
        FiringsTypicalPerPeriod(period="business_day", count_range=(50, 500)),
    )
    assert out == [50, 500]


def test_serializer_non_default_period_emits_mapping() -> None:
    out = _dump_firings_typical_per_period(
        FiringsTypicalPerPeriod(period="month", count_range=(80, 120)),
    )
    assert out == {"period": "month", "range": [80, 120]}


@pytest.mark.parametrize(
    "ftp",
    [
        FiringsTypicalPerPeriod(period="business_day", count_range=(50, 500)),
        FiringsTypicalPerPeriod(period="month", count_range=(80, 120)),
        FiringsTypicalPerPeriod(period="week", count_range=(1, 1)),
        FiringsTypicalPerPeriod(period="pay_period", count_range=(0, 10)),
    ],
)
def test_serializer_loader_round_trip(ftp: FiringsTypicalPerPeriod) -> None:
    dumped = _dump_firings_typical_per_period(ftp)
    reloaded = _load_firings_typical_per_period(dumped, path="r.f")
    assert reloaded == ftp


# ---------------------------------------------------------------------------
# AF.2 — validator (W1a-c)
# ---------------------------------------------------------------------------


def _single_leg(
    name: str,
    *,
    direction: Literal["Debit", "Credit"] = "Debit",
    aggregating: bool = False,
    ftp: FiringsTypicalPerPeriod | None = None,
) -> SingleLegRail:
    return SingleLegRail(
        name=Identifier(name),
        origin="InternalInitiated",
        metadata_keys=(Identifier("k"),),
        leg_role=(Identifier("R"),),
        leg_direction=direction,
        aggregating=aggregating,
        firings_typical_per_period=ftp,
    )


def _instance_with_rail(rail: SingleLegRail) -> L2Instance:
    return L2Instance(
        accounts=(
            Account(
                id=Identifier("a1"), role=Identifier("R"),
                scope="internal", name=Name("A1"),
            ),
        ),
        account_templates=(AccountTemplate(role=Identifier("R"), scope="internal"),),
        rails=(rail,),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def test_validator_w1a_min_gt_max_rejected() -> None:
    inst = _instance_with_rail(
        _single_leg("R1", ftp=FiringsTypicalPerPeriod("business_day", (10, 5))),
    )
    with pytest.raises(L2ValidationError, match="W1a"):
        _check_firings_typical_per_period_shape(inst)


def test_validator_w1a_equal_endpoints_allowed() -> None:
    # Unlike AB.5's V1a, equal endpoints are valid here ([1,1] = "exactly
    # one per period").
    inst = _instance_with_rail(
        _single_leg("R1", ftp=FiringsTypicalPerPeriod("month", (1, 1))),
    )
    _check_firings_typical_per_period_shape(inst)  # no raise


def test_validator_w1b_negative_rejected() -> None:
    inst = _instance_with_rail(
        _single_leg("R1", ftp=FiringsTypicalPerPeriod("week", (-1, 5))),
    )
    with pytest.raises(L2ValidationError, match="W1b"):
        _check_firings_typical_per_period_shape(inst)


def test_validator_w1b_zero_allowed() -> None:
    inst = _instance_with_rail(
        _single_leg("R1", ftp=FiringsTypicalPerPeriod("week", (0, 5))),
    )
    _check_firings_typical_per_period_shape(inst)  # no raise


def test_validator_w1c_aggregating_rail_rejected() -> None:
    inst = _instance_with_rail(
        _single_leg(
            "R1", aggregating=True,
            ftp=FiringsTypicalPerPeriod("business_day", (1, 5)),
        ),
    )
    with pytest.raises(L2ValidationError, match="W1c"):
        _check_firings_typical_per_period_shape(inst)


def test_validator_template_w1a_min_gt_max_rejected() -> None:
    tmpl = TransferTemplate(
        name=Identifier("T1"),
        expected_net=Money(Decimal("0")),
        transfer_key=(Identifier("k"),),
        completion="business_day_end+1d",
        leg_rails=(Identifier("R1"),),
        firings_typical_per_period=FiringsTypicalPerPeriod("month", (9, 2)),
    )
    inst = L2Instance(
        accounts=(
            Account(
                id=Identifier("a1"), role=Identifier("R"),
                scope="internal", name=Name("A1"),
            ),
        ),
        account_templates=(AccountTemplate(role=Identifier("R"), scope="internal"),),
        rails=(_single_leg("R1"),),
        transfer_templates=(tmpl,),
        chains=(),
        limit_schedules=(),
    )
    with pytest.raises(L2ValidationError, match="W1a"):
        _check_firings_typical_per_period_shape(inst)


# ---------------------------------------------------------------------------
# AF.3 — _pick_firings_count + _periods_in_window
# ---------------------------------------------------------------------------


def test_periods_in_window() -> None:
    assert _periods_in_window("business_day", 63) == 63
    assert _periods_in_window("week", 63) == 12  # 63 // 5
    assert _periods_in_window("pay_period", 63) == 6  # 63 // 10
    assert _periods_in_window("month", 63) == 3  # 63 // 21
    # Window shorter than one period still yields one period's firings.
    assert _periods_in_window("month", 5) == 1


def test_pick_firings_count_absent_returns_fallback_no_rng_consume() -> None:
    rail = _single_leg("R1")  # no firings_typical_per_period
    rng = random.Random(1234)
    before = rng.getstate()
    out = _pick_firings_count(
        rail, business_day_count=63, rng=rng, fallback=999,
    )
    assert out == 999
    # Critical: no rng consumed when the field is absent (keeps pre-AF
    # locked seeds byte-identical).
    assert rng.getstate() == before


def test_pick_firings_count_present_scales_by_periods() -> None:
    rail = _single_leg(
        "R1", ftp=FiringsTypicalPerPeriod("business_day", (10, 10)),
    )
    rng = random.Random(1234)
    # [10,10] per business_day × 63 business days = 630.
    out = _pick_firings_count(
        rail, business_day_count=63, rng=rng, fallback=1,
    )
    assert out == 630


def test_pick_firings_count_monthly_scales() -> None:
    rail = _single_leg("R1", ftp=FiringsTypicalPerPeriod("month", (100, 100)))
    rng = random.Random(1234)
    # [100,100] per month × (63 // 21 = 3 months) = 300.
    out = _pick_firings_count(
        rail, business_day_count=63, rng=rng, fallback=1,
    )
    assert out == 300


def test_pick_firings_count_deterministic_for_seed() -> None:
    rail = _single_leg("R1", ftp=FiringsTypicalPerPeriod("business_day", (50, 500)))
    a = _pick_firings_count(
        rail, business_day_count=63, rng=random.Random(7), fallback=1,
    )
    b = _pick_firings_count(
        rail, business_day_count=63, rng=random.Random(7), fallback=1,
    )
    assert a == b


# ---------------------------------------------------------------------------
# AF.3 — end-to-end: declared range bounds the emitted firing count
# ---------------------------------------------------------------------------


def _rail_count_l2(ftp: FiringsTypicalPerPeriod | None) -> L2Instance:
    """Single two-leg rail; firings_typical_per_period optional."""
    return L2Instance(
        accounts=(
            Account(
                id=Identifier("src"), role=Identifier("SRC"),
                scope="internal", name=Name("Src"),
            ),
            Account(
                id=Identifier("dst"), role=Identifier("DST"),
                scope="external", name=Name("Dst"),
            ),
        ),
        account_templates=(
            AccountTemplate(role=Identifier("SRC"), scope="internal"),
        ),
        rails=(
            TwoLegRail(
                name=Identifier("CountRail"),
                metadata_keys=(Identifier("k"),),
                source_role=(Identifier("SRC"),),
                destination_role=(Identifier("DST"),),
                origin="InternalInitiated",
                firings_typical_per_period=ftp,
            ),
        ),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def _count_rail_firings(sql: str, rail_slug: str) -> int:
    """Distinct ``tr-base-<rail_slug>-NNNNNN`` transfer_ids in the SQL."""
    ids = set(re.findall(rf"'(tr-base-{rail_slug}-\d+)'", sql))
    return len(ids)


def test_declared_range_bounds_emitted_count() -> None:
    """AF.3 end-to-end: a rail with firings_typical_per_period=[5,5] per
    business_day emits ~5 × business_days firings (exact band endpoints
    pin the per-period count; the per-day Poisson spread averages to it
    but total is deterministic from _pick_firings_count)."""
    inst = _rail_count_l2(
        FiringsTypicalPerPeriod("business_day", (5, 5)),
    )
    sql = emit_baseline_seed(
        inst, prefix="t", window_days=20, anchor=date(2030, 1, 1),
        dialect=Dialect.SQLITE,
    )
    n = _count_rail_firings(sql, "countrail")
    # window_days=20 → ~14 business days. total = 5 × 14 = 70 target;
    # Poisson per-day spread means actual emitted ≈ target within a
    # tolerance band. Assert it lands far above the pre-AF heuristic
    # floor (which for a generic 2-leg rail is much smaller) and is
    # plausibly near 5/business-day.
    assert n > 0
    # The fallback heuristic for this shape would be a different number;
    # the declared [5,5] band must dominate. Loose sanity band:
    assert 30 <= n <= 110, f"emitted {n} firings, expected ~70 (5/day × ~14 days)"
