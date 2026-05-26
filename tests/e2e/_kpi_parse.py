"""BG.X — shared KPI value parsers for the honest-gate tests.

Both renderers emit KPI values as strings — App2 stamps
``"$1,234.56"`` / ``"3,032"`` / etc; QS does the same via its
numeric-format-applied DOM read. Honest assertions compare the
rendered string (parsed) to a Decimal/int from ``driver.query_db``.

Two parsers, two contracts:

- ``_parse_currency_kpi`` — currency KPIs (``$``-prefixed). **Strict
  ≤2 decimal places** per ``feedback_kpi_currency_decimals_strict``
  (user 2026-05-25: "a 3 decimal place currency IS a test failure").
  v11.21.0 cold-read finding #14 surfaced 3-decimal precision on
  flagship sheets; the parser gates every BG.X KPI read site so
  individual tightenings don't need to re-enforce.

- ``_parse_int_kpi`` — count KPIs (e.g. "Internal Accounts in
  Overdraft", "Leaf Accounts in Drift"). Accepts integer-shaped
  strings with optional thousands commas. Decimal-point content
  raises — a count KPI showing ``"3.5"`` is a wrong-binding bug
  shape worth tripping the test, not a parser tolerance.

Both raise ``AssertionError`` with messages naming the parse failure
shape — the BG.X tightenings get the call-site context for free.
"""

from __future__ import annotations

import re
from decimal import Decimal


_CURRENCY_RE = re.compile(r"[^0-9.\-]")
_DECIMAL_PART_RE = re.compile(r"\.(\d+)")
_INT_RE = re.compile(r"[^0-9\-]")


def parse_currency_kpi(text: str | None) -> Decimal:
    """Parse a currency-shaped KPI string (``"$1,234.56"``,
    ``"-$1,234.56"``, ``"$-1,234.56"``) into a Decimal.

    **Strict ≤2 decimal-place contract** (v11.21.0 cold-read finding
    #14, user-confirmed 2026-05-25 as a test failure shape, not a
    tolerated edge): currency KPIs must render with 0, 1, or 2 decimal
    digits. 3+ decimals (``"308,535.982"``) is a real misread risk
    (cold-read: two of four judges misread ``-308,535.982`` as
    ``-$308M``) AND indicates the KPI's numeric-format is unset /
    wrong. Parser raises on 3+ — the trip surfaces the bug at the
    call site instead of silently rounding away the noise.

    ``None`` raises — a missing KPI is a different failure shape than
    a parse failure.
    """
    if text is None:
        raise AssertionError("KPI value was None — visual didn't render?")
    cleaned = _CURRENCY_RE.sub("", text)
    if cleaned in ("", "-"):
        raise AssertionError(f"KPI value {text!r} parses to empty after cleanup")
    decimal_match = _DECIMAL_PART_RE.search(cleaned)
    if decimal_match is not None and len(decimal_match.group(1)) > 2:
        raise AssertionError(
            f"KPI value {text!r} renders with "
            f"{len(decimal_match.group(1))} decimal places; currency "
            f"KPIs must render with ≤2 decimals (v11.21.0 cold-read "
            f"finding #14 — 3-decimal rendering creates real misread "
            f"risk vs $-millions scale). Audit the visual's "
            f"numerical(currency=True) wiring + the common/models.py "
            f"format string."
        )
    return Decimal(cleaned)


def parse_int_kpi(text: str | None) -> int:
    """Parse a count-shaped KPI string (``"3,032"``, ``"0"``, ``"-1"``)
    into an int. Decimal content raises — a count KPI carrying decimals
    is a wrong-measure-binding shape (e.g. SUM where COUNT was
    intended) and the test should trip on it."""
    if text is None:
        raise AssertionError("KPI value was None — visual didn't render?")
    if "." in text:
        raise AssertionError(
            f"KPI value {text!r} carries a decimal point; count-shaped "
            f"KPIs must render as integers. Wrong measure binding "
            f"(SUM vs COUNT)?"
        )
    cleaned = _INT_RE.sub("", text)
    if cleaned in ("", "-"):
        raise AssertionError(f"KPI value {text!r} parses to empty after cleanup")
    return int(cleaned)
