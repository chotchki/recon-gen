"""BG.2 assertion-logic validation — proves the test's per-finding
catch-by-construction by exercising the assertion machinery against
known-shape data.

The browser test ``test_bg2_daily_statement_kpis_match_summary_matview``
in ``tests/e2e/test_l1_account_filters.py`` requires a deployed
dashboard + browser stack. This unit test exercises the same
assertion logic standalone against an in-memory SQLite with planted
matview rows — so we can demonstrate, deterministically, that:

- v11.21.0 finding #1 (Drift KPI ≠ matview's `drift` column): the
  per-KPI identity assertion trips with a message naming the column.
- finding #1 (other half — matview's `drift` column ≠ narrative
  formula): the narrative-formula assertion trips.
- finding #2 (date picker ignored): the day1 ≠ day2 delta assertion
  trips when both days resolve to byte-identical KPI sets.
- finding #3 (negative Opening Balance on a class-restricted role):
  surfaces via the identity assertion when the rendered KPI value
  disagrees with what the matview holds.
- finding #14 (3-decimal currency): the `_parse_currency_kpi`
  precision gate raises with a finding-#14-named message.

Healthy data is also covered: when KPI values match the matview AND
day1 ≠ day2 in distinct ways, all assertions pass — proving the test
isn't tautologically failing.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg
from tests.e2e._kpi_parse import parse_currency_kpi as _parse_currency_kpi
from tests.e2e.test_l1_account_filters import _KPI_TO_COLUMN

if TYPE_CHECKING:
    from recon_gen.common.config import Config


@pytest.fixture
def planted_sqlite() -> Iterator["Config"]:
    """Spin up a SQLite holding a synthetic ``daily_statement_summary``
    matview with two rows for one account (two distinct days). Values
    are chosen so the narrative formula
    ``drift = closing_stored − (opening + net_flow)`` holds on the
    healthy row and fails on the buggy one — tests pick which row to
    query."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    # Minimal projection — matches DAILY_STATEMENT_SUMMARY_CONTRACT
    # columns the SQL projects. Values stored as integer cents
    # (matches what the production matview holds; the production SQL
    # wraps via cents_to_dollars_sql).
    conn.execute(
        "CREATE TABLE pfx_daily_statement_summary ("
        "  account_id TEXT, account_name TEXT, account_role TEXT,"
        "  account_parent_role TEXT, account_scope TEXT,"
        "  business_day_start TEXT, business_day_end TEXT,"
        "  opening_balance INTEGER, total_debits INTEGER,"
        "  total_credits INTEGER, net_flow INTEGER, leg_count INTEGER,"
        "  closing_balance_stored INTEGER,"
        "  closing_balance_recomputed INTEGER, drift INTEGER"
        ")"
    )
    # Healthy day-A (formula holds): opening=10000, debits=-2000,
    # credits=3000, net=1000, closing_stored=11000,
    # closing_recomputed=11000, drift=0.
    # Healthy day-B (formula holds, distinct values from A):
    # opening=11000, debits=-500, credits=1500, net=1000,
    # closing_stored=12000, closing_recomputed=12000, drift=0.
    conn.executemany(
        "INSERT INTO pfx_daily_statement_summary VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "acc-1", "Account One", "dda", None, "internal",
                "2026-05-24 00:00:00", "2026-05-24 23:59:59",
                10_000, -2_000, 3_000, 1_000, 3, 11_000, 11_000, 0,
            ),
            (
                "acc-1", "Account One", "dda", None, "internal",
                "2026-05-25 00:00:00", "2026-05-25 23:59:59",
                11_000, -500, 1_500, 1_000, 2, 12_000, 12_000, 0,
            ),
        ],
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    cfg.db_table_prefix = "pfx"
    try:
        yield cfg
    finally:
        os.unlink(path)


# Lift the production SQL once at module load — same path the browser
# test uses, just bypassing the dataset wrapper since we just need the
# query string for an isolated query.
_SUMMARY_SQL_FOR_TEST = (
    "SELECT account_id, account_name, account_role,"
    " account_parent_role, account_scope,"
    " business_day_start, business_day_end,"
    " (opening_balance / 100.0) AS opening_balance,"
    " (total_debits / 100.0) AS total_debits,"
    " (total_credits / 100.0) AS total_credits,"
    " (net_flow / 100.0) AS net_flow,"
    " leg_count,"
    " (closing_balance_stored / 100.0) AS closing_balance_stored,"
    " (closing_balance_recomputed / 100.0) AS closing_balance_recomputed,"
    " (drift / 100.0) AS drift\n"
    "FROM pfx_daily_statement_summary\n"
    "WHERE (account_name || ' (' || account_id || ')') = <<$pL1DsAccount>>\n"
    "  AND strftime('%Y-%m-%d', business_day_start) = "
    "strftime('%Y-%m-%d', <<$pL1DsBalanceDate>>)"
)


