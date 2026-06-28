"""DY.1 — auto-apply `@tier(Tier.APP2)` + `@needs(Need.PLAYWRIGHT)` to every
test under tests/e2e/app2_browser/.

This dir is the terminal browser tier — the root `tests/e2e/test_*.py`
files moved here in DY.1's finish of the CB.6 `-m mark` → `--tier`
migration. Same auto-marking shape as `tests/e2e/app2/conftest.py` and
`tests/unit/conftest.py`: adding a browser test is now
`touch tests/e2e/app2_browser/test_foo.py` — no per-file `@pytest.mark.
browser` to forget. Forgetting that mark is EXACTLY how
`test_dashboard_driver.py` (and ~10 other tests) ran NOWHERE: tier-marked
but not `browser`-marked, so the old `-m browser` selector deselected them
and no dir layer collected them. The runner's `app2_browser` layer now
selects THIS DIRECTORY, so the tier-dir IS the run-set; the legacy
`-m browser` selector + the `browser` marker registration are retired.

Unlike `app2/conftest.py` there are no fixture re-exports here: the browser
tests inherit their fixtures (dashboard drivers, cfg) from the parent
`tests/e2e/conftest.py` and use none of the isolation fixtures
(`isolated_cfg` / `db_conn` / `enforce_readonly`).
"""

from __future__ import annotations

import pathlib
from typing import Any

from tests._marks import Need, Tier, needs, tier

_APP2_BROWSER_MARKS = (tier(Tier.APP2), needs(Need.PLAYWRIGHT))
_OWN_DIR = pathlib.Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Auto-apply the APP2 tier + PLAYWRIGHT need to every item collected
    from this dir. Items with an explicit `@tier(...)` already applied
    (the moved files declare theirs) pass through unchanged — the
    path-string filter ensures we only touch items that live below us.
    """
    _ = config
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        if any(m.name == "tier" for m in item.iter_markers()):
            continue
        for mark in _APP2_BROWSER_MARKS:
            item.add_marker(mark)
