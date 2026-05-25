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

from pathlib import Path

from recon_gen.common.intervals import DateInterval
from recon_gen.common.config import (
    EtlDatasourceConfig,
    PlantKind,
    TestGeneratorConfig,
)
from recon_gen.common.l2.studio_state import (
    SIDEFILE_NAME,
    StudioState,
    load_studio_state,
    save_studio_state,
)
from recon_gen.common.l2.tg_cache import TestGeneratorCache
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


# ----- X.4.h.etl-toggle — etl_hook enable/disable knob -----


def test_etl_hook_enabled_default_true() -> None:
    cache = TestGeneratorCache(TestGeneratorConfig())
    assert cache.is_etl_hook_enabled() is True


def test_set_etl_hook_enabled_round_trip() -> None:
    cache = TestGeneratorCache(TestGeneratorConfig())
    cache.set_etl_hook_enabled(False)
    assert cache.is_etl_hook_enabled() is False
    cache.set_etl_hook_enabled(True)
    assert cache.is_etl_hook_enabled() is True


def test_init_kwarg_seeds_etl_hook_state() -> None:
    cache = TestGeneratorCache(
        TestGeneratorConfig(),
        etl_hook_enabled=False,
    )
    assert cache.is_etl_hook_enabled() is False


def test_patched_config_keeps_etl_hook_pair_when_enabled() -> None:
    """When the toggle is on, both cfg.etl_hook and cfg.etl_datasource
    flow through unchanged — they're a coupled "upstream re-seed" pair
    (step 1 fetch + step 2 pull)."""
    etl_ds = EtlDatasourceConfig(
        url="postgresql://localhost/upstream",
        transactions_table="up_tx",
        daily_balances_table="up_bal",
    )
    cfg = make_test_config(
        etl_hook="echo upstream-pull",
        etl_datasource=etl_ds,
    )
    cache = TestGeneratorCache.from_config(cfg)
    assert cache.is_etl_hook_enabled() is True
    patched = cache.patched_config(cfg)
    assert patched.etl_hook == "echo upstream-pull"
    assert patched.etl_datasource is etl_ds


def test_patched_config_clears_etl_hook_pair_when_disabled() -> None:
    """When the toggle is off, BOTH etl_hook and etl_datasource get
    nuked on the patched cfg — step 1 + step 2-pull both no-op for
    that deploy. Original cfg's stored fields are untouched (re-enable
    + re-deploy gets the whole pair back)."""
    etl_ds = EtlDatasourceConfig(
        url="postgresql://localhost/upstream",
        transactions_table="up_tx",
        daily_balances_table="up_bal",
    )
    cfg = make_test_config(
        etl_hook="echo upstream-pull",
        etl_datasource=etl_ds,
    )
    cache = TestGeneratorCache.from_config(cfg)
    cache.set_etl_hook_enabled(False)
    patched = cache.patched_config(cfg)
    assert patched.etl_hook is None
    assert patched.etl_datasource is None
    # Original cfg's fields preserved (no mutation).
    assert cfg.etl_hook == "echo upstream-pull"
    assert cfg.etl_datasource is etl_ds


def test_patched_config_disable_with_no_pair_is_noop() -> None:
    """When neither field is configured, the toggle's disabled-arm
    still produces None for both — no spurious churn."""
    cfg = make_test_config()
    assert cfg.etl_hook is None
    assert cfg.etl_datasource is None
    cache = TestGeneratorCache.from_config(cfg)
    cache.set_etl_hook_enabled(False)
    patched = cache.patched_config(cfg)
    assert patched.etl_hook is None
    assert patched.etl_datasource is None


# ----- X.4.h.7 — sidefile persistence wiring -----


def test_from_cfg_with_state_no_sidefile_uses_cfg_defaults(
    tmp_path: Path,
) -> None:
    """First-run: no sidefile yet ⇒ cache state matches cfg.test_generator."""
    cfg = make_test_config(
        test_generator=TestGeneratorConfig(scope="full", seed=42),
    )
    cfg_path = tmp_path / "config.yaml"
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    assert cache.get() == cfg.test_generator
    assert cache.is_etl_hook_enabled() is True


def test_from_cfg_with_state_sidefile_overrides_cfg(tmp_path: Path) -> None:
    """Sidefile field set ⇒ wins over cfg.test_generator."""
    cfg = make_test_config(
        test_generator=TestGeneratorConfig(scope="full", seed=42),
    )
    cfg_path = tmp_path / "config.yaml"
    sidefile = tmp_path / SIDEFILE_NAME
    save_studio_state(
        StudioState(
            scope="exceptions_only",
            seed=99999,
            plants=("drift", "overdraft"),
            etl_hook_enabled=False,
        ),
        sidefile,
    )
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    assert cache.get().scope == "exceptions_only"
    assert cache.get().seed == 99999
    assert cache.get().plants == ("drift", "overdraft")
    assert cache.is_etl_hook_enabled() is False


def test_from_cfg_with_state_sidefile_window_used(tmp_path: Path) -> None:
    """Window dates from sidefile take precedence over the today-anchored
    default the constructor would otherwise compute."""
    cfg = make_test_config()
    cfg_path = tmp_path / "config.yaml"
    sidefile = tmp_path / SIDEFILE_NAME
    save_studio_state(
        StudioState(
            window=DateInterval.closed(date(2025, 12, 1), date(2026, 3, 1)),
        ),
        sidefile,
    )
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    assert cache.get_window() == DateInterval.closed(
        date(2025, 12, 1), date(2026, 3, 1),
    )


