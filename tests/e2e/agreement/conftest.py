"""DW.3 — auto-apply `@tier(Tier.AGREEMENT)` to every test under
tests/e2e/agreement/.

Same inheritance shape as `tests/unit/conftest.py` (CB.2),
`tests/e2e/db/conftest.py`, and `tests/e2e/app2/conftest.py` (CB.3).

This dir holds the high-watermark cross-renderer agreement validators
(`test_*_agreement.py`) — pure JSON-artifact readers that compare the
rows the db + app2 producers rendered. They request NO fixtures: no
DB, no browser, no AWS. So unlike the qs_browser conftest this used to
be, NO `@needs(...)` is auto-applied — the agreement tier needs
nothing but the producer artifacts on disk (which the earlier db +
app2 layers in the runner chain wrote under the shared run dir).

The runner's `agreement` layer collects `tests/e2e/db/` +
`tests/e2e/app2/` alongside this dir (so the validators' `@inputs(...)`
producer nodeids resolve at collection time) then selects only this
tier to RUN via `--tier=agreement`. See `runner.py::_layer_command`.
"""

from __future__ import annotations

import pathlib
from typing import Any

from tests._marks import Tier, tier


_AGREEMENT_TIER_MARK = tier(Tier.AGREEMENT)
_OWN_DIR = pathlib.Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Apply `@tier(Tier.AGREEMENT)` to every item collected from this
    dir. No needs marker — the agreement tier reads artifacts only.
    """
    _ = config
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        if not any(m.name == "tier" for m in item.iter_markers()):
            item.add_marker(_AGREEMENT_TIER_MARK)
