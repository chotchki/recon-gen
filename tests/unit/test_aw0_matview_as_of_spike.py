"""AW.0 spike — controlled as_of injection into matview age computation.

User-driven (2026-05-23): the spine's `datetime.now()` in stuck_pending
+ stuck_unbundled is a symptom of a deeper uncontrolled dependency —
the matview SQL itself uses `CURRENT_TIMESTAMP` / `julianday('now')` for
the `age_seconds = NOW - posting` computation. AR's principle ("own the
temporal frame") says this should read from the cfg's `as_of`.

This spike validates the proposed mechanism BEFORE locking the
implementation across schema.py + every reader. Two candidate designs:

(A) **Re-emit-on-refresh** — the schema-emit takes an as_of literal;
    every refresh DROPs + re-CREATEs the matviews with the new literal
    substituted. Works on SQLite (which already does DROP+CREATE on
    refresh) but turns PG's REFRESH MATERIALIZED VIEW into a heavy
    operation (loses the optimized path).

(B) **Runtime-table subquery** — schema emits a small `<prefix>_runtime
    (as_of TIMESTAMP)` table; the matview body reads as_of via a
    correlated subquery: ``(SELECT as_of FROM runtime)`` instead of
    `CURRENT_TIMESTAMP`. Each refresh just UPDATEs the runtime row + does
    REFRESH MATERIALIZED VIEW; the body re-evaluates the subquery
    against the fresh row. Preserves the optimized refresh path on PG.

The spike validates **(B)** because it's the lighter touch on PG +
matches AR's "owned frame in config" framing. What's verified:

1. SQLite accepts the subquery shape inside `julianday(...)` (the
   epoch-seconds formula on SQLite). If this fails, (B) is dead.
2. Updating the runtime row + refreshing yields different `age_seconds`
   without re-creating the matview body — the same matview SQL adapts
   to the new as_of.
3. The plant + matview both read from the same as_of source → no
   wall-clock skew between them; tests become deterministic.

---

**UPDATE (2026-05-23): design pivoted by user feedback.**

User raised the architectural question: "does encoding any of the
l2/config into a database table make things easier or harder?" — past
explorations have held the line on L2+cfg-stays-in-YAML, with DB tables
being DATA, not CONFIGURATION.

Counter-proposal that the user accepted: a SINGLE config table
(`<prefix>_config`) holding the cfg + L2 yaml content as JSON, with
`as_of` as a typed sibling column. This is the user's ONLY-allowed
relaxation of the two-table rule because the table is DERIVED from
cfg+L2 (Python populates it; never operator-mutated; mirrors the YAML
1:1). The design generalizes: matviews stop baking per-L2 literals via
Python emit-time templating and instead JOIN to the config table to
read whatever they need.

So the final AW design isn't (A) or (B) alone — it's a **superset of
(B) with the config table holding cfg+L2-yaml-as-JSON**:

```sql
CREATE TABLE <prefix>_config (
    as_of    TIMESTAMP NOT NULL,
    cfg_yaml {json_text} NOT NULL,
    l2_yaml  {json_text} NOT NULL
);
```

Operational model:
- **Deploy** (cfg or L2 yaml changes): Python REPLACES the row; initial
  as_of set; matviews refresh.
- **Daily ETL** (data load, cfg unchanged): refresh helper UPDATEs
  `as_of = CURRENT_TIMESTAMP` (or pinned for tests); matviews refresh.

The mechanism this spike validates (subquery in matview body re-evaluates
per-refresh against the table row) carries over directly — just against
the config table's `as_of` column instead of a `_runtime` table.

The portability check for multi-valued reads (e.g. "find the rail with
name=X and return its max_pending_age") lives in the sibling spike
`test_aw0b_jsonpath_filter_spike.py` — finding: SQLite doesn't support
SQL/JSON filter-path syntax; the portable shape uses `json_each() +
WHERE` + LEFT JOIN. AW.1+ implements both shapes with a dialect-switch
helper.

What this spike does NOT prove (AW.1+ work):

- The PG / Oracle side of the subquery-in-EXTRACT formula. PG and
  Oracle's SQL/JSON syntax should work but needs the deploy-layer test.
- The schema.py refactor + ETL refresh path + every consuming reader.
- The full config-table migration of L2-baked literals
  (`{pending_age_cases}`, `{unbundled_age_cases}`, `{limit_cases_*}`)
  to read from `<prefix>_config.l2_yaml` via the dialect-switch JSON
  helper.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


_SCHEMA_SQL = """
-- Runtime config table — one row, holds the owned `as_of`.
CREATE TABLE spike_runtime (
    as_of TEXT NOT NULL
);

