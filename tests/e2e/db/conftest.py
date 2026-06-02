"""CB.4 — auto-apply `@tier(Tier.DB)` to every test under tests/e2e/db/.

Same inheritance shape as `tests/unit/conftest.py` (CB.2) and the
sibling `app2/` / `qs_browser/` / `qs_api/` conftests. Files moved
here in CB.4:

- `test_dataset_sql_smoke.py` — per-dataset CustomSQL parse + bind
  smoke against the live demo DB via `connect_demo_db`
- `test_demo_apply_row_counts.py` — post-`demo apply` row-count
  smoke (≥1 row in every named matview the seed should populate)
- `test_audit_pdf_render_verify.py` — audit PDF render + verify
  against the live demo DB

These touch a DB but no QS embed and no browser rendering — pure
DB tier per the audit doc taxonomy.

`Need.DOCKER` is auto-added alongside the tier marker because every
DB-tier test needs a database container (PG / Oracle Docker images
when run locally; the runner's `--targets=aw` cells use AWS RDS so
the DOCKER need is "ok if absent when AWS_RDS is up" — CB.6's needs
audit may refine this to a logical-OR shape, but for now DOCKER is
the cheap-default need that won't false-positive a skip in the AWS
cells either because the runner already handles RDS-vs-Docker
substrate selection upstream of the needs check).
"""

from __future__ import annotations

import pathlib
from typing import Any

from tests._marks import Need, Tier, needs, tier


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
