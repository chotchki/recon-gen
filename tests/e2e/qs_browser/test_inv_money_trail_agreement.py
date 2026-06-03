"""CB.5 stage 2 — high-watermark validator for L2 money_trail agreement.

Reads the 3 producer artifacts and asserts:

    spine == direct_matview(root) == App2(root) == QS(root)

Same shape as `test_inv_anomaly_agreement.py`; per-invariant
projection lives in the producer.
"""

from __future__ import annotations

from typing import Any

import pytest

from recon_gen.common.env_keys import RECON_GEN_E2E

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "Investigation agreement validator needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

from tests._marks import inputs  # noqa: E402
from tests.e2e._agreement import read_rendered_rows  # noqa: E402


_DIRECT = "tests/e2e/db/test_inv_direct.py::test_money_trail_direct_extract"
_APP2 = "tests/e2e/app2/test_inv_money_trail_app2.py::test_money_trail_app2_extract"
_QS = "tests/e2e/qs_browser/test_inv_money_trail_qs.py::test_money_trail_qs_extract"


def _read_meta(layer: str, name: str, key: str) -> Any:
    payload = read_rendered_rows(layer, name)
    assert payload, f"meta {layer}/{name}.json empty"
    return payload[0][key]


def _row_keys(layer: str, name: str) -> set[tuple[Any, ...]]:
    rows = read_rendered_rows(layer, name)
    return {tuple(row["natural_key"]) for row in rows if "natural_key" in row}


@inputs(_DIRECT, _APP2, _QS)
def test_money_trail_three_way_agreement() -> None:
    direct_count = _read_meta(
        "db", "money_trail_direct_meta", "direct_count",
    )
    app2_count = _read_meta(
        "app2", "money_trail_app2_meta", "app2_count",
    )
    qs_available = _read_meta(
        "qs_browser", "money_trail_qs_meta", "qs_available",
    )
    qs_count = _read_meta(
        "qs_browser", "money_trail_qs_meta", "qs_count",
    )

    assert direct_count == app2_count, (
        f"money_trail count direct/app2 disagree: "
        f"direct={direct_count} app2={app2_count}"
    )
    if qs_available:
        assert qs_count == direct_count, (
            f"money_trail count direct/qs disagree: "
            f"direct={direct_count} qs={qs_count}"
        )

    direct_keys = _row_keys("db", "money_trail_direct_rows")
    app2_keys = _row_keys("app2", "money_trail_app2_rows")
    assert direct_keys == app2_keys, (
        f"money_trail row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )
    if qs_available:
        qs_keys = _row_keys("qs_browser", "money_trail_qs_rows")
        assert direct_keys == qs_keys, (
            f"money_trail row-identity disagreement direct/qs:\n"
            f"  direct-only: {sorted(direct_keys - qs_keys)[:5]}\n"
            f"  qs-only: {sorted(qs_keys - direct_keys)[:5]}"
        )
