"""DS.1 — run every hand-derived KAT vector through its residual.

The vectors in tests/data/kats/*.json were derived BY HAND from the
written laws (derivations: docs/audits/ds_1_kat_derivations.md) — no
expected value was read out of implementation code, so a residual bug
and a KAT bug can't cancel. The two BORN-DIVERGENT vectors (drift M3
carried-day, multi_xor MX4 day-multiplication) assert the LAW side —
they pass HERE against the residuals and will pin the DS.3.2/DS.3.3a
SQL fixes when the enumeration gate arrives.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from recon_gen.common.money import Cents
from recon_gen.common.spine.residuals import (
    BalanceRow,
    CadenceGap,
    LegRow,
    ResidualState,
    TrailEdge,
    cadence_gap_residual,
    chain_parent_residual,
    drift_residual,
    expected_eod_residual,
    fan_in_residual,
    ledger_drift_residual,
    limit_breach_residual,
    money_trail_residual,
    multi_xor_residual,
    overdraft_residual,
    stuck_pending_residual,
    stuck_unbundled_residual,
    xor_group_residual,
)

KATS_DIR = Path(__file__).parent.parent / "data" / "kats"


def _leg(d: dict[str, Any]) -> LegRow:
    return LegRow(
        id=d["id"],
        entry=d["entry"],  # typing-smell: ignore[no-inline-production-constants]: KAT JSON field key, not the migrate_mark column constant
        account_id=d["account_id"],
        amount=Cents(d["amount"]),
        status=d["status"],
        posting=datetime.fromisoformat(d["posting"]),
        transfer_id=d["transfer_id"],
        transfer_parent_id=d.get("transfer_parent_id"),
        rail_name=d.get("rail_name"),
        template_name=d.get("template_name"),
        bundle_id=d.get("bundle_id"),
        account_scope=d.get("account_scope", "internal"),
        account_role=d.get("account_role"),
        account_parent_role=d.get("account_parent_role"),
    )


def _bal(d: dict[str, Any]) -> BalanceRow:
    expected_eod = d.get("expected_eod")
    day_end = d.get("day_end")
    return BalanceRow(
        account_id=d["account_id"],
        entry=d["entry"],  # typing-smell: ignore[no-inline-production-constants]: KAT JSON field key, not the migrate_mark column constant
        day=date.fromisoformat(d["day"]),
        money=Cents(d["money"]),
        day_end=None if day_end is None else datetime.fromisoformat(day_end),
        expected_eod=None if expected_eod is None else Cents(expected_eod),
        account_scope=d.get("account_scope", "internal"),
        account_role=d.get("account_role"),
        account_parent_role=d.get("account_parent_role"),
    )


def _state(d: dict[str, Any]) -> ResidualState:
    return ResidualState(
        legs=tuple(_leg(x) for x in d.get("legs", ())),
        balances=tuple(_bal(x) for x in d.get("balances", ())),
    )


def _cents_or_none(raw: int | None) -> Cents | None:
    return None if raw is None else Cents(raw)


def _edge(raw: list[Any]) -> TrailEdge:
    return TrailEdge(str(raw[0]), str(raw[1]), str(raw[2]), int(raw[3]))


def _gap(raw: list[Any]) -> CadenceGap:
    return CadenceGap(str(raw[0]), date.fromisoformat(str(raw[1])), str(raw[2]))


def _run_vector(invariant: str, vec: dict[str, Any]) -> object:
    """Dispatch one KAT vector to its residual; returns the raw result."""
    cell = vec.get("cell", {})
    params = vec.get("params", {})
    if invariant in ("stuck_pending", "stuck_unbundled"):
        leg = _leg(vec["leg"])
        fn = stuck_pending_residual if invariant == "stuck_pending" else stuck_unbundled_residual
        return fn(leg, params["cap_seconds"], datetime.fromisoformat(params["as_of"]))
    state = _state(vec["state"])
    day = date.fromisoformat(cell["day"]) if "day" in cell else None
    if invariant == "drift":
        assert day is not None
        return drift_residual(state, cell["account_id"], day)
    if invariant == "ledger_drift":
        assert day is not None
        return ledger_drift_residual(state, cell["parent_account_id"], day)
    if invariant == "overdraft":
        assert day is not None
        return overdraft_residual(state, cell["account_id"], day)
    if invariant == "expected_eod":
        assert day is not None
        return expected_eod_residual(state, cell["account_id"], day)
    if invariant == "limit_breach":
        assert day is not None
        return limit_breach_residual(
            state, cell["account_id"], day, cell["rail_name"], cell["direction"],
            _cents_or_none(params["cap"]),
        )
    if invariant == "chain_parent":
        return chain_parent_residual(state, cell["transfer_id"], cell["template_name"])
    if invariant == "xor_group":
        return xor_group_residual(
            state, cell["transfer_id"], cell["template_name"],
            frozenset(params["member_rails"]),
        )
    if invariant == "fan_in":
        return fan_in_residual(state, cell["child_transfer_id"], params["expected_parent_count"])
    if invariant == "multi_xor":
        return multi_xor_residual(
            state, cell["parent_transfer_id"], cell["parent_name"],
            frozenset(params["child_names"]),
        )
    if invariant == "money_trail":
        emitted = frozenset(_edge(e) for e in vec["emitted"])
        return money_trail_residual(state, emitted)
    if invariant == "cadence_gap":
        emitted_gaps = frozenset(_gap(g) for g in vec["emitted"])
        return cadence_gap_residual(
            state, params["singleton_cadences"], params["role_cadences"],
            emitted_gaps,
        )
    raise AssertionError(f"no dispatcher for invariant {invariant!r}")


def _expected_value(invariant: str, kind: str, raw: object) -> object:
    if invariant == "money_trail":
        assert isinstance(raw, list)
        edges = cast("list[list[Any]]", raw)
        return frozenset(_edge(e) for e in edges)
    if invariant == "cadence_gap":
        assert isinstance(raw, list)
        gaps = cast("list[list[Any]]", raw)
        return frozenset(_gap(g) for g in gaps)
    if kind == "MONEY":
        assert raw is None or isinstance(raw, int)
        return _cents_or_none(raw)
    return raw  # CARDINALITY / THRESHOLD: int | None


def _all_vectors() -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for path in sorted(KATS_DIR.glob("*.json")):
        payload = json.loads(path.read_text())
        for vec in payload["vectors"]:
            out.append((payload["invariant"], payload["kind"], vec))
    return out


@pytest.mark.parametrize(
    ("invariant", "kind", "vec"),
    _all_vectors(),
    ids=[f"{inv}-{v['name']}" for inv, _, v in _all_vectors()],
)
def test_kat_vector(invariant: str, kind: str, vec: dict[str, Any]) -> None:
    result = _run_vector(invariant, vec)
    expected = _expected_value(invariant, kind, vec["expected"])
    assert result == expected, (
        f"{invariant}/{vec['name']}: residual returned {result!r}, "
        f"hand-derived expectation is {expected!r}"
        + (f" — {vec['comment']}" if "comment" in vec else "")
    )


def test_every_kat_file_has_vectors() -> None:
    """A KAT file with zero vectors is a silent coverage hole."""
    files = list(KATS_DIR.glob("*.json"))
    assert files, "no KAT files found — path broke"
    for path in files:
        payload = json.loads(path.read_text())
        assert payload["vectors"], f"{path.name} carries no vectors"
