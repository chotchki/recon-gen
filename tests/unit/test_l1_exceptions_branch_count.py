"""CS.7 anti-drift gate — the L1 Exceptions matview UNION ALL must
emit exactly the check_type values declared in
``L1_EXCEPTIONS_BRANCH_NAMES``.

WHY: the L1 Exceptions sheet copy in ``apps/l1_dashboard/app.py``
(description + KPI subtitle + bar-chart subtitle) names the branch
count. Pre-CS.7 it claimed "10 invariants" but the matview UNIONs 12
(balance_cadence_gap was added at CL.6 + xor_group_violation went
unnamed at C6's recount). This gate makes the next add-a-branch
operator notice the prose drift at unit tier rather than at cold-read.

The check walks the L1 Exceptions matview template emitted by
``common.l2.schema``, extracts the ``check_type`` literal from every
SELECT branch, and asserts the set equals ``L1_EXCEPTIONS_BRANCH_NAMES``.
"""

from __future__ import annotations

import re

from recon_gen.apps.l1_dashboard.app import L1_EXCEPTIONS_BRANCH_NAMES
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.sql.dialect import Dialect


# Match either bare-literal or ``... AS check_type`` form:
#   SELECT 'drift' AS check_type, account_id, ...
#   SELECT 'limit_breach', account_id, ...
# Whitelist the second form because the matview emits both shapes
# depending on whether the per-branch column header is explicit.
_BRANCH_RE = re.compile(
    r"SELECT\s+'([a-z_]+)'(?:\s+AS\s+check_type)?\s*,",
    re.IGNORECASE,
)


def _extract_check_types_from_l1_exceptions_matview() -> tuple[str, ...]:
    """Walk the emitted schema SQL + return every check_type literal
    inside the L1 Exceptions matview's UNION ALL branches.

    The matview is uniquely identified by the
    ``CREATE MATERIALIZED VIEW <prefix>_l1_exceptions`` header (PG
    arm) — we slice the schema text from that header to the trailing
    ``;`` so we don't pick up SELECTs from other matviews.
    """
    inst = default_l2_instance()
    sql = emit_schema(inst, prefix=DEFAULT_PREFIX, dialect=Dialect.DUCKDB)
    # DuckDB's matview build uses CREATE TABLE AS SELECT (CB.8); the
    # marker still resolves the L1 Exceptions block uniquely.
    start_marker_re = re.compile(
        rf"CREATE\s+(?:MATERIALIZED\s+VIEW|TABLE)\s+{DEFAULT_PREFIX}_l1_exceptions\b",
        re.IGNORECASE,
    )
    m = start_marker_re.search(sql)
    assert m is not None, (
        "could not locate the L1 Exceptions matview block in the "
        "emitted schema — did the marker shape change?"
    )
    # End of the block: the next CREATE statement, or end-of-string.
    rest = sql[m.start():]
    end_m = re.search(
        r"\n\s*CREATE\s+(?:MATERIALIZED\s+VIEW|TABLE|INDEX)\b",
        rest[1:],
        re.IGNORECASE,
    )
    block = rest if end_m is None else rest[: 1 + end_m.start()]
    return tuple(_BRANCH_RE.findall(block))


def test_l1_exceptions_matview_branch_set_matches_app_declaration() -> None:
    """Bidirectional gate: every check_type in the matview is named in
    ``L1_EXCEPTIONS_BRANCH_NAMES`` AND every name in the tuple appears
    in the matview. Catches drift in EITHER direction (add to matview
    but forget the tuple → undercount in prose; remove from matview
    but leave the tuple → overcount).
    """
    matview = set(_extract_check_types_from_l1_exceptions_matview())
    declared = set(L1_EXCEPTIONS_BRANCH_NAMES)
    missing_from_tuple = matview - declared
    missing_from_matview = declared - matview
    assert not missing_from_tuple, (
        f"L1 Exceptions matview emits check_type values not declared "
        f"in L1_EXCEPTIONS_BRANCH_NAMES: {sorted(missing_from_tuple)}. "
        f"Add them to the tuple in apps/l1_dashboard/app.py AND bump "
        f"the count in the sheet description / KPI / bar-chart copy."
    )
    assert not missing_from_matview, (
        f"L1_EXCEPTIONS_BRANCH_NAMES declares check_type values not "
        f"emitted by the matview: {sorted(missing_from_matview)}. "
        f"Drop them from the tuple OR add the matching SELECT branch "
        f"to common/l2/schema.py::_L1_EXCEPTIONS_TEMPLATE."
    )


def test_l1_exceptions_branch_count_matches_prose() -> None:
    """The number-in-prose gate. The description, KPI subtitle, and
    bar-chart subtitle all name the branch count literally
    ("12 invariant views" / "12 invariant checks" / "12 L1 invariants").
    Bump this assertion alongside any tuple change so the prose stays
    consistent across the three callsites.
    """
    assert len(L1_EXCEPTIONS_BRANCH_NAMES) == 12, (
        f"L1_EXCEPTIONS_BRANCH_NAMES has {len(L1_EXCEPTIONS_BRANCH_NAMES)} "
        f"entries; the sheet copy in apps/l1_dashboard/app.py names "
        f"the count in 3 places (sheet description, KPI subtitle, "
        f"bar-chart subtitle). Update all 3 + bump this assertion."
    )
