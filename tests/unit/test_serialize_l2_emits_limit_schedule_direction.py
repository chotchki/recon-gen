"""BC.12.5 — `serialize_l2` emits `direction` on every `LimitSchedule`.

Same shape as BC.8's `_seconds`-omit fix. The L1 limit_breach matview
(`<prefix>_limit_breach`) reads `direction` from
`<prefix>_v_config_limit_schedules` (a typed projection view over
`<prefix>_config_kv`). The view projects whichever value the kv
stores for the `direction` key; if the kv has no `direction` row for
that limit-schedule entry, the view returns NULL, and the matview's
JOIN clause (`ls.direction = 'Outbound'`) filters the row out via
NULL-doesn't-equal-string semantics → false negative on every
Outbound limit_breach plant.

Pre-BC.12.5: `LimitSchedule.direction` defaulted to "Outbound" on the
dataclass and the serializer OMITTED the field for default-Outbound
entries (the "compact yaml" rule). Pre-BC.12 the matview consumed the
JSON directly via `JSON_VALUE(... direction)` whose NULL-fallback
happened to match the desired Outbound semantics. The BC.12 typed-view
migration broke this: now the kv stores what serialize_l2 emits, the
typed view projects what's in the kv, and matviews see NULL → strict
equality FALSE → row dropped.

Post-BC.12.5: serialize_l2 always emits `direction` (the dataclass
default IS the data — no implicit defaulting at SQL layer). Outbound
limit_breach plants surface in the matview. This is the chronic
v11.10.0+ release-pipeline gate's blocker.

Two-tier coverage (mirrors BC.8 test structure):

1. YAML emit shape — every LimitSchedule (Inbound or Outbound)
   carries `direction:`.
2. End-to-end — load → serialize → JSON → populate config_kv → plant
   one Outbound-direction Debit tx whose absolute > cap → refresh →
   `<prefix>_limit_breach` matview has one row. Pre-fix this returns 0.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.serializer import serialize_l2
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE
_TEST_AS_OF = datetime(2030, 1, 1, 12, 0, 0)


def test_serialize_l2_emits_direction_on_every_limit_schedule() -> None:
    """spec_example carries 2 limit_schedules (one explicit Inbound,
    one implicit-default Outbound). Post-BC.12.5 both serialize with
    `direction:`."""
    instance = load_instance(_SPEC_EXAMPLE)
    text = serialize_l2(instance)
    assert text.count("direction: Outbound") >= 1, (
        f"Expected the default-Outbound limit_schedule to emit "
        f"`direction: Outbound` explicitly; got:\n{text[-2000:]}"
    )
    assert text.count("direction: Inbound") >= 1, (
        f"Expected the explicit-Inbound limit_schedule to keep its "
        f"`direction: Inbound` emit; got:\n{text[-2000:]}"
    )


def test_serialize_l2_drives_limit_breach_outbound_end_to_end() -> None:
    """End-to-end: the property the production bug regressed.

    Load spec_example → serialize_l2 → parse YAML to JSON →
    replace_config(<prefix>_config_kv) → manufacture one Debit tx
    whose ABS(amount) > the Outbound cap → refresh matviews → assert
    `<prefix>_limit_breach` has one row for that (account, day, rail,
    direction='Outbound').

    Pre-BC.12.5: kv has no `direction` row for the Outbound schedule;
    typed view returns NULL; matview JOIN drops the row → count=0.
    Post-BC.12.5: kv has `direction='Outbound'`; view projects
    'Outbound'; matview JOIN finds the cap; SUM(ABS(tx)) > cap → row.
    """
    instance = load_instance(_SPEC_EXAMPLE)
    l2_yaml_text = serialize_l2(instance)
    l2_json_text = json.dumps(yaml.safe_load(l2_yaml_text))

    outbound_ls = next(
        ls for ls in instance.limit_schedules
        if str(ls.direction) == "Outbound"
    )
    posting = _TEST_AS_OF - timedelta(days=2)
    # Amount-money is stored as integer cents (AO.1); cap from the L2
    # is a dollar Decimal. Breach = cap * 100 cents * 1.5.
    breach_cents = int(float(outbound_ls.cap) * 100 * 1.5)

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        _register_sqlite_aggregates(conn)
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
            dialect=_DIALECT,
        )
        conn.commit()
        replace_config(
            conn, prefix=_PREFIX,
            cfg_json="{}", l2_json=l2_json_text,
            as_of=_TEST_AS_OF,
        )

        # Plant one Posted Debit tx on a CustomerSubledger child of
        # CustomerLedger via ExternalRailOutbound. The matview's
        # Outbound branch sums ABS(amount_money) per (account, day,
        # rail) and JOINs the limit_schedule on (parent_role, rail,
        # direction='Outbound') for the cap.
        conn.execute(
            f"INSERT INTO {_PREFIX}_transactions "
            f"(id, account_id, account_name, account_role, account_scope, "
            f"  account_parent_role, "
            f"  amount_money, amount_direction, status, posting, "
            f"  transfer_id, rail_name, origin, metadata) VALUES "
            f"(?, ?, ?, 'CustomerSubledger', 'internal', 'CustomerLedger', "
            f" ?, 'Debit', 'Posted', ?, ?, ?, 'etl', '{{}}')",
            (
                "tx-bc12-5-1", "cust-bc12-5", "Cust BC.12.5",
                -breach_cents,
                posting.strftime("%Y-%m-%d %H:%M:%S"),
                "xfer-bc12-5-1", str(outbound_ls.rail),
            ),
        )
        conn.commit()

        cur = conn.cursor()
        execute_script(
            cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
            dialect=_DIALECT,
        )
        conn.commit()

        rows = conn.execute(
            f"SELECT account_id, business_day, rail_name, direction, "
            f"  outbound_total, cap "
            f"FROM {_PREFIX}_limit_breach "
            f"WHERE direction = 'Outbound'"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, (
        f"limit_breach matview should have 1 Outbound row (Debit tx "
        f"with ABS > cap on {outbound_ls.rail}); got {len(rows)} "
        f"row(s): {rows}. This means the typed view "
        f"`{_PREFIX}_v_config_limit_schedules` projected NULL for "
        f"`direction` (the kv had no `direction` row for the default-"
        f"Outbound limit_schedule), so the matview's strict "
        f"`ls.direction = 'Outbound'` JOIN dropped the row — the "
        f"BC.12.5 production-honest-invariants bug."
    )
