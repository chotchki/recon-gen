"""CB.5 — auto-apply `@tier(Tier.QS_API)` to every test under
tests/e2e/qs_api/.

Same inheritance shape as `tests/unit/conftest.py` (CB.2) and the
sibling `app2/` / `qs_browser/` conftests. Files moved here in CB.5:

- `test_*_deployed_resources.py` (exec / inv / l1) — boto3
  `describe_data_source` / `describe_data_set` / `describe_analysis` /
  `describe_dashboard` shape assertions
- `test_*_dashboard_structure.py` (exec / inv / l1) — analysis JSON
  + dashboard JSON shape gates against the QS describe API

`Need.AWS_QS` is NOT auto-added here (the composition rules in
`tests/conftest.py` enforce that `tier(qs_api)` tests carry it —
every file in this dir should declare it via
`@needs(Need.AWS_QS)`). CB.6 audits this and flips the WARN to
ERROR.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from recon_gen.common.config import Config
from tests._marks import Need, Tier, needs, tier
from tests.e2e.conftest import _load_session_cfg, _substitute_container_url


# CB.17.d — qs_api-tier `cfg` override. See `tests/e2e/db/conftest.py`'s
# `cfg` override for the full rationale; same shape here.
@pytest.fixture(scope="session")
def cfg(request: pytest.FixtureRequest) -> Config:
    base = _load_session_cfg(request)
    return _substitute_container_url(base, request)


_QS_API_TIER_MARK = tier(Tier.QS_API)
_QS_API_NEEDS_MARK = needs(Need.AWS_QS)
_OWN_DIR = pathlib.Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Apply `@tier(Tier.QS_API)` + `@needs(AWS_QS)` to every item
    collected from this dir.

    The needs marker is auto-attached alongside the tier marker
    because the dir IS the source of truth for both — every QS describe_*
    test needs AWS QS (the subscription).
    """
    _ = config
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        if not any(m.name == "tier" for m in item.iter_markers()):
            item.add_marker(_QS_API_TIER_MARK)
        if not any(m.name == "needs" for m in item.iter_markers()):
            item.add_marker(_QS_API_NEEDS_MARK)
