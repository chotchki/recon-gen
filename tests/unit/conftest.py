"""CB.2 — auto-apply `@tier(Tier.UNIT)` to every test under tests/unit/.

Inheritance approach: instead of every file declaring
`pytestmark = [tier(Tier.UNIT)]` at the top, the dir-level conftest
adds the mark to every collected item under tests/unit/. Tests inherit
the tier from their location.

Trade-offs vs the per-file approach:

- **Pro**: zero per-file boilerplate. Authors of new unit tests don't
  need to remember the import + the module-level declaration.
- **Pro**: a file's tier follows its directory — the moment a test
  moves out of `tests/unit/`, it stops being unit-tier (which is
  desirable; moving a test to `tests/e2e/db/` SHOULD lose the
  unit-tier classification).
- **Con**: less explicit. A reader looking at one file in isolation
  won't see the tier mark; they have to know the convention.
- **Con**: if a per-file override is needed (rare — e.g., a unit
  test that genuinely belongs in a different tier), it has to fight
  the auto-mark. The simplest fight: an explicit
  `pytestmark = [tier(Tier.OTHER)]` at the file level wins (pytest's
  marker semantics dedupe by attribute name; the file-level mark is
  applied AFTER this hook and overrides).

CB.3-CB.5 mirror this shape in tests/e2e/ (app2 / db / qs tiers).
CB.6 then deletes the runner's hardcoded test-file lists and lets
the marks-driven `--tier=X` filter do the dispatch.
"""

from __future__ import annotations

from typing import Any

from tests._marks import Tier, tier


_UNIT_TIER_MARK = tier(Tier.UNIT)


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Apply `@tier(Tier.UNIT)` to every item under this conftest's dir.

    Pytest delivers all items being collected for the current session
    here, not just the ones under tests/unit/. The path filter ensures
    we only auto-mark items that actually live below us — a sibling
    `tests/data/` test collected in the same run doesn't get tagged
    UNIT.

    Skip items that already carry an explicit `@tier(...)` marker
    (whether applied via decorator, module-level `pytestmark`, or
    parametrize). The marker dedupes work via pytest's own behavior
    but the explicit-override-wins rule keeps the surface intuitive
    for the rare file that needs a different tier.
    """
    _ = config  # unused
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        # If the test already carries an explicit tier mark, respect it
        # (override). The marker is inserted via `add_marker(...)` not
        # via decorator-style merge, so multiple application would
        # accumulate as duplicate markers — duplicate `tier` markers
        # then confuse the CB.0 composition rules. Skip if present.
        if any(m.name == "tier" for m in item.iter_markers()):
            continue
        item.add_marker(_UNIT_TIER_MARK)


# Module-scope: this conftest's containing directory. Computed once
# at import time; cheap to test against item.path strings.
import pathlib  # noqa: E402,PLC0415: late-import next to its sole use
_OWN_DIR = pathlib.Path(__file__).resolve().parent
