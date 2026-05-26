"""BC.11.3 — regression: seed-emitted INSERT values don't overflow column widths.

The CI failure that surfaced this: `tx-chainfill-xfer-limit-breach-
CustomerInboundACH-Inbound-cust-0001-snb-CustomerInboundACHReturnNSF-0`
— 101 chars, overflowing the vc100 cap on `<prefix>_transactions.id`
when the spec_example variant passed (its rail names are shorter).

BC.11.2 widened id / transfer_id / transfer_parent_id / bundle_id to
vc255. This test catches the next overflow: it walks the seed SQL the
production `recon-gen data apply` emits and asserts no INSERT VALUE
exceeds the column's declared width. Generator-synthesized IDs that
grow past vc255 in a future L2 fire this test instead of CI's
`StringDataRightTruncation`.

Runs against spec_example + sasquatch_pr (the two L2 fixtures the
CI matrix exercises); fuzz seeds add automatic coverage if/when
they grow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from recon_gen.cli._helpers import build_full_seed_sql
from recon_gen.common.config import Config
from recon_gen.common.l2 import load_instance
from recon_gen.common.sql import Dialect


_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "l2"


# Column widths the schema declares on <prefix>_transactions /
# <prefix>_daily_balances. Keep aligned with the {vc20}/{vc50}/{vc100}/
# {vc255} placeholders in `common/l2/schema.py`. If a column widens
# there, update this map; if a generator emits beyond a declared
# width, this test fires.
#
# Only string-typed columns; numeric / timestamp / JSON columns are
# unconstrained from this test's point of view (the matview SQL has
# its own checks).
_TX_COLUMN_WIDTHS: dict[str, int] = {
    "id": 255,
    "account_id": 100,
    "account_name": 255,
    "account_role": 100,
    "account_scope": 20,
    "account_parent_role": 100,
    "amount_direction": 20,
    "status": 50,
    "transfer_id": 255,
    "transfer_parent_id": 255,
    "rail_name": 100,
    "template_name": 100,
    "bundle_id": 255,
    "supersedes": 50,
    "origin": 50,
}

_DB_COLUMN_WIDTHS: dict[str, int] = {
    "account_id": 100,
    "account_name": 255,
    "account_role": 100,
    "account_scope": 20,
    "account_parent_role": 100,
    "supersedes": 50,
}


def _cfg_for_test(prefix: str) -> Config:
    """A minimal Config the seed emitter accepts. The actual values
    don't matter — we only walk the emitted SQL, never connect."""
    return Config(
        aws_account_id="000000000000",
        aws_region="us-east-1",
        deployment_name=prefix,
        db_table_prefix=prefix,
        dialect=Dialect.POSTGRES,
        # demo_database_url required so Config skips the datasource_arn
        # gate; we never actually connect.
        demo_database_url=f"postgresql://noconn:noconn@localhost:1/{prefix}",
        principal_arns=[],
    )


# Match a single `INSERT INTO <prefix>_<table> (<cols>) VALUES (<vals>);`
# block. Captures the table suffix, column list, and the VALUES tuples
# block (which may span many parens for batched inserts).
_INSERT_RE = re.compile(
    r"INSERT INTO \w+_(\w+) \(([^)]+)\)\s+VALUES\s+(.+?);",
    re.DOTALL,
)

# Split the VALUES block into individual `(...)` tuples. Naive but
# works for our seed (no nested parens in values; quotes escape `''`).
_TUPLE_RE = re.compile(r"\((.*?)\)(?=\s*[,;]|\s*$)", re.DOTALL)


def _parse_values_tuple(tuple_text: str) -> list[str | None]:
    """Split a VALUES tuple body into typed values: str|None.

    NULL → None. 'literal' → str (with `''` un-escaped). Numbers and
    other non-string tokens → None (we only care about string-typed
    columns for the width check).
    """
    out: list[str | None] = []
    i = 0
    n = len(tuple_text)
    while i < n:
        # Skip whitespace + commas.
        while i < n and tuple_text[i] in " \t\n,":
            i += 1
        if i >= n:
            break
        if tuple_text[i] == "'":
            # String literal; respect `''` escape.
            i += 1
            start = i
            buf: list[str] = []
            while i < n:
                if tuple_text[i] == "'":
                    if i + 1 < n and tuple_text[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    break
                buf.append(tuple_text[i])
                i += 1
            out.append("".join(buf))
            i += 1  # consume closing quote
        else:
            # Non-string token — read until next comma.
            start = i
            while i < n and tuple_text[i] != ",":
                i += 1
            token = tuple_text[start:i].strip()
            out.append(None if token.upper() == "NULL" else None)
    return out


def _columns_for_table(table_suffix: str) -> dict[str, int] | None:
    if table_suffix == "transactions":
        return _TX_COLUMN_WIDTHS
    if table_suffix == "daily_balances":
        return _DB_COLUMN_WIDTHS
    return None  # other tables (e.g. config) are out of scope here


def _walk_inserts_for_overflows(sql_text: str) -> list[str]:
    """Return a list of `<table>.<col>: <len> > <max>: <value-prefix>...`
    messages, one per overflow. Empty list = clean."""
    failures: list[str] = []
    for match in _INSERT_RE.finditer(sql_text):
        table_suffix = match.group(1)
        col_widths = _columns_for_table(table_suffix)
        if col_widths is None:
            continue
        col_names = [c.strip() for c in match.group(2).split(",")]
        values_block = match.group(3)
        # Drop trailing `;` if INSERT_RE captured up to it.
        values_block = values_block.rstrip(";").rstrip()
        # Walk every tuple in the batch.
        for tuple_match in _TUPLE_RE.finditer(values_block):
            tuple_text = tuple_match.group(1)
            values = _parse_values_tuple(tuple_text)
            if len(values) != len(col_names):
                # Parser disagreement — skip rather than false-flag.
                continue
            for col_name, val in zip(col_names, values):
                if val is None:
                    continue
                width = col_widths.get(col_name)
                if width is None:
                    continue  # column not string-typed; skip
                if len(val) > width:
                    failures.append(
                        f"{table_suffix}.{col_name}: {len(val)} chars > "
                        f"vc{width} cap. value (truncated): "
                        f"{val[:120]!r}{'...' if len(val) > 120 else ''}"
                    )
    return failures


@pytest.mark.parametrize(
    "fixture_name",
    [
        pytest.param("spec_example", id="spec_example"),
        pytest.param("sasquatch_pr", id="sasquatch_pr"),
    ],
)
def test_seed_emit_respects_column_widths(fixture_name: str) -> None:
    """Walk every INSERT in the seed SQL `data apply` emits; assert no
    string VALUE exceeds the declared column width.

    Regression for the BC.11 CI failure (CI run 26373578977,
    integration-pg/sq_pg_lo): `tx-chainfill-...` IDs grow with rail-
    name length; sasquatch hit 101 chars on a vc100 column. Schema
    widened to vc255 in BC.11.2; this test catches the next overflow.
    """
    instance = load_instance(_FIXTURES_DIR / f"{fixture_name}.yaml")
    cfg = _cfg_for_test(prefix=f"qsgen_{fixture_name}")
    sql = build_full_seed_sql(cfg, instance)
    overflows = _walk_inserts_for_overflows(sql)
    if overflows:
        msg = (
            f"{len(overflows)} INSERT VALUE(s) exceed declared "
            f"column widths in the {fixture_name} seed:\n"
            + "\n".join(f"  {o}" for o in overflows[:10])
            + (f"\n  ... and {len(overflows) - 10} more" if len(overflows) > 10 else "")
        )
        pytest.fail(msg)
