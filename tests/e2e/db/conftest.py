"""CB.4 — auto-apply `@tier(Tier.DB)` to every test under tests/e2e/db/.

CB.7 (refactored 2026-06-02) — the isolation primitives (`isolated_cfg`,
`db_conn`, `_isolate_cfg`, `_isolated_cfg_key`) moved to
`tests/e2e/_isolation.py` so all tier conftests (db / app2 / qs_browser)
can re-export the same fixtures. See that module's docstring for the
provider-marked isolation contract.

Sister-tier conftests do the same re-export pattern:
- `tests/e2e/app2/conftest.py`
- `tests/e2e/qs_browser/conftest.py`

Tier-mark auto-application (CB.4):
- `test_dataset_sql_smoke.py` — per-dataset CustomSQL parse + bind
  smoke against the live demo DB via `connect_demo_db`.
- `test_demo_apply_row_counts.py` — post-`demo apply` row-count smoke.
- `test_audit_pdf_render_verify.py` — audit PDF render + verify.

`Need.DOCKER` is auto-added alongside the tier marker because every
DB-tier test needs a database container.
"""

from __future__ import annotations

import pathlib
from typing import Any

from tests._marks import Need, Tier, needs, tier

# Re-export the isolation primitives from the shared module so db-tier
# tests find them via the same conftest lookup. The fixtures themselves
# live in `tests/e2e/_isolation.py` so app2 + qs_browser tier conftests
# can import the same primitives.
from tests.e2e._isolation import (  # noqa: F401 — re-export so pytest discovers the fixtures
    _isolate_cfg,
    _isolated_cfg_key,
    db_conn,
    enforce_readonly,
    isolated_cfg,
)

__all__ = [
    "_isolate_cfg", "_isolated_cfg_key", "db_conn", "enforce_readonly",
    "isolated_cfg",
]


_DB_TIER_MARK = tier(Tier.DB)
_DB_NEEDS_MARK = needs(Need.DOCKER)
_OWN_DIR = pathlib.Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Apply `@tier(Tier.DB)` + `@needs(DOCKER)` to every item collected
    from this dir."""
    _ = config
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        if not any(m.name == "tier" for m in item.iter_markers()):
            item.add_marker(_DB_TIER_MARK)
        if not any(m.name == "needs" for m in item.iter_markers()):
            item.add_marker(_DB_NEEDS_MARK)
