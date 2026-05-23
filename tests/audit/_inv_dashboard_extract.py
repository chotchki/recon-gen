"""Extract per-invariant table row counts from the deployed Investigation
dashboard (AT.5.b — L2 sibling of ``_dashboard_extract``).

Counterpart to ``_matview_extract.count_anomaly_matview_rows`` /
``count_money_trail_matview_rows``. AT.5's L2 4-way agreement chain
extends AT.5.a (spine ⋈ direct matview) onto the Investigation
dashboard:

    spine_plants  ⊆  direct_matview(filtered)  ==  App2  ==  QS

This module owns the per-L2-invariant sheet / table mapping + the
parameter-control-state write (σ slider for anomaly, chain-root
dropdown for money_trail). The agreement test pegs the renderer to a
known parameter value, reads the resulting table, then compares
against the matview filtered by the *same* parameter.

Speaks the X.2.q ``DashboardDriver`` protocol — the QS and App2 paths
both flow through ``set_slider`` / ``pick_filter`` / ``table_rows``
without renderer-specific code at the call site. Caller is responsible
for ``driver.open(dashboard_id)`` before calling.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from tests.e2e._drivers import DashboardDriver


L2Invariant = Literal["anomaly", "money_trail"]


# Maps invariant → (sheet_name, table_visual_title, param_control_label).
#
# Visual titles + control labels match what ``apps/investigation/app.py``
# declares (customer-facing strings; never auto-derived internal IDs).
# Pre-AT.5.b this mapping lived only in the source app's docstrings;
# extracting it here means the agreement test asserts against the
# concrete table + control the analyst actually drives.
_DASHBOARD_LAYOUT: dict[L2Invariant, tuple[str, str, str]] = {
    "anomaly": (
        "Volume Anomalies", "Flagged Pair-Windows — Ranked", "Min sigma",
    ),
    "money_trail": (
        "Money Trail", "Money Trail — Hop-by-Hop", "Chain root transfer",
    ),
}


# Natural-key columns per L2 invariant. The flat-shape pattern (one row
# per matview row) holds for both detail tables — the table's group_by
# is exactly the column set the matview row is uniquely identified by
# under the dashboard's filter state.
#
# For anomaly the table also shows ``recipient_account_name`` /
# ``sender_account_name`` columns alongside the IDs, but the names are
# derived from the IDs (matview's denormalized columns); the IDs +
# window_end are the irreducible identity. For money_trail the
# ``transfer_id`` is globally unique across the matview, so depth
# alone is redundant for identity — including it makes the key shape
# match the matview's edge surface (one row per (chain, edge)).
_KEY_COLS: dict[L2Invariant, tuple[str, ...]] = {
    "anomaly": (
        "sender_account_id", "recipient_account_id", "window_end",
    ),
    "money_trail": ("transfer_id", "depth"),
}


_DATE_COLS = frozenset({"window_end"})
_INT_COLS = frozenset({"depth"})


def key_columns_for(invariant: L2Invariant) -> tuple[str, ...]:
    """Natural-key column names for ``invariant`` (see ``_KEY_COLS``)."""
    return _KEY_COLS[invariant]


def layout_for(invariant: L2Invariant) -> tuple[str, str, str]:
    """``(sheet_name, table_visual_title, param_control_label)``."""
    return _DASHBOARD_LAYOUT[invariant]


def _parse_window_end(cell: str) -> date:
    """Coerce a window_end cell to a ``date``. Same shape as
    ``_dashboard_extract._parse_day_cell`` — handles ISO heads
    (App2 raw ``2030-01-01 00:00:00``) + the QS locale form
    (``"January 1, 2030"``). Returns the date portion only."""
    iso_head = cell[:10]
    try:
        return date.fromisoformat(iso_head)
    except ValueError:
        pass
    head = " ".join(cell.split()[:3])
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"_parse_window_end: cannot parse {cell!r} as ISO or "
        f"locale-rendered date"
    )


def _set_anomaly_threshold(
    driver: DashboardDriver, sigma: float,
) -> None:
    """Set the σ slider control to ``sigma`` and block until the table
    re-fetches. ``set_slider`` passes the single value as ``lo`` (the
    App2 driver maps single-value sliders to the parameter input)."""
    _sheet, _table, control = _DASHBOARD_LAYOUT["anomaly"]
    driver.set_slider(control, sigma, None)


def _set_money_trail_root(
    driver: DashboardDriver, root_transfer_id: str,
) -> None:
    """Set the chain-root dropdown to ``root_transfer_id`` and block
    until the table re-fetches. The dropdown's default sentinel
    matches nothing in the matview — without this write, the table is
    empty by construction."""
    _sheet, _table, control = _DASHBOARD_LAYOUT["money_trail"]
    driver.pick_filter(control, [root_transfer_id])


def _go_to_anomaly_sheet(
    driver: DashboardDriver, sigma: float,
) -> str:
    """Switch to "Volume Anomalies", set the σ slider, return the
    detail-table visual title."""
    sheet, table, _control = _DASHBOARD_LAYOUT["anomaly"]
    driver.goto_sheet(sheet)
    _set_anomaly_threshold(driver, sigma)
    return table


def _go_to_money_trail_sheet(
    driver: DashboardDriver, root_transfer_id: str,
) -> str:
    """Switch to "Money Trail", pick the chain root, return the
    detail-table visual title."""
    sheet, table, _control = _DASHBOARD_LAYOUT["money_trail"]
    driver.goto_sheet(sheet)
    _set_money_trail_root(driver, root_transfer_id)
    return table


def count_anomaly_rows(driver: DashboardDriver, sigma: float) -> int:
    """Total rows in the "Flagged Pair-Windows — Ranked" table when the
    σ slider is at ``sigma``. Uses the page-size-bump path on QS;
    counts the rendered rows on App2."""
    table = _go_to_anomaly_sheet(driver, sigma)
    return driver.table_row_count(table)


def count_money_trail_rows(
    driver: DashboardDriver, root_transfer_id: str,
) -> int:
    """Total rows in the "Money Trail — Hop-by-Hop" table when the
    chain-root dropdown picks ``root_transfer_id``."""
    table = _go_to_money_trail_sheet(driver, root_transfer_id)
    return driver.table_row_count(table)


def anomaly_row_keys(
    driver: DashboardDriver, sigma: float,
) -> set[tuple[str | date, ...]]:
    """Natural-key tuples shown in the "Flagged Pair-Windows — Ranked"
    table when σ slider is at ``sigma``.

    Window_end cells parse to ``date`` so they compare against the
    matview key set (which normalises the column the same way).

    Caller's responsibility: ``table_rows`` returns the DOM-visible
    window — the caller asserts ``rows_seen == total`` first so a
    truncated window fails loudly rather than passing partial."""
    table = _go_to_anomaly_sheet(driver, sigma)
    key_cols = _KEY_COLS["anomaly"]
    rows = driver.table_rows(table, columns=key_cols)
    out: set[tuple[str | date, ...]] = set()
    for r in rows:
        key: list[str | date] = []
        for sql_col in key_cols:
            cell = r[sql_col].strip()
            key.append(
                _parse_window_end(cell) if sql_col in _DATE_COLS else cell
            )
        out.add(tuple(key))
    return out


def money_trail_row_keys(
    driver: DashboardDriver, root_transfer_id: str,
) -> set[tuple[str | int, ...]]:
    """Natural-key tuples ``(transfer_id, depth)`` shown in the "Money
    Trail — Hop-by-Hop" table when the chain root is ``root_transfer_id``.

    Depth cells parse to ``int`` so they compare against the matview
    key set (the matview emits depth as INTEGER)."""
    table = _go_to_money_trail_sheet(driver, root_transfer_id)
    key_cols = _KEY_COLS["money_trail"]
    rows = driver.table_rows(table, columns=key_cols)
    out: set[tuple[str | int, ...]] = set()
    for r in rows:
        key: list[str | int] = []
        for sql_col in key_cols:
            cell = r[sql_col].strip()
            key.append(int(cell) if sql_col in _INT_COLS else cell)
        out.add(tuple(key))
    return out


def rows_seen_anomaly(driver: DashboardDriver, sigma: float) -> int:
    """How many rows ``table_rows`` actually returned for the anomaly
    table. Caller compares this to ``count_anomaly_rows`` (the page-
    bumped total) to confirm the row-identity comparison sees the full
    set, not a truncated window."""
    table = _go_to_anomaly_sheet(driver, sigma)
    return len(driver.table_rows(table))


def rows_seen_money_trail(
    driver: DashboardDriver, root_transfer_id: str,
) -> int:
    """How many rows ``table_rows`` actually returned for the
    money_trail table — same truncation-guard rationale as the anomaly
    sibling."""
    table = _go_to_money_trail_sheet(driver, root_transfer_id)
    return len(driver.table_rows(table))
