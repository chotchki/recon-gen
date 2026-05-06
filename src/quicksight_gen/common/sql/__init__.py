"""SQL dialect helpers — Phase P.2 catalog + P.3 Oracle fill +
P.3.e cleanup.

Public surface:

- ``Dialect`` enum (``POSTGRES``, ``ORACLE``).
- Per-construct helpers that emit dialect-appropriate SQL strings.
  Every helper takes a ``Dialect`` explicitly — no defaults.

See ``dialect.py``'s module docstring for the "statement vs fragment"
output convention (statement helpers self-terminate; fragment helpers
return expression-level SQL).
"""

from __future__ import annotations

from .dialect import (
    Dialect,
    analyze_table,
    boolean_type,
    cast,
    create_matview,
    date_minus_days,
    date_trunc_day,
    decimal_type,
    drop_index_if_exists,
    drop_matview_if_exists,
    drop_table_if_exists,
    drop_view_if_exists,
    dual_from,
    epoch_seconds_between,
    interval_days,
    json_check,
    json_text_type,
    matview_create_keyword,
    matview_options,
    order_by_day_expr,
    range_interval_days,
    refresh_matview,
    serial_type,
    text_type,
    timestamp_type,
    to_date,
    typed_null,
    varchar_type,
    with_recursive,
)

__all__ = [
    "Dialect",
    "analyze_table",
    "boolean_type",
    "cast",
    "create_matview",
    "date_minus_days",
    "date_trunc_day",
    "decimal_type",
    "drop_index_if_exists",
    "drop_matview_if_exists",
    "drop_table_if_exists",
    "drop_view_if_exists",
    "dual_from",
    "epoch_seconds_between",
    "interval_days",
    "json_check",
    "json_text_type",
    "matview_create_keyword",
    "matview_options",
    "order_by_day_expr",
    "range_interval_days",
    "refresh_matview",
    "serial_type",
    "text_type",
    "timestamp_type",
    "to_date",
    "typed_null",
    "varchar_type",
    "with_recursive",
]
