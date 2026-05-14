"""TestGeneratorCache unit tests (X.4.h.2).

Locks the contract for Studio's data-shaping knob cache:

- Initialized from cfg.test_generator (Snapshot, not pin); cfg-side
  mutation does NOT leak into the cache (the dataclass is frozen on
  the cfg side).
- Partial update preserves None-valued fields (``end_date=None`` is a
  valid value meaning "use locked default", so a sentinel is the only
  way to express "leave unchanged").
- Full replace + patched_config(cfg) clone the new state into a
  fresh Config without mutating the startup cfg.
"""

from __future__ import annotations

from datetime import date

from quicksight_gen.common.config import (
    PlantKind,
    TestGeneratorConfig,
)
from quicksight_gen.common.l2.tg_cache import TestGeneratorCache
from tests._test_helpers import make_test_config


def test_from_config_snapshots_cfg_test_generator() -> None:
    cfg = make_test_config()
    cache = TestGeneratorCache.from_config(cfg)
    assert cache.get() == cfg.test_generator
    assert cache.get() is cfg.test_generator  # frozen, so reuse is safe


def test_get_returns_current_state() -> None:
    state = TestGeneratorConfig(scope="full", plants=("drift",))
    cache = TestGeneratorCache(state)
    assert cache.get() is state


def test_replace_swaps_state() -> None:
    cache = TestGeneratorCache(TestGeneratorConfig())
    new_state = TestGeneratorConfig(scope="exceptions_only", seed=12345)
    cache.replace(new_state)
    assert cache.get() is new_state


def test_update_partial_preserves_unset_fields() -> None:
    """Setting plants only must NOT change scope / end_date / seed."""
    initial = TestGeneratorConfig(
        scope="exceptions_only",
        end_date=date(2026, 5, 14),
        seed=999,
        plants=(),
    )
    cache = TestGeneratorCache(initial)
    cache.update(plants=("drift", "overdraft"))
    new = cache.get()
    assert new.plants == ("drift", "overdraft")
    assert new.scope == "exceptions_only"  # preserved
    assert new.end_date == date(2026, 5, 14)  # preserved
    assert new.seed == 999  # preserved


def test_update_can_set_seed_to_none() -> None:
    """None is a valid value (means 'use locked default'), so explicit
    None must clear the seed — only the _UNSET sentinel preserves."""
    cache = TestGeneratorCache(TestGeneratorConfig(seed=42))
    cache.update(seed=None)
    assert cache.get().seed is None


def test_update_can_set_end_date_to_none() -> None:
    cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
    )
    cache.update(end_date=None)
    assert cache.get().end_date is None


def test_update_returns_new_state() -> None:
    cache = TestGeneratorCache(TestGeneratorConfig())
    plants: tuple[PlantKind, ...] = ("limit_breach",)
    returned = cache.update(plants=plants)
    assert returned is cache.get()
    assert returned.plants == plants


def test_patched_config_returns_fresh_clone() -> None:
    """patched_config must produce a new Config without mutating the
    startup one — that's the contract the deploy route depends on."""
    cfg = make_test_config()
    original_tg = cfg.test_generator
    cache = TestGeneratorCache.from_config(cfg)
    cache.update(plants=("drift",))
    patched = cache.patched_config(cfg)
    # Patched gets the new TG state.
    assert patched.test_generator.plants == ("drift",)
    # Startup cfg is unchanged.
    assert cfg.test_generator is original_tg
    assert cfg.test_generator.plants == ()
    # Patched is a *clone*, not the same object.
    assert patched is not cfg
    # Other cfg fields propagate.
    assert patched.aws_account_id == cfg.aws_account_id
    assert patched.aws_region == cfg.aws_region


def test_multiple_updates_compose() -> None:
    cache = TestGeneratorCache(TestGeneratorConfig())
    cache.update(plants=("drift",))
    cache.update(seed=12345)
    cache.update(scope="exceptions_only")
    final = cache.get()
    assert final.plants == ("drift",)
    assert final.seed == 12345
    assert final.scope == "exceptions_only"
