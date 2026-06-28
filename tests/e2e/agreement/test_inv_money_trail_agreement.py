"""DW.3 — high-watermark validator for L2 money_trail agreement.

Reads the 2 producer artifacts and asserts:

    spine == direct_matview(root) == App2(root)

Same shape as `test_inv_anomaly_agreement.py`; per-invariant
projection lives in the producer.

(QS was the third leg until DW.3; with QuickSight removed, direct-DB
is the truth anchor and App2 the corroborator.)
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._marks import inputs
from tests.e2e._agreement import artifact_exists, read_rendered_rows


_DIRECT = "tests/e2e/db/test_inv_direct.py::test_money_trail_direct_extract"
_APP2 = "tests/e2e/app2/test_inv_money_trail_app2.py::test_money_trail_app2_extract"


def _read_meta(layer: str, name: str, key: str) -> Any:
    payload = read_rendered_rows(layer, name)
    assert payload, f"meta {layer}/{name}.json empty"
    return payload[0][key]


def _row_keys(layer: str, name: str) -> set[tuple[Any, ...]]:
    rows = read_rendered_rows(layer, name)
    return {tuple(row["natural_key"]) for row in rows if "natural_key" in row}


@inputs(_DIRECT, _APP2)
def test_money_trail_agreement() -> None:
    """L2-OPTIONAL (same skip discipline as `test_anomaly_agreement`):
    money_trail needs a leaf-money chain role the L2 must declare. When
    it doesn't, the producers skip (backlog #239) and there's nothing to
    validate — skip rather than hard-fail. A producer FAILURE goes red
    upstream and halts the chain first (see `_agreement.artifact_exists`).
    """
    if not artifact_exists("db", "money_trail_direct_meta"):
        pytest.skip(
            "money_trail producers skipped — the L2 doesn't declare the "
            "chain role (backlog #239); nothing to cross-validate."
        )
    direct_count = _read_meta(
        "db", "money_trail_direct_meta", "direct_count",
    )
    app2_count = _read_meta(
        "app2", "money_trail_app2_meta", "app2_count",
    )

    assert direct_count == app2_count, (
        f"money_trail count direct/app2 disagree: "
        f"direct={direct_count} app2={app2_count}"
    )

    direct_keys = _row_keys("db", "money_trail_direct_rows")
    app2_keys = _row_keys("app2", "money_trail_app2_rows")
    assert direct_keys == app2_keys, (
        f"money_trail row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )
