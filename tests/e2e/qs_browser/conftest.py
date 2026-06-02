"""CB.5 — auto-apply `@tier(Tier.QS_BROWSER)` to every test under
tests/e2e/qs_browser/.

Same inheritance shape as `tests/unit/conftest.py` (CB.2) and
`tests/e2e/app2/conftest.py` (CB.3). Files moved here in CB.5:

- `test_dashboard_driver.py` — driver protocol tests against QS embed
- `test_*_dashboard_renders.py` (exec / inv / l1) — QS render gates
- `test_l2ft_metadata_cascade.py` — QS-side metadata cascade probe

Parametrized `[qs, app2]` tests that run against BOTH renderers
(test_inv_drilldown.py, test_l1_filters.py, etc.) stay at
`tests/e2e/` root for now — they don't fit a single tier marker.
CB.6 resolves them (likely per-parametrize-instance marks via the
`_dashboard_driver` fixture, OR a tier-disjunction
`@tier_any(Tier.QS_BROWSER, Tier.APP2)` extension to the mark vocab).

The agreement tests (`test_*_dashboard_agreement.py`) similarly
stay at root — they exercise qs + app2 + direct DB read in one
test, multi-tier semantics need their own design pass.

`Need.AWS_QS` + `Need.PLAYWRIGHT` are NOT auto-added here (the
composition rules in `tests/conftest.py` enforce that
`tier(qs_browser)` tests carry both — every file in this dir
should declare them via `@needs(Need.AWS_QS, Need.PLAYWRIGHT)`
at the test or module level). CB.6 audits this and flips the
WARN to ERROR.
"""

from __future__ import annotations

import pathlib
from typing import Any

from tests._marks import Need, Tier, needs, tier

# CB.7 (refactored 2026-06-02) — re-export the isolation primitives so
# qs_browser-tier tests (especially the cross-tier consumers of
# `IsolationScope.AGREEMENT_INV` / `AGREEMENT_AUDIT`) get the same
# `isolated_cfg` + `db_conn` fixtures as the db tier.
from tests.e2e._isolation import (  # noqa: F401 — re-export so pytest discovers the fixtures
    _isolate_cfg,
    _isolated_cfg_key,
    db_conn,
    isolated_cfg,
)

__all__ = ["_isolate_cfg", "_isolated_cfg_key", "db_conn", "isolated_cfg"]


_QS_BROWSER_TIER_MARK = tier(Tier.QS_BROWSER)
_QS_BROWSER_NEEDS_MARK = needs(Need.AWS_QS, Need.PLAYWRIGHT)
_OWN_DIR = pathlib.Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Apply `@tier(Tier.QS_BROWSER)` + `@needs(AWS_QS, PLAYWRIGHT)` to
    every item collected from this dir.

    The needs marker is auto-attached alongside the tier marker
    because the dir IS the source of truth for both — every QS embed
    test needs AWS QS (the subscription) + Playwright (the browser
    driver). A future per-file override that needs additional deps
    (e.g., docker too) declares `@needs(...)` at the file level and
    pytest merges marks across the chain.
    """
    _ = config
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        if not any(m.name == "tier" for m in item.iter_markers()):
            item.add_marker(_QS_BROWSER_TIER_MARK)
        if not any(m.name == "needs" for m in item.iter_markers()):
            item.add_marker(_QS_BROWSER_NEEDS_MARK)