def test_from_cfg_with_state_sidefile_no_etl_field_keeps_default(
    tmp_path: Path,
) -> None:
    """A sidefile without etl_hook_enabled set still defaults to True."""
    cfg = make_test_config()
    cfg_path = tmp_path / "config.yaml"
    sidefile = tmp_path / SIDEFILE_NAME
    save_studio_state(StudioState(scope="full"), sidefile)
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    assert cache.is_etl_hook_enabled() is True


def test_update_persists_to_sidefile(tmp_path: Path) -> None:
    """Mutation through update() writes the snapshot to the sidefile."""
    cfg = make_test_config()
    cfg_path = tmp_path / "config.yaml"
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    cache.update(scope="exceptions_only", plants=("drift",))
    reloaded = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert reloaded is not None
    assert reloaded.scope == "exceptions_only"
    assert reloaded.plants == ("drift",)


def test_replace_persists_to_sidefile(tmp_path: Path) -> None:
    cfg = make_test_config()
    cfg_path = tmp_path / "config.yaml"
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    cache.replace(TestGeneratorConfig(scope="exceptions_only", seed=12345))
    reloaded = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert reloaded is not None
    assert reloaded.scope == "exceptions_only"
    assert reloaded.seed == 12345


def test_update_window_persists_to_sidefile(tmp_path: Path) -> None:
    cfg = make_test_config()
    cfg_path = tmp_path / "config.yaml"
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    cache.update_window(start=date(2026, 1, 1), end=date(2026, 4, 1))
    reloaded = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert reloaded is not None
    assert reloaded.window == DateInterval.closed(
        date(2026, 1, 1), date(2026, 4, 1),
    )


def test_set_etl_hook_enabled_persists_to_sidefile(tmp_path: Path) -> None:
    cfg = make_test_config()
    cfg_path = tmp_path / "config.yaml"
    cache = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    cache.set_etl_hook_enabled(False)
    reloaded = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert reloaded is not None
    assert reloaded.etl_hook_enabled is False


def test_from_config_does_not_persist_on_mutation(tmp_path: Path) -> None:
    """The legacy factory wires no state_path — mutations stay in-memory.
    Lets unit tests construct caches without touching disk."""
    cfg = make_test_config()
    cache = TestGeneratorCache.from_config(cfg)
    cache.update(scope="exceptions_only")
    cache.set_etl_hook_enabled(False)
    # No sidefile written anywhere.
    assert list(tmp_path.iterdir()) == []


def test_full_round_trip_via_two_cache_instances(tmp_path: Path) -> None:
    """Mutate the first cache → close it → load a second cache from the
    same cfg + path → the second cache reflects the persisted state.
    Simulates a Studio restart."""
    cfg = make_test_config(
        test_generator=TestGeneratorConfig(scope="full"),
    )
    cfg_path = tmp_path / "config.yaml"
    cache_a = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    cache_a.update(
        scope="exceptions_only",
        seed=99,
        plants=("limit_breach",),
    )
    cache_a.update_window(start=date(2026, 2, 1), end=date(2026, 5, 14))
    cache_a.set_etl_hook_enabled(False)
    # Restart simulation — fresh cache from disk.
    cache_b = TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)
    assert cache_b.get().scope == "exceptions_only"
    assert cache_b.get().seed == 99
    assert cache_b.get().plants == ("limit_breach",)
    assert cache_b.get_window() == DateInterval.closed(
        date(2026, 2, 1), date(2026, 5, 14),
    )
    assert cache_b.is_etl_hook_enabled() is False


# ---------------------------------------------------------------------------
# BD.5 — get_frame() bundles (up_to, window, seed) into one AsOfFrame.
# ---------------------------------------------------------------------------


def test_get_frame_bundles_up_to_window_and_seed() -> None:
    """Frame aggregates the three temporal-and-determinism pieces
    the trainer mutates independently. ``as_of`` is the scrub head
    (`get_up_to()`); ``window`` is the scenario window; ``seed`` is
    the cached generator seed."""
    from recon_gen.common.as_of_frame import AsOfFrame
    state = TestGeneratorConfig(end_date=date(2026, 3, 15), seed=42)
    window = DateInterval.closed(date(2026, 1, 1), date(2026, 4, 1))
    cache = TestGeneratorCache(state, window=window)
    frame = cache.get_frame()
    assert frame == AsOfFrame(
        as_of=date(2026, 3, 15),
        window=window,
        seed=42,
    )


def test_get_frame_falls_back_to_window_end_when_end_date_unset() -> None:
    """When the trainer hasn't pinned an end_date, the frame's
    `as_of` resolves to `window.end` — mirrors `get_up_to()`'s
    fallback (the trainer's intent: "render up through the right
    edge of my scenario window")."""
    state = TestGeneratorConfig(end_date=None, seed=None)
    window = DateInterval.closed(date(2026, 1, 1), date(2026, 4, 1))
    cache = TestGeneratorCache(state, window=window)
    frame = cache.get_frame()
    assert frame.as_of == date(2026, 4, 1)  # = window.end
    assert frame.seed is None


def test_get_frame_reflects_subsequent_mutations() -> None:
    """Frame is derived, not snapshotted — each call reads current
    state. Mutations through `update_window` / `update(seed=...)` /
    `update(end_date=...)` all surface in the next `get_frame()`."""
    state = TestGeneratorConfig(end_date=date(2026, 1, 1), seed=7)
    initial = DateInterval.closed(date(2026, 1, 1), date(2026, 2, 1))
    cache = TestGeneratorCache(state, window=initial)
    assert cache.get_frame().seed == 7
    cache.update(seed=99)
    cache.update(end_date=date(2026, 1, 15))
    cache.update_window(end=date(2026, 3, 1))
    frame = cache.get_frame()
    assert frame.as_of == date(2026, 1, 15)
    assert frame.window.end == date(2026, 3, 1)
    assert frame.seed == 99
