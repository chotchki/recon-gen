"""l1_exceptions (the 13th enumeration target) — the rollup union law.

The rollup regulators actually see has a TRIVIAL residual (DS.0 §3):
its row multiset equals the union of its source matviews projected to
the branch identity shape. The per-detector gates already prove each
source matview == its residual, so this check closes the last hop:
matview-union == rollup (branch column mapping, magnitude routing,
NULL slotting and the ``seq`` disambiguator's multiplicity
preservation). ``balance_cadence_gap`` participates as a source read
(inventory row 14 — unregistered, no residual yet; DS.5 owns its
kit-or-rationale), so the union stays exact on states where it fires.

Identity shape per row: (check_type, account_id, business_day,
rail_name, transfer_id, magnitude_amount, magnitude_count) compared
as a MULTISET (key -> row count) — ``seq`` exists precisely because
distinct violations can share the 5-tuple.
"""
from __future__ import annotations

import datetime as dt
from typing import Final

from tests.enumeration.domains._base import as_int
from tests.enumeration.harness import EnumerationDB, ViolationMap

#: (check_type, projection SQL producing exactly the branch's
#: l1_exceptions column mapping) per source matview, mirroring the
#: emitter's twelve UNION branches.
_SOURCE_PROJECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("drift",
     "SELECT account_id, business_day_start, NULL, NULL, ABS(drift), NULL "
     "FROM {p}_drift"),
    ("ledger_drift",
     "SELECT account_id, business_day_start, NULL, NULL, ABS(drift), NULL "
     "FROM {p}_ledger_drift"),
    ("overdraft",
     "SELECT account_id, business_day_start, NULL, NULL, "
     "ABS(stored_balance), NULL FROM {p}_overdraft"),
    ("limit_breach",
     "SELECT account_id, business_day, rail_name, NULL, "
     "outbound_total - cap, NULL FROM {p}_limit_breach"),
    ("expected_eod_balance_breach",
     "SELECT account_id, business_day_start, NULL, NULL, ABS(variance), "
     "NULL FROM {p}_expected_eod_balance_breach"),
    ("balance_cadence_gap",
     "SELECT account_id, business_day_start, gap_kind, NULL, "
     "ABS(COALESCE(gap_day_net_flow, 0)), gap_day_leg_count "
     "FROM {p}_balance_cadence_gap"),
    ("stuck_pending",
     "SELECT account_id, CAST(posting AS DATE), rail_name, transfer_id, "
     "amount_money, NULL FROM {p}_stuck_pending"),
    ("stuck_unbundled",
     "SELECT account_id, CAST(posting AS DATE), rail_name, transfer_id, "
     "amount_money, NULL FROM {p}_stuck_unbundled"),
    ("chain_parent_disagreement",
     "SELECT NULL, business_day, child_template_name, transfer_id, NULL, "
     "distinct_parent_count FROM {p}_chain_parent_disagreement"),
    ("xor_group_violation",
     "SELECT NULL, business_day, template_name, transfer_id, NULL, "
     "firing_count FROM {p}_xor_group_violation"),
    ("fan_in_disagreement",
     "SELECT NULL, business_day, child_template_name, child_transfer_id, "
     "NULL, parent_count FROM {p}_fan_in_disagreement"),
    ("multi_xor_violation",
     "SELECT NULL, business_day, parent_rail_or_template_name, "
     "parent_transfer_id, NULL, child_count FROM {p}_multi_xor_violation"),
)


def _normalize(
    check_type: str, row: tuple[object, ...],
) -> tuple[object, ...]:
    account, day, rail, transfer, amount, count = row
    if isinstance(day, dt.datetime):
        day = day.date()
    return (
        check_type, account, day, rail, transfer,
        None if amount is None else as_int(amount),
        None if count is None else as_int(count),
    )


def _count(rows: list[tuple[object, ...]]) -> ViolationMap:
    out: ViolationMap = {}
    for key in rows:
        current = out.get(key, 0)
        assert isinstance(current, int)
        out[key] = current + 1
    return out


def union_maps(db: EnumerationDB) -> tuple[ViolationMap, ViolationMap]:
    """(rollup multiset, source-union multiset) for one packed DB."""
    engine_rows = [
        _normalize(str(row[0]), tuple(row[1:]))
        for row in db.fetchall(
            f"SELECT check_type, account_id, business_day, rail_name, "
            f"transfer_id, magnitude_amount, magnitude_count "
            f"FROM {db.prefix}_l1_exceptions",
        )
    ]
    source_rows: list[tuple[object, ...]] = []
    for check_type, sql in _SOURCE_PROJECTIONS:
        source_rows.extend(
            _normalize(check_type, row)
            for row in db.fetchall(sql.format(p=db.prefix))
        )
    return _count(engine_rows), _count(source_rows)
