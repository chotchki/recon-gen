"""DM.3 — Daily Statement day-picker availability decoration (App2-only).

Three load-bearing surfaces locked here:

- **SQL projection** (`_day_availability_sql`) — the UNION-ALL over
  ``current_transactions`` + ``current_daily_balances``, matched on the
  derived ``account_display`` expression + a day-range window. Shape
  assertion (not byte-identity) across dialects per
  ``[[feedback_sql_dialect_convergence_preferred]]``.
- **Row-collapse + empty-state** (`make_day_availability_fetcher`) — the
  ``(business_day, source)`` rows collapse to ``{iso: [tags]}``; an
  empty / sentinel account returns ``{}`` with NO SQL fired.
- **Render markup** (`_render_parameter_date`) — the App2 picker emits
  ``data-day-availability-url`` + ``data-account-param`` + a
  ``role="status"`` empty-state hint ONLY when the spec carries both
  the URL and the source account param; otherwise the legacy
  undecorated picker.

Design lock: ``docs/audits/dm_0_daily_statement_app2_cascade.md``
§"Day-availability endpoint contract" + §"Empty-state UX".
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime

from typing import cast

import pytest

from recon_gen.apps.l1_dashboard.app import (
    P_L1_DS_ACCOUNT,
    P_L1_DS_BALANCE_DATE,
)
from recon_gen.apps.l1_dashboard.datasets import (
    _L1_DS_ACCOUNT_SENTINEL,
    P_L1_DATE_START,
)
from recon_gen.common.config import Config
from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.html._tree_fetcher import (
    _coerce_iso_date,
    _day_availability_sql,
    make_day_availability_fetcher,
)
from recon_gen.common.html.render import ParameterDateSpec, _render_parameter_date
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests._test_helpers import make_test_config

_ACCOUNT = str(P_L1_DS_ACCOUNT)
_BALANCE_DATE = str(P_L1_DS_BALANCE_DATE)


# --------------------------------------------------------------------------
# SQL projection shape.
# --------------------------------------------------------------------------

def test_day_availability_sql_unions_both_matviews() -> None:
    sql = _day_availability_sql("recon")
    # Both source matviews, both tags, the UNION ALL, the window bounds.
    assert "recon_current_transactions" in sql
    assert "recon_current_daily_balances" in sql
    assert "'transactions'" in sql
    assert "'balance'" in sql
    assert "UNION ALL" in sql
    assert ":p_account" in sql
    assert ":p_wstart" in sql
    assert ":p_wend" in sql
    # Day truncation so the per-day map keys are bare dates. The two
    # arms key off DIFFERENT day columns (canonical L1 date-column map):
    # transactions → ``posting`` (a timestamp); daily_balances →
    # ``business_day_start`` (stored day). Regression guard for the
    # column-name bug the DuckDB smoke caught (the design doc's SQL had
    # ``business_day_start`` on the transactions arm, which doesn't
    # exist on that matview).
    assert "CAST(posting AS DATE)" in sql
    assert "CAST(business_day_start AS DATE)" in sql
    # account_display matched via the NULL-safe COALESCE expression
    # (CQ.1 single-source shape), not a stored column.
    assert "COALESCE(account_name, account_id)" in sql


# --------------------------------------------------------------------------
# Row-collapse + empty-state (fetcher).
# --------------------------------------------------------------------------

class _StubPool:
    """Stand-in async pool — the fetcher never reaches it on the empty
    account path; a non-empty path uses ``execute_visual_sql_async`` which
    we don't exercise here (covered by the db tier). This stub asserts the
    'no SQL fired' contract: if the fetcher touches the pool, ``acquire``
    raises."""

    def acquire(self) -> object:  # pragma: no cover - must NOT be called
        raise AssertionError("empty/sentinel account must not query the DB")


def _make_cfg() -> Config:
    return make_test_config(db_table_prefix=DEFAULT_PREFIX)


@pytest.mark.parametrize("account", ["", _L1_DS_ACCOUNT_SENTINEL])
def test_empty_or_sentinel_account_returns_empty_map_no_sql(account: str) -> None:
    """Empty / sentinel account → empty map, no SQL fired (the stub pool
    raises if touched)."""
    cfg = _make_cfg()
    # cast: _StubPool intentionally implements only ``acquire`` (which
    # raises) to prove the empty/sentinel path never reaches the DB; it
    # is not a full AsyncConnectionPool, which is exactly the point.
    pool = cast(AsyncConnectionPool, _StubPool())
    fetcher = make_day_availability_fetcher(cfg, pool=pool)
    result = asyncio.run(fetcher(account, "2030-01-01", "2030-01-31"))
    assert result == {}


def test_coerce_iso_date_handles_date_datetime_and_string() -> None:
    assert _coerce_iso_date(date(2030, 1, 5)) == "2030-01-05"
    assert _coerce_iso_date(datetime(2030, 1, 5, 12, 0)) == "2030-01-05"
    assert _coerce_iso_date("2030-01-05") == "2030-01-05"
    assert _coerce_iso_date("2030-01-05T00:00:00") == "2030-01-05"
    assert _coerce_iso_date("nope") is None


# --------------------------------------------------------------------------
# Render markup — decoration attributes + empty-state hint.
# --------------------------------------------------------------------------

def test_render_emits_day_availability_attrs_when_wired() -> None:
    spec = ParameterDateSpec(
        name=_BALANCE_DATE, label="Business Day",
        day_availability_account_param=_ACCOUNT,
        day_availability_url="/dashboards/l1/sheets/daily-statement/day-availability",
    )
    out = _render_parameter_date(spec)
    assert "data-day-availability-url=" in out
    assert "/day-availability" in out
    # The JS reads the account picker by name — must carry the param_ prefix.
    assert f'data-account-param="param_{_ACCOUNT}"' in out
    # Empty-state hint: role="status" + aria-live, located by visible text
    # in e2e (not by class). Renders hidden by default.
    assert 'role="status"' in out
    assert 'aria-live="polite"' in out
    assert "day-picker-empty-window" in out
    assert "hidden" in out


def test_render_skips_decoration_when_no_url_stamped() -> None:
    """A date spec WITHOUT a stamped day_availability_url (the common
    case: range pickers, decoration-less single pickers, no fetcher
    wired) renders the legacy undecorated picker."""
    spec = ParameterDateSpec(
        name=_BALANCE_DATE, label="Business Day",
        # account param present (tree declared it) but no URL stamped
        # (no fetcher on this dashboard) → no decoration.
        day_availability_account_param=_ACCOUNT,
        day_availability_url=None,
    )
    out = _render_parameter_date(spec)
    assert "data-day-availability-url=" not in out
    assert "day-picker-empty-window" not in out


def test_render_legacy_date_spec_unchanged() -> None:
    """A plain date spec (no DM.3 fields) renders exactly as before —
    no decoration attrs, no hint."""
    spec = ParameterDateSpec(name=P_L1_DATE_START, label="Date From")
    out = _render_parameter_date(spec)
    assert "data-day-availability-url=" not in out
    assert "data-account-param=" not in out
    assert "day-picker-empty-window" not in out
    # Still the normal flatpickr-single target.
    assert 'data-widget="flatpickr-single"' in out
    assert f'name="param_{P_L1_DATE_START}"' in out
