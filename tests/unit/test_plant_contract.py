"""BV.3.3 snapshot — PlantContract typed invariant tests.

The trainer-dogfood snapshot/restore lifecycle (BV.3.3) takes a single
golden snapshot of the v-overlay before any plants fire, then restores
to that snapshot between each plant Apply. The lifecycle holds only
when every plant_function constrains its mutations to v-overlay tables
(``{prefix}_transactions`` / ``{prefix}_daily_balances`` /
``{prefix}_config_kv`` / refreshed matviews). A plant that emits DDL or
hardcodes the base prefix would invalidate the snapshot mid-loop.

``PlantContract.mutates='v_overlay_only'`` is the typed declaration of
that invariant. Every PLANT_REGISTRY entry carries a contract; a
construction-time guard on ``PlantContract`` rejects bogus values; the
``apply_plants`` caller refuses to invoke a plant whose contract sits
outside its supported set.

Tests:
1. Every PLANT_REGISTRY entry has a PlantContract.
2. Every entry's contract.mutates is in the allowed set.
3. PlantContract construction rejects an out-of-set value.
4. apply_plants raises when handed a plant whose contract is
   unsupported (simulates a future widening that didn't update the
   snapshot caller).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.plant_registry import (
    DEFAULT_PLANT_CONTRACT,
    PLANT_REGISTRY,
    PlantContract,
    PlantKindEntry,
    _ALLOWED_MUTATION_SURFACES,
)
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.l2.seed import emit_full_seed
from recon_gen.common.l2.v_overlay import (
    apply_plants,
    clone_base_to_v_sql,
    create_v_overlay_sql,
    refresh_v_overlay_matviews_sql,
)
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


# -- Static invariants over PLANT_REGISTRY ---------------------------------


def test_every_registry_entry_carries_a_contract() -> None:
    """No registry entry should land without an explicit contract — the
    default lives at module scope (``DEFAULT_PLANT_CONTRACT``) so the
    dataclass default fills in for unannotated rows, but the contract
    attribute itself must exist on every entry."""
    assert PLANT_REGISTRY, "registry is empty; BV.3.3 contract gate has nothing to assert against"
    for entry in PLANT_REGISTRY:
        assert hasattr(entry, "contract"), (
            f"PLANT_REGISTRY entry {entry.kind!r} missing contract field"
        )
        assert isinstance(entry.contract, PlantContract), (
            f"PLANT_REGISTRY entry {entry.kind!r} has "
            f"contract={entry.contract!r} (expected PlantContract)"
        )


def test_every_registry_contract_is_in_allowed_set() -> None:
    """BV.3.3 — every audited plant_function only mutates v-overlay
    tables; the contract must say so. A future entry that widens its
    mutation surface needs an operator review + a snapshot-lifecycle
    update before this assertion can be relaxed."""
    for entry in PLANT_REGISTRY:
        assert entry.contract.mutates in _ALLOWED_MUTATION_SURFACES, (
            f"PLANT_REGISTRY entry {entry.kind!r} declares "
            f"contract.mutates={entry.contract.mutates!r} which is "
            f"outside the BV.3.3 snapshot lifecycle's supported set "
            f"{sorted(_ALLOWED_MUTATION_SURFACES)}."
        )


def test_default_plant_contract_is_v_overlay_only() -> None:
    """Anti-drift — if someone weakens DEFAULT_PLANT_CONTRACT to a
    wider mutation surface, every defaulting registry entry silently
    inherits it. Pin the default value so a widening is loud."""
    assert DEFAULT_PLANT_CONTRACT.mutates == "v_overlay_only"


# -- PlantContract construction guards -------------------------------------


def test_plant_contract_construction_rejects_unknown_mutates() -> None:
    """The dataclass's ``__post_init__`` rejects an out-of-set value at
    construction time, not at apply time. This means a typo in a
    future registry entry fails at import (the registry module
    evaluates ``PlantContract(...)`` calls at import) rather than the
    first time the trainer tries to plant that kind."""
    with pytest.raises(ValueError, match="PlantContract.mutates"):
        PlantContract(mutates="base_prefix_too")  # pyright: ignore[reportArgumentType]  # intentional violation tests


def test_plant_contract_default_constructor_succeeds() -> None:
    """Default arg path — caller writes ``PlantContract()`` and gets
    the v_overlay_only invariant for free."""
    c = PlantContract()
    assert c.mutates == "v_overlay_only"


# -- apply_plants contract gate (e2e against a v overlay) -------------------


@pytest.fixture
def fresh_v_overlay(tmp_path: Path) -> Iterator[tuple[Config, Path]]:
    """Mirror of test_bv49_diff_apply::fresh_v_overlay — spins up a real
    DuckDB-backed v overlay so we can confirm the runtime contract
    gate fires on a widened plant before the v overlay gets touched."""
    db_path = tmp_path / "plant_contract.duckdb"
    cfg = make_test_config(
        dialect=Dialect.DUCKDB,
        demo_database_url=str(db_path),
        db_table_prefix="plant_contract",
    )

    fixtures_root = Path(__file__).resolve().parent.parent / "l2"
    yaml_path = fixtures_root / "spec_example.yaml"
    cache = L2InstanceCache.from_path(yaml_path)
    instance = cache.get()

    base_prefix = cfg.db_table_prefix
    scenarios = default_scenario_for(instance).scenario
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(
                cur, emit_schema(instance, prefix=base_prefix, dialect=cfg.dialect),
                dialect=cfg.dialect,
            )
            execute_script(
                cur,
                emit_full_seed(
                    instance, scenarios,
                    prefix=base_prefix, dialect=cfg.dialect,
                ),
                dialect=cfg.dialect,
            )
            execute_script(
                cur,
                create_v_overlay_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                ),
                dialect=cfg.dialect,
            )
            execute_script(
                cur, clone_base_to_v_sql(base_prefix), dialect=cfg.dialect,
            )
            execute_script(
                cur,
                refresh_v_overlay_matviews_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                ),
                dialect=cfg.dialect,
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()

    yield cfg, yaml_path


def _phantom_rail_entry() -> PlantKindEntry:
    return next(e for e in PLANT_REGISTRY if e.kind == "phantom_rail")


def test_apply_plants_rejects_widened_contract(
    fresh_v_overlay: tuple[Config, Path],
) -> None:
    """If a future entry declares a wider mutation surface than
    apply_plants supports, the contract gate fails LOUDLY before the
    plant_function runs — protecting the BV.3.3 snapshot lifecycle
    from a silent base-prefix write."""
    cfg, yaml_path = fresh_v_overlay
    cache = L2InstanceCache.from_path(yaml_path)
    instance = cache.get()
    entry = _phantom_rail_entry()

    # Bypass PlantContract.__post_init__ — we're simulating a hypothetical
    # future widening that landed without operator review. Use
    # object.__setattr__ to skirt the dataclass frozen=True guard
    # (a forced violation; production code never does this).
    widened = PlantContract.__new__(PlantContract)
    object.__setattr__(widened, "mutates", "base_prefix_too")
    bad_entry = replace(entry, contract=widened)

    with pytest.raises(RuntimeError, match="contract.mutates"):
        asyncio.run(
            apply_plants(
                cfg, instance,
                enabled_plants=[(bad_entry, {"count": 1, "rail_name": "x"})],
            )
        )


def test_apply_plants_accepts_v_overlay_only_contract(
    fresh_v_overlay: tuple[Config, Path],
) -> None:
    """Sanity check — the default contract round-trips through
    apply_plants without firing the gate. Without this we couldn't tell
    "gate is broken open" from "gate is correctly closed"."""
    cfg, yaml_path = fresh_v_overlay
    cache = L2InstanceCache.from_path(yaml_path)
    instance = cache.get()
    entry = _phantom_rail_entry()

    # The entry's default contract is v_overlay_only; this should just work.
    assert entry.contract.mutates == "v_overlay_only"
    asyncio.run(
        apply_plants(
            cfg, instance,
            enabled_plants=[(entry, {"count": 2, "rail_name": "x"})],
        )
    )


