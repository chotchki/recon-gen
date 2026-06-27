"""DW.3 — high-watermark validator for L2 anomaly agreement.

Reads the 2 producer artifacts (db direct + app2; no PDF leg for L2
per AT.5.d) and asserts:

    spine == direct_matview(σ) == App2(σ)

Row-identity for direct vs App2 (set equality on the matview's
natural-key tuple `(sender, recipient, window_end)`).

(QS was the third leg until DW.3; with QuickSight removed, direct-DB
is the truth anchor and App2 the corroborator.)
"""

from __future__ import annotations

from typing import Any

from tests._marks import inputs
from tests.e2e._agreement import read_rendered_rows


_DIRECT = "tests/e2e/db/test_inv_direct.py::test_anomaly_direct_extract"
_APP2 = "tests/e2e/app2/test_inv_anomaly_app2.py::test_anomaly_app2_extract"


def _read_meta(layer: str, name: str, key: str) -> Any:
    payload = read_rendered_rows(layer, name)
    assert payload, f"meta {layer}/{name}.json empty"
    return payload[0][key]


def _row_keys(layer: str, name: str) -> set[tuple[Any, ...]]:
    rows = read_rendered_rows(layer, name)
    return {tuple(row["natural_key"]) for row in rows if "natural_key" in row}


@inputs(_DIRECT, _APP2)
def test_anomaly_agreement() -> None:
    """Agreement for L2 anomaly (AT.5.e):

        spine == direct_matview(σ) == App2(σ)

    The producers each asserted their own piece (spine == direct
    in the db producer; App2 ≥ expected in theirs); the validator
    confirms cross-renderer set equality.
    """
    direct_count = _read_meta("db", "anomaly_direct_meta", "direct_count")
    app2_count = _read_meta("app2", "anomaly_app2_meta", "app2_count")

    assert direct_count == app2_count, (
        f"anomaly count direct/app2 disagree: direct={direct_count} "
        f"app2={app2_count}"
    )

    direct_keys = _row_keys("db", "anomaly_direct_rows")
    app2_keys = _row_keys("app2", "anomaly_app2_rows")
    assert direct_keys == app2_keys, (
        f"anomaly row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )
