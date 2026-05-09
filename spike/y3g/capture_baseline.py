"""Y.3.g spike — capture current emitter output for Account Network per dialect.

Throwaway. Run via `.venv/bin/python spike/y3g/capture_baseline.py`.

Writes baseline SQL strings to spike/y3g/baseline/<dialect>.sql so we can
diff against the SQLAlchemy-emitted SQL later.
"""

from __future__ import annotations

from pathlib import Path

from quicksight_gen.apps.investigation.datasets import (
    build_account_network_dataset,
)
from quicksight_gen.common.sql import Dialect
from tests._test_helpers import make_test_config

BASELINE_DIR = Path(__file__).parent / "baseline"


def _build_for_dialect(dialect: Dialect) -> str:
    cfg = make_test_config(
        aws_region="us-east-2",
        l2_instance_prefix="spec_example",
        dialect=dialect,
    )
    ds = build_account_network_dataset(cfg)
    for physical in ds.PhysicalTableMap.values():
        return physical.CustomSql.SqlQuery
    raise AssertionError("no PhysicalTable")


def main() -> None:
    for d in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.SQLITE):
        sql = _build_for_dialect(d)
        path = BASELINE_DIR / f"{d.value}.sql"
        path.write_text(sql)
        line_count = len(sql.splitlines())
        char_count = len(sql)
        print(
            f"[{d.value}] {line_count} lines, {char_count} chars → {path}"
        )


if __name__ == "__main__":
    main()