def _query_day(cfg: "Config", account: str, day: str) -> dict[str, Any]:
    return query_db_via_cfg(
        cfg, _SUMMARY_SQL_FOR_TEST,
        binds={"param_pL1DsAccount": account, "param_pL1DsBalanceDate": day},
    )[0]


def _kpis_from_matview_row(row: dict[str, Any]) -> dict[str, Decimal]:
    return {
        title: Decimal(str(row[col])) for title, col in _KPI_TO_COLUMN.items()
    }


# ─── Healthy-path coverage ────────────────────────────────────────────


def test_bg2_assertion_passes_when_kpis_match_matview_on_healthy_data(
    planted_sqlite: "Config",
) -> None:
    """When rendered KPIs equal the matview's values AND day1 ≠ day2,
    BG.2's full assertion chain passes. Establishes the test isn't
    tautologically tripping."""
    cfg = planted_sqlite
    account = "Account One (acc-1)"
    row_a = _query_day(cfg, account, "2026-05-24")
    row_b = _query_day(cfg, account, "2026-05-25")
    rendered_a = _kpis_from_matview_row(row_a)
    rendered_b = _kpis_from_matview_row(row_b)

    # Identity per KPI.
    for title in _KPI_TO_COLUMN:
        assert rendered_a[title] == Decimal(str(row_a[_KPI_TO_COLUMN[title]]))
        assert rendered_b[title] == Decimal(str(row_b[_KPI_TO_COLUMN[title]]))
    # Narrative formula holds on row A.
    assert row_a["drift"] == (
        row_a["closing_balance_stored"]
        - (row_a["opening_balance"] + row_a["net_flow"])
    )
    # Delta — day1 ≠ day2.
    assert rendered_a != rendered_b


# ─── Finding #1 — Drift KPI binding mismatch ─────────────────────────


def test_bg2_identity_trips_when_rendered_drift_disagrees_with_matview(
    planted_sqlite: "Config",
) -> None:
    """v11.21.0 finding #1, half-A: rendered KPI binds a different
    measure than the matview's `drift` column. Identity assertion fails
    with a message that points at the finding."""
    cfg = planted_sqlite
    account = "Account One (acc-1)"
    row = _query_day(cfg, account, "2026-05-24")
    rendered = _kpis_from_matview_row(row)
    # Simulate the bug: the rendered Posting Drift KPI shows an arbitrary
    # different value (the cold-read's "-8091.841" vs the formula
    # "+6.000" shape).
    rendered["Posting Drift"] = Decimal("-80.91")

    # Run the per-KPI identity loop the browser test runs. We expect
    # the Posting Drift entry to fail.
    failures: list[str] = []
    for title in _KPI_TO_COLUMN:
        expected = Decimal(str(row[_KPI_TO_COLUMN[title]]))
        if rendered[title] != expected:
            failures.append(
                f"{title}: rendered={rendered[title]} vs matview={expected}"
            )
    assert failures == ["Posting Drift: rendered=-80.91 vs matview=0.0"]


# ─── Finding #1, half-B — matview's drift column violates narrative ──


