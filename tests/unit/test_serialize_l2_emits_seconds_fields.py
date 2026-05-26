"""BC.8 — `serialize_l2` emits `_seconds` companions for Duration fields.

The matview SQL (`<prefix>_stuck_pending` + `<prefix>_stuck_unbundled`)
reads each rail's per-leg aging cap from `<prefix>_config.l2_yaml` via
`JSON_VALUE(rail.value, '$.max_pending_age_seconds' RETURNING ...)::bigint`
(see `common/l2/schema.py::_emit_l1_invariant_views`). The dataclass
carries `max_pending_age: timedelta` and the serializer emits a
human-readable Duration string under `max_pending_age`. Without a
parallel `_seconds` field, the JSON_VALUE lookup returns NULL, the
matview's `WHERE max_pending_age_seconds IS NOT NULL` excludes every
row, and stuck_pending / stuck_unbundled stay EMPTY regardless of
populated transactions.

BC.8 fix: `serialize_l2` emits BOTH `max_pending_age` (Duration ISO,
round-trip stability) AND `max_pending_age_seconds` (int, matview SQL
consumption). Same for `max_unbundled_age`. The loader ignores unknown
keys, so round-trip stays field-equal.

Two-tier coverage:

1. YAML emit shape — every rail with a cap carries BOTH names.
2. End-to-end — load → serialize → JSON → INSERT into `<prefix>_config`
   → manufactured Pending tx with age > cap → refresh →
   stuck_pending matview has one row. This is the property the
   production bug would silently regress.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import (
    Rail,
    SingleLegRail,
    TwoLegRail,
)
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.serializer import serialize_l2
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE
_TEST_AS_OF = datetime(2030, 1, 1, 12, 0, 0)


def _rails_with_cap(instance_rails: tuple[Rail, ...]) -> list[Rail]:
    """Rails that declare at least one aging cap — the ones the matview
    SQL filters in via `WHERE max_*_age_seconds IS NOT NULL`."""
    return [
        r for r in instance_rails
        if r.max_pending_age is not None or r.max_unbundled_age is not None
    ]


def test_serialize_l2_emits_seconds_companion_for_every_rail_with_cap() -> None:
    """Every rail that declares `max_pending_age` / `max_unbundled_age`
    in the dataclass MUST emit a parallel `_seconds` int alongside the
    Duration ISO string. The matview JSON_VALUE lookup reads the
    `_seconds` field."""
    instance = load_instance(_SPEC_EXAMPLE)
    yaml_text = serialize_l2(instance)
    parsed = yaml.safe_load(yaml_text)

    capped_rails = _rails_with_cap(instance.rails)
    # spec_example carries exactly two rails with caps — ExternalRailInbound
    # (max_pending_age) and SubledgerCharge (max_unbundled_age). If the
    # fixture grows more, this assertion still holds since the test walks
    # the loaded model.
    assert len(capped_rails) >= 1, (
        "fixture regression: spec_example used to carry rails with "
        "max_pending_age / max_unbundled_age — none found in the loaded "
        "model. Re-check the fixture before changing this test."
    )

    raw_by_name: dict[str, dict[str, Any]] = {r["name"]: r for r in parsed["rails"]}
    for rail in capped_rails:
        raw = raw_by_name[str(rail.name)]
        if rail.max_pending_age is not None:
            assert "max_pending_age" in raw
            assert "max_pending_age_seconds" in raw, (
                f"rail {rail.name!r} declares max_pending_age but the "
                f"serializer did not emit `_seconds` companion — matview "
                f"JSON_VALUE returns NULL → stuck_pending stays empty."
            )
            assert isinstance(raw["max_pending_age_seconds"], int)
            assert raw["max_pending_age_seconds"] == int(
                rail.max_pending_age.total_seconds(),
            )
        if rail.max_unbundled_age is not None:
            assert "max_unbundled_age" in raw
            assert "max_unbundled_age_seconds" in raw, (
                f"rail {rail.name!r} declares max_unbundled_age but the "
                f"serializer did not emit `_seconds` companion — matview "
                f"JSON_VALUE returns NULL → stuck_unbundled stays empty."
            )
            assert isinstance(raw["max_unbundled_age_seconds"], int)
            assert raw["max_unbundled_age_seconds"] == int(
                rail.max_unbundled_age.total_seconds(),
            )


def test_serialize_l2_does_not_emit_seconds_for_rails_without_cap() -> None:
    """A rail with no cap MUST NOT emit either the Duration field or
    the `_seconds` companion — the loader interprets absence as `None`
    and the matview LEFT JOIN treats those rails as "no cap declared"."""
    instance = load_instance(_SPEC_EXAMPLE)
    yaml_text = serialize_l2(instance)
    parsed = yaml.safe_load(yaml_text)

    raw_by_name: dict[str, dict[str, Any]] = {r["name"]: r for r in parsed["rails"]}
    for rail in instance.rails:
        raw = raw_by_name[str(rail.name)]
        if rail.max_pending_age is None:
            assert "max_pending_age_seconds" not in raw, (
                f"rail {rail.name!r} has no max_pending_age but the "
                f"serializer emitted a `_seconds` companion — should "
                f"only emit when the parent Duration field emits."
            )
        if rail.max_unbundled_age is None:
            assert "max_unbundled_age_seconds" not in raw, (
                f"rail {rail.name!r} has no max_unbundled_age but the "
                f"serializer emitted a `_seconds` companion."
            )


def test_round_trip_through_serializer_is_field_equal() -> None:
    """The `_seconds` companion is a derived field; the loader ignores
    it. Round-trip stays field-equal — same contract as the existing
    round-trip suite, asserted here so this test alone reveals a
    regression if the loader ever starts treating `_seconds` specially."""
    instance = load_instance(_SPEC_EXAMPLE)
    yaml_text = serialize_l2(instance)

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write(yaml_text)
        tmp_path = Path(f.name)
    try:
        round_tripped = load_instance(tmp_path)
    finally:
        tmp_path.unlink()
    assert round_tripped == instance


def test_serialize_l2_drives_stuck_pending_matview_end_to_end() -> None:
    """End-to-end: the property the production bug would regress.

    Load spec_example → serialize_l2 → parse YAML to JSON →
    replace_config(<prefix>_config) → manufacture one Pending tx with
    posting older than the cap → refresh matviews → assert
    `<prefix>_stuck_pending` has one row.

    Pre-BC.8: serializer omits `_seconds`; JSON_VALUE returns NULL;
    matview's outer `WHERE max_pending_age_seconds IS NOT NULL`
    excludes the row → empty matview → test fails with count=0.
    Post-BC.8: `_seconds` populated; cap = 86400s; planted tx age =
    2 days > cap; matview has one row.
    """
    instance = load_instance(_SPEC_EXAMPLE)
    # Convert serialize_l2 output (YAML) to JSON for the config table.
    l2_yaml_text = serialize_l2(instance)
    l2_json_text = json.dumps(yaml.safe_load(l2_yaml_text))

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

        # Find a Pending-eligible rail with max_pending_age from the
        # serialized YAML (the same path the matview consumes).
        pending_rail = next(
            r for r in instance.rails
            if r.max_pending_age is not None
        )
        # Plant ONE Pending leg whose age >> cap. The matview's outer
        # filter is `WHERE max_pending_age_seconds IS NOT NULL AND
        # age_seconds > max_pending_age_seconds`.
        assert pending_rail.max_pending_age is not None
        posting = _TEST_AS_OF - timedelta(
            seconds=int(pending_rail.max_pending_age.total_seconds()) + 3600,
        )
        # Minimal insert into `<prefix>_transactions` — the Current* view
        # filters to the highest-entry-per-id, so a single insert is the
        # current row by construction. Columns match the
        # `_SCHEMA_TEMPLATE` CREATE TABLE in `common/l2/schema.py`.
        conn.execute(
            f"INSERT INTO {_PREFIX}_transactions "
            f"(id, account_id, account_name, account_role, account_scope, "
            f"  amount_money, amount_direction, status, posting, "
            f"  transfer_id, rail_name, origin, metadata) VALUES "
            f"(?, ?, ?, ?, 'internal', -100, 'Debit', 'Pending', ?, "
            f" ?, ?, 'InternalInitiated', '{{}}')",
            (
                "tx-bc8-1", "acct-bc8", "Acct BC8", "ProbeRole",
                posting.strftime("%Y-%m-%d %H:%M:%S"),
                "tr-bc8-1", str(pending_rail.name),
            ),
        )
        conn.commit()

        # Refresh matviews — re-emits the SELECT against the new config
        # row + the planted tx.
        cur = conn.cursor()
        execute_script(
            cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
            dialect=_DIALECT,
        )
        conn.commit()

        count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_stuck_pending"
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1, (
        f"stuck_pending matview should have 1 row (Pending tx with "
        f"age > cap); got {count}. This means the serializer-emitted "
        f"`max_pending_age_seconds` in <prefix>_config.l2_yaml didn't "
        f"reach the matview's JSON_VALUE extraction — the BC.8 bug."
    )


def test_serialize_l2_drives_stuck_unbundled_matview_end_to_end() -> None:
    """End-to-end mirror of stuck_pending for max_unbundled_age — same
    property, different cap field, different status filter."""
    instance = load_instance(_SPEC_EXAMPLE)
    l2_yaml_text = serialize_l2(instance)
    l2_json_text = json.dumps(yaml.safe_load(l2_yaml_text))

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

        unbundled_rail = next(
            r for r in instance.rails
            if r.max_unbundled_age is not None
        )
        assert unbundled_rail.max_unbundled_age is not None
        posting = _TEST_AS_OF - timedelta(
            seconds=int(unbundled_rail.max_unbundled_age.total_seconds()) + 3600,
        )
        # stuck_unbundled filters status='Posted' AND bundle_id IS NULL.
        conn.execute(
            f"INSERT INTO {_PREFIX}_transactions "
            f"(id, account_id, account_name, account_role, account_scope, "
            f"  amount_money, amount_direction, status, posting, "
            f"  transfer_id, rail_name, bundle_id, origin, metadata) VALUES "
            f"(?, ?, ?, ?, 'internal', -100, 'Debit', 'Posted', ?, "
            f" ?, ?, NULL, 'InternalInitiated', '{{}}')",
            (
                "tx-bc8-2", "acct-bc8b", "Acct BC8b", "ProbeRole",
                posting.strftime("%Y-%m-%d %H:%M:%S"),
                "tr-bc8-2", str(unbundled_rail.name),
            ),
        )
        conn.commit()

        cur = conn.cursor()
        execute_script(
            cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
            dialect=_DIALECT,
        )
        conn.commit()

        count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_stuck_unbundled"
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1, (
        f"stuck_unbundled matview should have 1 row (Posted+unbundled "
        f"tx with age > cap); got {count}. BC.8 regression."
    )


# Silence unused-import warnings for typing-only references.
_ = (TwoLegRail, SingleLegRail)
