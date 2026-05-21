"""Regression guard: the CLI serve path wires an options fetcher.

The local ``recon-gen dashboards`` / ``studio`` server composes its
``ServedDashboard`` map inside ``_html_serve._serve``. A bug shipped where
that composition omitted the ``options_fetcher`` — so dataset-backed
(LinkedValues) parameter controls (the Daily Statement account/role
picker, plus Money Trail / Account Network / Recipient Fanout) rendered
with an EMPTY option list. With nothing to pick, the (correct,
parameterized) dataset query never received a value and the sheet stayed
permanently blank.

It went unnoticed because the e2e harness (``_harness_html2`` /
``conftest``) DID wire an options fetcher — so the browser tests
exercised a faithfully-served app while the production CLI path served a
broken one. The two serve paths diverged with nothing guarding parity.

This test pins the wiring at the shared seam (``build_real_dashboards``),
so the CLI path can't silently drop it again.
"""

from __future__ import annotations

from typing import Any

from recon_gen.cli._html_serve import (
    REAL_APPS,
    build_real_app,
    build_real_dashboards,
)
from recon_gen.common.l2 import default_l2_instance
from tests._test_helpers import make_test_config


def test_build_real_dashboards_wires_options_fetcher_for_every_app() -> None:
    """Every real app's ServedDashboard must carry both a data fetcher
    AND an options fetcher — the latter is what populates dataset-backed
    parameter-control dropdowns from their companion option-source
    datasets."""
    cfg = make_test_config()
    instance = default_l2_instance()
    real_apps = [
        (name, *build_real_app(name, cfg, instance)) for name in REAL_APPS
    ]
    # The fetchers capture the pool lazily (in a closure), so a sentinel
    # is fine — we're asserting the WIRING, not running a query.
    sentinel_pool: Any = object()

    dashboards = build_real_dashboards(
        real_apps, cfg, pool=sentinel_pool, theme=None,
    )

    assert set(dashboards) == set(REAL_APPS)
    for name, served in dashboards.items():
        assert served.options_fetcher is not None, (
            f"{name}: ServedDashboard has no options_fetcher — "
            f"dataset-backed (LinkedValues) parameter controls will "
            f"render with empty option lists and the parameterized query "
            f"will never narrow (permanently blank sheet)."
        )
        assert served.data_fetcher is not None, f"{name}: no data_fetcher"
