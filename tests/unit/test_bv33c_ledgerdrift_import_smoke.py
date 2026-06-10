"""BV.3.3.c anti-drift — every PLANT_REGISTRY entry's ``plant_function``
must be importable end-to-end.

Pins the fix from BV.3.3.c.bug2: prior to this phase every
``_invoke_*_plant`` body did its own lazy ``from
recon_gen.common.l2.seed import ...``. A long-running Studio
process whose ``sys.modules['recon_gen.common.l2.seed']`` pre-dated
a new dataclass (e.g. ``LedgerDriftPlant`` post-BV.3.3.c.bug1)
only blew up on the operator's FIRST click — surfaced as
``ImportError: cannot import name 'LedgerDriftPlant' from
'recon_gen.common.l2.seed'`` in the trainer-card tooltip
(``docs/audits/_archive/bv_cold_read_v3.md`` §P1.1).

Hoisting those imports to module-level made the failure mode
process-boot rather than first-click. This test pins the hoist:
if a future refactor reverts to lazy imports AND the seed module
ships without a required name, this test fails at unit-test time,
not in the operator's morning cold-read.

The test imports ``plant_registry`` once (which forces all
module-level imports to resolve) then walks the registry asserting
each ``plant_function`` is a real callable from this codebase.
That's the "if you can collect this module, the registry surface
is callable" gate.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantKindEntry,
)


# Surface the failing kind by name, not by index — mirrors the
# convention in tests/unit/test_bu2b_registry_anti_drift.py.
_REGISTRY_PARAMS: list[object] = [
    pytest.param(entry, id=entry.kind) for entry in PLANT_REGISTRY
]


def test_registry_imports_cleanly_at_module_level() -> None:
    """The act of ``from recon_gen.common.l2.plant_registry import
    PLANT_REGISTRY`` at the top of this file must not raise. If any
    of plant_registry's module-level imports (notably
    ``from recon_gen.common.l2.seed import LedgerDriftPlant, …``)
    can't resolve, pytest collection of THIS file fails — which is
    the desired "loud at boot" diagnostic.

    The body is a tautology: by the time this function executes,
    the import already succeeded. The point is the collection gate.
    """
    assert len(PLANT_REGISTRY) >= 26, (
        f"PLANT_REGISTRY shrunk unexpectedly: got {len(PLANT_REGISTRY)} "
        f"entries, expected ≥26 (the BV.3.3.c surface). If you "
        f"deliberately retired a kind, bump this floor."
    )


@pytest.mark.parametrize("entry", _REGISTRY_PARAMS)  # typing-smell: ignore[no-inline-production-constants]: 'entry' is the pytest parametrize fixture name, not the `_ROW_ID_COLUMN` value
def test_plant_function_is_callable(entry: PlantKindEntry) -> None:
    """Every entry's ``plant_function`` must be a real callable from
    this codebase (not a string, not a stale reference, not None).

    Catches a class of bugs where a registry row points at a name
    that survived a rename only by living in someone's IDE
    autocomplete — running pyright on the file would catch this,
    but pyright doesn't fire in the Studio runtime path, so we
    pin it as a unit test too.
    """
    fn = entry.plant_function
    assert isinstance(fn, Callable), (
        f"{entry.kind!r}: plant_function is not callable "
        f"(type={type(fn).__name__})."
    )
    # Must be defined in OUR namespace — a leftover ``Callable[..., str]``
    # placeholder from some refactor would fail here.
    module = getattr(fn, "__module__", "")
    assert module.startswith("recon_gen."), (
        f"{entry.kind!r}: plant_function lives in {module!r} (not "
        f"under recon_gen.*) — likely a stale import."
    )


@pytest.mark.parametrize("entry", _REGISTRY_PARAMS)  # typing-smell: ignore[no-inline-production-constants]: 'entry' is the pytest parametrize fixture name, not the `_ROW_ID_COLUMN` value
def test_plant_function_references_resolved_seed_classes(
    entry: PlantKindEntry,
) -> None:
    """For every entry, the function object's ``__globals__`` must
    contain the seed-module classes the function will reach for.

    This is the direct anti-drift test for BV.3.3.c.bug2: the
    `_invoke_ledger_drift_plant` function (and all sibling
    invokers) reaches for ``LedgerDriftPlant`` / ``OverdraftPlant``
    / etc. via the plant_registry module's globals (post-hoist).
    If that hoist is reverted AND the seed module loses a name,
    the function compiles fine but explodes at call time. By
    checking globals at parametrize time we surface a missing
    name at unit-test time, well before any operator click.

    A spot-check approach: confirm ``ScenarioPlant`` (used by every
    invoker) resolves from the function's globals. ``ScenarioPlant``
    going missing would have been the canary for the original bug.
    """
    fn = entry.plant_function
    fn_globals = getattr(fn, "__globals__", {})
    assert "ScenarioPlant" in fn_globals, (
        f"{entry.kind!r}: ScenarioPlant not in plant_function's "
        f"__globals__ — module-level hoist regressed and lazy "
        f"imports came back. See BV.3.3.c.bug2 in "
        f"plant_registry.py for the rationale."
    )
    # The actual symbol must be the real class, not a forward-ref
    # stub or None.
    sp = fn_globals["ScenarioPlant"]
    assert sp is not None and hasattr(sp, "__dataclass_fields__"), (
        f"{entry.kind!r}: ScenarioPlant in globals isn't a real "
        f"dataclass (got {type(sp).__name__})."
    )