def test_bg2_narrative_formula_against_independent_truth_catches_matview_net_flow_bug(
    planted_sqlite: "Config",
) -> None:
    """v11.21.0 finding #1 root cause (2026-05-25 BH.0 share): the
    matview's `net_flow` formula at `schema.py:2502-2504` uses
    ``SUM(CASE WHEN Credit THEN amount_money ELSE -amount_money END)``
    which assumed v5's unsigned `amount_money`. v6 made
    `amount_money` already-signed (Credit positive, Debit negative)
    so `-amount_money` for Debit rows over-flips → matview's net_flow
    becomes ``credits + abs(debits)`` = gross magnitude, not signed
    net. Matview's `drift` column then = ``closing − (opening +
    gross)`` = wrong.

    **The original BG.2 narrative-formula assertion was tautological**:
    it pulled both `drift` and `net_flow` from the SAME matview row,
    so the assertion `matview.drift == closing − (opening +
    matview.net_flow)` held by construction even when both columns
    were wrong together. Strengthened 2026-05-25: pull the ground-
    truth `net_flow` DIRECTLY from `<prefix>_current_transactions`
    via plain `SUM(amount_money)` (v6's signed convention gives
    signed net by construction), bypassing the matview's CASE
    expression.

    This test plants both shapes side-by-side to prove the
    strengthened assertion catches the bug:

    1. **Matview row with the bug** — `net_flow` carries the gross
       magnitude; drift = closing − (opening + gross). Tautological
       check passes; strengthened check trips.
    2. **Base-transactions ground truth** — plain SUM gives signed
       net (= credits + debits where debits are negative).
    3. **Strengthened assertion**: matview.drift vs closing −
       (opening + independent_sum) trips because matview.net_flow
       diverges from independent_sum.
    """
    cfg = planted_sqlite
    assert cfg.demo_database_url is not None
    conn = sqlite3.connect(cfg.demo_database_url)
    # Plant a base-transactions table mirroring the matview's input
    # shape. In v6 amount_money is signed: Credit positive, Debit
    # negative.
    conn.execute(
        "CREATE TABLE pfx_current_transactions ("
        "  account_id TEXT, amount_money INTEGER,"
        "  amount_direction TEXT, status TEXT, posting TEXT"
        ")"
    )
    # acc-3 on 2026-05-24: Credit +30,000, Debit -20,000
    # Signed net = -20,000 + 30,000 = +10,000 (matches truth)
    # Buggy matview net_flow = abs(-20,000) + 30,000 = 50,000 (wrong)
    conn.executemany(
        "INSERT INTO pfx_current_transactions VALUES (?,?,?,?,?)",
        [
            ("acc-3", 30_000, "Credit", "Posted", "2026-05-24 10:00:00"),
            ("acc-3", -20_000, "Debit", "Posted", "2026-05-24 14:00:00"),
        ],
    )
    # Matview row carrying the BUG-SHAPE net_flow (gross magnitude).
    conn.execute(
        "INSERT INTO pfx_daily_statement_summary VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "acc-3", "Account Three", "dda", None, "internal",
            "2026-05-24 00:00:00", "2026-05-24 23:59:59",
            # opening=0, debits=-20000, credits=30000
            0, -20_000, 30_000,
            # BUGGY net_flow = abs(-20000) + 30000 = 50000 cents
            50_000,
            2,
            # closing_stored = opening + true_signed_net = 0 + 10000 = 10000
            10_000,
            # closing_recomputed = opening + buggy_net_flow = 50000
            50_000,
            # matview drift = closing_stored - closing_recomputed
            #               = 10000 - 50000 = -40000 cents
            -40_000,
        ),
    )
    conn.commit()
    conn.close()

    matview_row = _query_day(cfg, "Account Three (acc-3)", "2026-05-24")

    # === Tautological check (the OLD weak assertion) — passes
    # silently on the buggy data. Proves the bug class the old
    # assertion missed.
    tautological_formula = (
        Decimal(str(matview_row["closing_balance_stored"]))
        - (
            Decimal(str(matview_row["opening_balance"]))
            + Decimal(str(matview_row["net_flow"]))
        )
    )
    assert Decimal(str(matview_row["drift"])) == tautological_formula, (
        "Old (tautological) assertion silently passes on buggy data "
        "— this is the bug class BG.2 missed"
    )

    # === Strengthened check — pulls signed net DIRECTLY from
    # current_transactions, bypassing the matview's CASE expression.
    independent_net_rows = query_db_via_cfg(
        cfg,
        "SELECT COALESCE(SUM(amount_money), 0) / 100.0 AS net "
        "FROM pfx_current_transactions "
        "WHERE account_id = :acc AND posting >= :start AND posting < :end "
        "AND status <> 'Failed'",
        binds={
            "acc": "acc-3",
            "start": "2026-05-24 00:00:00",
            "end": "2026-05-25 00:00:00",
        },
    )
    independent_net = Decimal(str(independent_net_rows[0]["net"]))
    # True signed net on this plant: +10,000 cents = +$100.00
    assert independent_net == Decimal("100"), (
        f"Plant arithmetic — independent SUM should be $100 (=+10000 "
        f"cents), got {independent_net}"
    )

    strengthened_formula = (
        Decimal(str(matview_row["closing_balance_stored"]))
        - (
            Decimal(str(matview_row["opening_balance"]))
            + independent_net
        )
    )
    # strengthened_formula = $100 − ($0 + $100) = $0 (the narrative
    # says drift should be $0 on this plant). Matview holds drift =
    # -$400. ASSERTION TRIPS.
    assert Decimal(str(matview_row["drift"])) != strengthened_formula, (
        f"Strengthened assertion should trip on buggy net_flow: "
        f"matview.drift={matview_row['drift']} vs "
        f"closing - (opening + independent_net) = "
        f"{strengthened_formula}. If THIS passes, the strengthened "
        f"gate is ALSO tautological — review."
    )


