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

from tests._marks import Tier, tier


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
