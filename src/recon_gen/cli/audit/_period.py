"""BC.2 — ``--period`` Click custom type producing ``DateInterval``.

Replaces the v1 ``--from X --to Y`` pair (two raw ``datetime`` callback
parameters, which the BC.5 ``no-raw-temporal-args`` AST lint would
otherwise reject) with a single ``--period`` option that parses one of
four shapes into a ``DateInterval``:

- ``trailing:N`` — trailing N days ending YESTERDAY (today excluded).
  N must be a positive integer. The audit window convention. Example:
  ``--period trailing:7`` → ``[today-7, today-1]``.
- ``today`` — single-day interval ``[today, today]``.
- ``yesterday`` — single-day interval ``[today-1, today-1]``.
- ``YYYY-MM-DD..YYYY-MM-DD`` — explicit closed-closed range. Both
  endpoints inclusive (the business convention). Example:
  ``--period 2026-05-17..2026-05-23``.
- ``YYYY-MM-DD`` — single-day interval (start == end == the given date).

When omitted entirely, ``_resolve_period`` defaults to
``trailing:7`` (matches the v1 default behavior).

Operators who want a non-default range pick the shape that reads best
for their flow: the keyword forms (``trailing:7``, ``yesterday``) for
common audit cadences; the explicit range when reproducing a specific
period (re-running a prior report; matching a manual SQL pull).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import click

from recon_gen.common.as_of_frame import AsOfFrame
from recon_gen.common.intervals import DateInterval


_TRAILING_RE = re.compile(r"^trailing:(\d+)$")
_ISO_RANGE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$"
)
_ISO_SINGLE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")


def _today_local() -> date:
    """Wall-clock today (LOCAL, single-TZ invariant).

    Routes through ``AsOfFrame.live()`` — the canonical
    blessed-wall-clock seam (per `as_of_frame.py` and the AQ.3 funnel
    intent). Phase BD's RunContext will subsume this; for BC.2 the
    audit CLI's keyword-period parsing reads through the same seam.
    """
    return AsOfFrame.live().as_of


class DateIntervalParamType(click.ParamType):
    """Click custom type: string → ``DateInterval``.

    Used by the ``--period`` option on ``recon-gen audit apply`` (and any
    other audit-CLI subcommand that takes a date range).

    Parsing is deterministic — no wall-clock for explicit ISO ranges;
    the keyword shapes (``trailing:N`` / ``today`` / ``yesterday``)
    resolve against ``_today_local()`` at parse time.
    """

    name = "date_interval"

    def convert(
        self,
        value: Any,  # typing-smell: ignore[explicit-any]: click's convert() value param is `Any` by interface
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> DateInterval:
        # Click already passes a DateInterval through unchanged when
        # this type is the option's annotated type and the operator
        # didn't supply the flag (Click's default-handling path).
        if isinstance(value, DateInterval):
            return value
        if not isinstance(value, str):
            self.fail(
                f"--period expects a string, got {type(value).__name__}",
                param, ctx,
            )

        text = value.strip()

        # 1) trailing:N
        m = _TRAILING_RE.match(text)
        if m is not None:
            try:
                n = int(m.group(1))
            except ValueError:
                self.fail(
                    f"--period trailing:N requires a positive integer, "
                    f"got {m.group(1)!r}", param, ctx,
                )
            if n < 1:
                self.fail(
                    f"--period trailing:N requires N >= 1, got {n}",
                    param, ctx,
                )
            return DateInterval.trailing_days_ending_yesterday(
                _today_local(), n,
            )

        # 2) keyword forms
        if text == "today":
            return DateInterval.single_day(_today_local())
        if text == "yesterday":
            return DateInterval.single_day(
                _today_local() - timedelta(days=1),
            )

        # 3) YYYY-MM-DD..YYYY-MM-DD
        m = _ISO_RANGE_RE.match(text)
        if m is not None:
            try:
                start = date.fromisoformat(m.group(1))
                end = date.fromisoformat(m.group(2))
            except ValueError as e:
                self.fail(
                    f"--period range has an invalid ISO date: {e}",
                    param, ctx,
                )
            if start > end:
                self.fail(
                    f"--period range start ({start.isoformat()}) must "
                    f"not be after end ({end.isoformat()}).",
                    param, ctx,
                )
            return DateInterval.closed(start, end)

        # 4) YYYY-MM-DD (single day)
        m = _ISO_SINGLE_RE.match(text)
        if m is not None:
            try:
                d = date.fromisoformat(m.group(1))
            except ValueError as e:
                self.fail(
                    f"--period date has an invalid ISO date: {e}",
                    param, ctx,
                )
            return DateInterval.single_day(d)

        self.fail(
            f"--period: cannot parse {text!r}. Accepted shapes: "
            f"``trailing:N`` (e.g. trailing:7), ``today``, "
            f"``yesterday``, ``YYYY-MM-DD..YYYY-MM-DD`` "
            f"(e.g. 2026-05-17..2026-05-23), or single ``YYYY-MM-DD``.",
            param, ctx,
        )


def period_option():  # type: ignore[no-untyped-def]: Click decorator strips the function-decorator return type
    """``--period`` option: parses to ``DateInterval``, default ``trailing:7``.

    The default (None at the callback) is resolved in ``_resolve_period``
    to ``DateInterval.trailing_days_ending_yesterday(today, 7)`` — the
    audit-window convention.
    """
    return click.option(
        "--period",
        "period",
        type=DateIntervalParamType(),
        default=None,
        help=(
            "Report period. Accepted shapes: ``trailing:N`` (N days "
            "ending yesterday; e.g. ``trailing:7``), ``today``, "
            "``yesterday``, ``YYYY-MM-DD..YYYY-MM-DD`` "
            "(explicit closed-closed range), or single ``YYYY-MM-DD`` "
            "(one-day report). Default: ``trailing:7`` (a 7-day window "
            "ending yesterday — the audit-window convention)."
        ),
    )