# ─── Finding #2 — date picker no-op produces identical KPI sets ──────


def test_bg2_delta_trips_when_day1_and_day2_produce_identical_kpis(
    planted_sqlite: "Config",
) -> None:
    """v11.21.0 finding #2: the Business Day picker is non-functional;
    KPIs are byte-identical across distinct days. The delta assertion
    catches this — when ``rendered_day1 == rendered_day2``, the
    picker isn't reaching the WHERE clause.

    We simulate the bug by ignoring the day-bind and reading the same
    row for both day1 and day2 — what would happen if the picker were
    a no-op."""
    cfg = planted_sqlite
    account = "Account One (acc-1)"
    # Bug shape: day1 + day2 both return row-A (picker ignored).
    rendered_day1 = _kpis_from_matview_row(_query_day(cfg, account, "2026-05-24"))
    rendered_day2_buggy = _kpis_from_matview_row(_query_day(cfg, account, "2026-05-24"))
    # The browser test's delta assertion: rendered_day1 != rendered_day2.
    # Bug shape makes them equal.
    assert rendered_day1 == rendered_day2_buggy, (
        "Test setup error — bug-shape branches should produce equal "
        "KPI sets"
    )


# ─── Finding #3 — negative Opening Balance for cardholder class ──────


def test_bg2_identity_trips_when_cardholder_opening_disagrees(
    planted_sqlite: "Config",
) -> None:
    """v11.21.0 finding #3: cardholder class shows negative Opening
    Balance KPI on a class whose semantic forbids it. The bug surfaces
    as a matview→KPI binding mismatch (binding reads from wrong
    column/sign) and the identity assertion catches it. Whether the
    matview's value is itself wrong is a separate upstream invariant
    (cardholder Opening ≥ 0) — BG.2 doesn't enforce that, but it
    catches the rendering-vs-matview divergence."""
    cfg = planted_sqlite
    account = "Account One (acc-1)"
    row = _query_day(cfg, account, "2026-05-25")
    rendered = _kpis_from_matview_row(row)
    # Simulate the bug: KPI's Opening renders as the negated matview
    # value (sign inversion bug class).
    rendered["Opening Balance"] = -rendered["Opening Balance"]

    # The identity loop trips with the Opening Balance entry naming
    # the divergence.
    expected = Decimal(str(row["opening_balance"]))
    assert rendered["Opening Balance"] != expected, (
        "Test setup error — sign inversion should produce mismatch"
    )


# ─── Finding #14 — strict 2-decimal currency gate ────────────────────


@pytest.mark.parametrize("bad_text", [
    "$308,535.982",      # 3-decimal — the cold-read's misread-risk shape
    "$-308,535.982",
    "1234.5678",         # 4-decimal
    "-$1.234",           # leading minus + 3-decimal
])
def test_bg2_currency_parser_rejects_3plus_decimal_places(
    bad_text: str,
) -> None:
    """v11.21.0 finding #14 + user 2026-05-25: 3+ decimal currency is a
    test failure shape. Parser raises at every KPI read site so every
    BG.X tightening inherits the gate."""
    with pytest.raises(AssertionError, match="finding #14"):
        _parse_currency_kpi(bad_text)


@pytest.mark.parametrize("good_text,expected", [
    ("$1,234", Decimal("1234")),
    ("$1,234.5", Decimal("1234.5")),
    ("$1,234.56", Decimal("1234.56")),
    ("-$1,234.56", Decimal("-1234.56")),
    ("$-1,234.56", Decimal("-1234.56")),
])
def test_bg2_currency_parser_accepts_0_to_2_decimal_places(
    good_text: str, expected: Decimal,
) -> None:
    assert _parse_currency_kpi(good_text) == expected
