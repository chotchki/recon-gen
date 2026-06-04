"""CB.3 — auto-apply `@tier(Tier.APP2)` to every test under tests/e2e/app2/.

Same inheritance shape as `tests/unit/conftest.py` (CB.2). Files moved
here in CB.3:

- `test_html2_*.py` — the HTML2 (renamed-ish App2) rendering tests
  that drive the self-hosted dashboard server through `App2Driver` from
  `tests/e2e/_drivers/app2.py`.

`test_bv33_trainer_dogfood.py` (the Trainer dogfood harness) ALSO
belongs in this tier but stays at `tests/e2e/` for now — it's pinned
to a sqlite3 substrate that CB.8 is migrating to DuckDB in an
isolated worktree. Once that migration lands the file moves here in
the same commit that nukes the parallel-development boundary.

See `docs/audits/cb_test_layers_update.md` for the full design.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from recon_gen.common.config import Config
from tests._marks import Tier, tier

# CB.7 (refactored 2026-06-02) — re-export the isolation primitives so
# app2-tier tests (especially the cross-tier consumers of
# `IsolationScope.AGREEMENT_INV` / `AGREEMENT_AUDIT`) get the same
# `isolated_cfg` + `db_conn` fixtures as the db tier. The DB tier writes;
# app2 reads via the shared scope-keyed prefix.
from tests.e2e._isolation import (  # noqa: F401 — re-export so pytest discovers the fixtures
    _isolate_cfg,
    _isolated_cfg_key,
    db_conn,
    enforce_readonly,
    isolated_cfg,
)
from tests.e2e.conftest import _load_session_cfg, _substitute_container_url

__all__ = [
    "_isolate_cfg", "_isolated_cfg_key", "db_conn", "enforce_readonly",
    "isolated_cfg",
]


# CB.17.d — app2-tier `cfg` override. See `tests/e2e/db/conftest.py`'s
# `cfg` override for the full rationale; same shape here.
@pytest.fixture(scope="session")
def cfg(request: pytest.FixtureRequest) -> Config:
    base = _load_session_cfg(request)
    return _substitute_container_url(base, request)


_APP2_TIER_MARK = tier(Tier.APP2)
_OWN_DIR = pathlib.Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Apply `@tier(Tier.APP2)` to every item collected from this dir.

    Mirror of `tests/unit/conftest.py`'s shape — the path-string filter
    ensures we only auto-mark items that actually live below us, and
    items with an explicit `@tier(...)` already applied (per-file
    override) pass through unchanged.
    """
    _ = config
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        if any(m.name == "tier" for m in item.iter_markers()):
            continue
        item.add_marker(_APP2_TIER_MARK)
