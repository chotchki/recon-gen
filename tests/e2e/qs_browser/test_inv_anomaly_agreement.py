"""CB.5 stage 2 — high-watermark validator for L2 anomaly agreement.

Reads the 3 producer artifacts (db direct + app2 + qs_browser; no
PDF leg for L2 per AT.5.d) and asserts:

    spine == direct_matview(σ) == App2(σ) == QS(σ)

Row-identity for direct vs App2 (set equality on the matview's
natural-key tuple `(sender, recipient, window_end)`); QS count
equality when QS leg ran (the producer's `qs_available` flag).
"""

from __future__ import annotations

from typing import Any

import pytest

from recon_gen.common.env_keys import RECON_GEN_E2E

if not RECON_GEN_E2E.get_or_none():
    # Mirror the L2 producers' module-level skip so the validator
    # collection matches the producer collection (the @inputs marker
    # requires the producer nodeids to exist in the same collection).
    pytest.skip(
        "Investigation agreement validator needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

from tests._marks import inputs  # noqa: E402
from tests.e2e._agreement import read_rendered_rows  # noqa: E402


_DIRECT = "tests/e2e/db/test_inv_direct.py::test_anomaly_direct_extract"
_APP2 = "tests/e2e/app2/test_inv_anomaly_app2.py::test_anomaly_app2_extract"
_QS = "tests/e2e/qs_browser/test_inv_anomaly_qs.py::test_anomaly_qs_extract"


def _read_meta(layer: str, name: str, key: str) -> Any:
    payload = read_rendered_rows(layer, name)
    assert payload, f"meta {layer}/{name}.json empty"
    return payload[0][key]


def _row_keys(layer: str, name: str) -> set[tuple[Any, ...]]:
    rows = read_rendered_rows(layer, name)
    return {tuple(row["natural_key"]) for row in rows if "natural_key" in row}


@inputs(_DIRECT, _APP2, _QS)
def test_anomaly_three_way_agreement() -> None:
    """3-way agreement for L2 anomaly (AT.5.e):

        spine == direct_matview(σ) == App2(σ) == QS(σ)

    The producers each asserted their own piece (spine == direct
    in the db producer; App2/QS ≥ expected in theirs); the
    validator confirms cross-renderer set equality.
    """
    direct_count = _read_meta("db", "anomaly_direct_meta", "direct_count")
    app2_count = _read_meta("app2", "anomaly_app2_meta", "app2_count")
    qs_available = _read_meta(
        "qs_browser", "anomaly_qs_meta", "qs_available",
    )
    qs_count = _read_meta("qs_browser", "anomaly_qs_meta", "qs_count")

    assert direct_count == app2_count, (
        f"anomaly count direct/app2 disagree: direct={direct_count} "
        f"app2={app2_count}"
    )
    if qs_available:
        assert qs_count == direct_count, (
            f"anomaly count direct/qs disagree: direct={direct_count} "
            f"qs={qs_count}"
        )

    direct_keys = _row_keys("db", "anomaly_direct_rows")
    app2_keys = _row_keys("app2", "anomaly_app2_rows")
    assert direct_keys == app2_keys, (
        f"anomaly row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )
    if qs_available:
        qs_keys = _row_keys("qs_browser", "anomaly_qs_rows")
        assert direct_keys == qs_keys, (
            f"anomaly row-identity disagreement direct/qs:\n"
            f"  direct-only: {sorted(direct_keys - qs_keys)[:5]}\n"
            f"  qs-only: {sorted(qs_keys - direct_keys)[:5]}"
        )
