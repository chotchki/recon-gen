"""Post-``demo apply`` verifier for the containerized CI job (P.7).

Connects to whichever dialect's local DB the CI job spun up
(``--dialect postgres`` or ``--dialect oracle``) and checks that the
expected per-prefix tables exist + the matviews carry the row counts
the seed produces.

Two assertion modes:
- **Exact** (default for ``spec_example``): the planted seed produces
  a known number of rows in each matview. Mismatch → fail.
- **Smoke** (``--smoke``): just assert ≥1 row in every named matview.
  Use when a live count for the L2 instance hasn't been locked yet
  (e.g. sasquatch_pr — different scenario shape, different counts).

Exits 0 on success; exits 1 with a per-row diff on any mismatch.

Run as::

    # spec_example exact (P.7 CI default)
    python tests/integration/verify_demo_apply.py --dialect postgres \\
        --url "postgresql://postgres:pw@localhost:5432/postgres"

    # sasquatch_pr smoke (no locked counts yet — assert non-empty)
    python tests/integration/verify_demo_apply.py --dialect oracle \\
        --url "system/pw@localhost:1521/FREEPDB1" \\
        --prefix sasquatch_pr --smoke

Designed for the CI job, not as a unit test — needs a live DB and
deliberately doesn't import ``pytest``. Living under ``tests/`` keeps
it close to the rest of the verification surface even though it
doesn't get collected by ``pytest`` (no ``test_*`` prefix).
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable


# Per-prefix locked row counts. Lifted from the live verification we
# did in P.5.b/P.5.c (PG + Oracle returned identical counts) and
# re-locked when scenario plants change. Add a new entry when locking
# a new L2 instance against the live DBs.
_LOCKED_COUNTS: dict[str, dict[str, int]] = {
    "spec_example": {
        "transactions": 16,
        "daily_balances": 2,
        "drift": 2,
        "overdraft": 1,
        "limit_breach": 1,
        "todays_exceptions": 3,
        "inv_pair_rolling_anomalies": 4,
        "inv_money_trail_edges": 6,
    },
}

# Smoke mode: matview suffixes we expect to be non-empty for any
# validated L2 instance. Doesn't include `transactions` /
# `daily_balances` because some L2s may have legitimately empty seed
# scenarios for either.
_SMOKE_SUFFIXES = (
    "transactions",
    "daily_balances",
    "todays_exceptions",
    "inv_money_trail_edges",
)


def _connect_pg(url: str) -> tuple[object, Callable[[str], int]]:
    import psycopg
    conn = psycopg.connect(url)

    def count(table: str) -> int:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]

    return conn, count


def _connect_oracle(url: str) -> tuple[object, Callable[[str], int]]:
    import oracledb  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs
    conn = oracledb.connect(url)

    def count(table: str) -> int:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]

    return conn, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dialect", required=True, choices=["postgres", "oracle"],
    )
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--prefix", default="spec_example",
        help="L2 instance prefix (default: spec_example)",
    )
    parser.add_argument(
        "--smoke", action="store_true",
        help="Skip exact counts; just assert ≥1 row in canonical matviews",
    )
    args = parser.parse_args()

    if args.dialect == "postgres":
        conn, count = _connect_pg(args.url)
    else:
        conn, count = _connect_oracle(args.url)

    failures: list[str] = []

    if args.smoke:
        targets = [(f"{args.prefix}_{s}", None) for s in _SMOKE_SUFFIXES]
    else:
        if args.prefix not in _LOCKED_COUNTS:
            print(
                f"FATAL: no locked counts for prefix {args.prefix!r}. "
                f"Pass --smoke to skip exact counts, or lock the counts "
                f"in _LOCKED_COUNTS first.",
                file=sys.stderr,
            )
            return 2
        counts = _LOCKED_COUNTS[args.prefix]
        targets = [(f"{args.prefix}_{s}", n) for s, n in counts.items()]

    for table, expected in targets:
        try:
            actual = count(table)
        except Exception as e:
            failures.append(f"{table}: query failed: {e}")
            continue
        if expected is None:
            ok = actual >= 1
            marker = "ok" if ok else "FAIL"
            print(f"  [{marker}] {table:60s} {actual:4d} (expected ≥1)")
            if not ok:
                failures.append(f"{table}: got 0 rows, expected ≥1")
        else:
            ok = actual == expected
            marker = "ok" if ok else "FAIL"
            print(f"  [{marker}] {table:60s} {actual:4d} (expected {expected})")
            if not ok:
                failures.append(
                    f"{table}: got {actual} rows, expected {expected}"
                )

    conn.close()
    if failures:
        print(f"\n{len(failures)} mismatch(es):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"\nAll {len(targets)} table counts match expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