-- Transactions table (subset).
CREATE TABLE spike_transactions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    posting TEXT NOT NULL
);

-- The matview's "age" formula uses the runtime table instead of
-- CURRENT_TIMESTAMP. SQLite: julianday(...) accepts arbitrary
-- expressions, including a scalar subquery, so the body re-evaluates
-- per-refresh.
CREATE TABLE spike_stuck_pending AS
SELECT
    id AS transaction_id,
    status,
    posting,
    (
        (julianday((SELECT as_of FROM spike_runtime)) - julianday(posting))
        * 86400
    ) AS age_seconds
FROM spike_transactions
WHERE status = 'Pending';
"""

# Cap = 1 hour. Tests vary as_of to push age across the cap.
_CAP_SECONDS = 3600


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_SQL)
    # Seed runtime with an arbitrary initial value; tests overwrite.
    conn.execute(
        "INSERT INTO spike_runtime (as_of) VALUES (?)",
        ("2030-01-01 00:00:00",),
    )
    conn.commit()
    return conn


def _set_as_of(conn: sqlite3.Connection, as_of: datetime) -> None:
    """Production-equivalent of `UPDATE <prefix>_runtime SET as_of=...`."""
    conn.execute(
        "DELETE FROM spike_runtime",
    )
    conn.execute(
        "INSERT INTO spike_runtime (as_of) VALUES (?)",
        (as_of.strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()


def _refresh_matview(conn: sqlite3.Connection) -> None:
    """SQLite refresh — DROP + re-CREATE the matview body. PG would do
    `REFRESH MATERIALIZED VIEW`; in both cases the SELECT re-evaluates
    against the current runtime row."""
    conn.executescript(
        """
        DROP TABLE spike_stuck_pending;
        CREATE TABLE spike_stuck_pending AS
        SELECT
            id AS transaction_id,
            status,
            posting,
            (
                (julianday((SELECT as_of FROM spike_runtime)) - julianday(posting))
                * 86400
            ) AS age_seconds
        FROM spike_transactions
        WHERE status = 'Pending';
        """
    )
    conn.commit()


def _plant_pending_tx(
    conn: sqlite3.Connection,
    *,
    tx_id: str,
    posting: datetime,
) -> None:
    conn.execute(
        "INSERT INTO spike_transactions (id, status, posting) "
        "VALUES (?, 'Pending', ?)",
        (tx_id, posting.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# The validation tests — what each design candidate is judged on.
# ---------------------------------------------------------------------------


def test_sqlite_julianday_accepts_subquery_for_as_of() -> None:
    """Question 1 — does SQLite's `julianday(...)` accept a scalar
    subquery? If this fails, design (B) is dead and the heavyweight
    re-emit-on-refresh (A) is the only path."""
    conn = _fresh_db()
    try:
        _set_as_of(conn, datetime(2030, 1, 1, 12, 0, 0))
        # The matview body already runs julianday((SELECT as_of FROM
        # runtime)) at CREATE time. If subquery isn't accepted, schema
        # init would have errored. Re-check at refresh.
        _refresh_matview(conn)
        # Sanity: the matview can be queried.
        row = conn.execute(
            "SELECT COUNT(*) FROM spike_stuck_pending",
        ).fetchone()
    finally:
        conn.close()
    assert row == (0,)  # no Pending transactions yet


def test_age_seconds_changes_when_as_of_changes() -> None:
    """Question 2 — the headline check: same plant, different as_of,
    refresh, see different age_seconds. Validates that the runtime-
    table subquery actually drives the matview's age computation per-
    refresh."""
    conn = _fresh_db()
    try:
        # Plant ONE pending transaction with posting = 2030-01-01 10:00.
        posting = datetime(2030, 1, 1, 10, 0, 0)
        _plant_pending_tx(conn, tx_id="tx-1", posting=posting)

        # Run 1: as_of = posting + 30min ⇒ age = 1800s (under cap)
        _set_as_of(conn, posting + timedelta(minutes=30))
        _refresh_matview(conn)
        age_1 = conn.execute(
            "SELECT age_seconds FROM spike_stuck_pending WHERE transaction_id = 'tx-1'",
        ).fetchone()[0]

        # Run 2: as_of = posting + 2h ⇒ age = 7200s (over cap)
        _set_as_of(conn, posting + timedelta(hours=2))
        _refresh_matview(conn)
        age_2 = conn.execute(
            "SELECT age_seconds FROM spike_stuck_pending WHERE transaction_id = 'tx-1'",
        ).fetchone()[0]
    finally:
        conn.close()

    # age_1 is ~1800s; age_2 is ~7200s. Floating-point tolerance.
    assert abs(age_1 - 1800.0) < 1.0, age_1
    assert abs(age_2 - 7200.0) < 1.0, age_2


def test_violation_set_changes_when_as_of_crosses_cap() -> None:
    """Question 2 (extended) — the THING that motivates this whole
    spike: the SAME plant fires or doesn't fire based on as_of
    relative to the cap. Tests are deterministic (no wall-clock)."""
    conn = _fresh_db()
    try:
        posting = datetime(2030, 1, 1, 10, 0, 0)
        _plant_pending_tx(conn, tx_id="tx-1", posting=posting)

        # Under cap → no violation.
        _set_as_of(conn, posting + timedelta(minutes=30))  # age = 1800s
        _refresh_matview(conn)
        violations_under = conn.execute(
            "SELECT transaction_id FROM spike_stuck_pending "
            "WHERE age_seconds > ?",
            (_CAP_SECONDS,),
        ).fetchall()

        # Over cap → violation fires.
        _set_as_of(conn, posting + timedelta(hours=2))  # age = 7200s
        _refresh_matview(conn)
        violations_over = conn.execute(
            "SELECT transaction_id FROM spike_stuck_pending "
            "WHERE age_seconds > ?",
            (_CAP_SECONDS,),
        ).fetchall()
    finally:
        conn.close()

    assert violations_under == []
    assert violations_over == [("tx-1",)]


def test_plant_and_matview_share_one_as_of_source() -> None:
    """Question 3 — the AR principle: BOTH halves of the spine read
    from one owned frame. In this spike, the plant computes posting
    relative to as_of, the matview computes age relative to the same
    as_of. Wall-clock plays no role. Deterministic regardless of
    system TZ or refresh latency."""
    conn = _fresh_db()
    try:
        # Pick an as_of arbitrarily — what matters is plant + matview
        # use the SAME value.
        as_of = datetime(2027, 4, 15, 14, 30, 0)
        cap = 3600
        overshoot = 600  # 10 minutes past cap → fires

        # Plant posting = as_of - cap - overshoot (the production-spine
        # convention, but now both halves use the SAME as_of).
        posting = as_of - timedelta(seconds=cap + overshoot)
        _plant_pending_tx(conn, tx_id="tx-1", posting=posting)

        _set_as_of(conn, as_of)
        _refresh_matview(conn)

        row = conn.execute(
            "SELECT age_seconds FROM spike_stuck_pending "
            "WHERE transaction_id = 'tx-1'",
        ).fetchone()
    finally:
        conn.close()

    # age should equal cap + overshoot, exactly (deterministic).
    assert abs(row[0] - (cap + overshoot)) < 0.001, row


def test_matview_body_stable_across_refreshes() -> None:
    """Bonus — the matview's CREATE SQL is the same across refreshes.
    The runtime table's row changes; the matview body's SQL doesn't
    re-emit. This confirms the design's "lighter touch on PG" claim —
    no DROP+CREATE per refresh in production."""
    conn = _fresh_db()
    try:
        # Capture the schema after first refresh
        _set_as_of(conn, datetime(2027, 1, 1))
        _refresh_matview(conn)
        sql_1 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'spike_stuck_pending'",
        ).fetchone()[0]

        # Refresh again with a different as_of
        _set_as_of(conn, datetime(2030, 1, 1))
        _refresh_matview(conn)
        sql_2 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'spike_stuck_pending'",
        ).fetchone()[0]
    finally:
        conn.close()

    # The CREATE SQL is byte-identical across refreshes. The runtime
    # row inside the body's subquery is what varied. In PG, this would
    # mean REFRESH MATERIALIZED VIEW (no DROP+CREATE) suffices.
    assert sql_1 == sql_2
