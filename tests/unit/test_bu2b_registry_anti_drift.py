"""BU.2b — Lock 9 anti-drift tests parameterized over PLANT_REGISTRY.

The registry IS the source of truth for the Trainer surface; every
test here walks the full registry so adding a new entry exercises the
same set of invariants without per-entry test code. Lock 9's five
tests (bijectivity, tour-URL liveness, plant→matview round-trip,
primitive-kwarg coverage, docs-freshness byte-identity):

- #1 bijectivity — covered here (section_resolves + section_uniqueness)
- #2 tour-URL liveness — covered here (tour_url_well_formed)
- #3 plant → matview round-trip — covered for phantom_rail in
  tests/unit/test_bu1_plant_registry_slice.py; per-kind expansion
  lands as each emitter gains DB-layer fixtures.
- #4 primitive-kwarg coverage — covered here (primitive_kwargs_match)
- #5 docs-freshness byte-identity — lands with BU.5 docs export.
"""

from __future__ import annotations

import inspect

import pytest

from recon_gen.common.html._studio_training_v2 import resolve_section
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantKindEntry,
)


# Use `id=...` on parametrize so a failure surfaces the offending kind
# clearly in the pytest report — no "[0]" / "[1]" indices to decode.
# pytest.param() is untyped; cast through `object` keeps strict-pyright happy.
_REGISTRY_PARAMS: list[object] = [
    pytest.param(entry, id=entry.kind) for entry in PLANT_REGISTRY
]


@pytest.mark.parametrize("entry", _REGISTRY_PARAMS)  # typing-smell: ignore[no-inline-production-constants]: 'entry' is the pytest parametrize fixture name, not the `_ROW_ID_COLUMN` value
def test_section_resolves(entry: PlantKindEntry) -> None:
    """Lock 9 #1 — every registry entry must resolve to a typed section.
    KeyError here means the entry references a section_kind the handbook
    parser doesn't carry."""
    section = resolve_section(entry)
    assert section.title, (
        f"entry {entry.kind!r} resolved to a section with empty title — "
        f"the typed source likely missed a **What to do:** line."
    )


@pytest.mark.parametrize("entry", _REGISTRY_PARAMS)  # typing-smell: ignore[no-inline-production-constants]: 'entry' is the pytest parametrize fixture name, not the `_ROW_ID_COLUMN` value
def test_tour_url_well_formed(entry: PlantKindEntry) -> None:
    """Lock 9 #2 — every entry's tour URL must be a server-relative
    path. Absolute URLs (http://...) leak the deployment shape;
    relative paths break the iframe."""
    url = entry.tour_destination.primary_url
    assert url.startswith("/"), (
        f"entry {entry.kind!r} tour_destination.primary_url={url!r} "
        f"isn't server-relative — paths must start with /."
    )
    assert "://" not in url, (
        f"entry {entry.kind!r} tour URL leaks an absolute scheme."
    )


@pytest.mark.parametrize("entry", _REGISTRY_PARAMS)  # typing-smell: ignore[no-inline-production-constants]: 'entry' is the pytest parametrize fixture name, not the `_ROW_ID_COLUMN` value
def test_primitive_kwargs_match_plant_function_signature(
    entry: PlantKindEntry,
) -> None:
    """Lock 9 #4 — every primitive's ``name`` must correspond to a
    kwarg the plant_function actually accepts. A drift here (rename
    the primitive, forget to update the adapter) breaks the POST
    silently — the form submits a key the function ignores."""
    sig = inspect.signature(entry.plant_function)
    fn_kwargs = set(sig.parameters.keys())
    for primitive in entry.primitives:
        # plant_function may take **kwargs (Callable[..., str]); when
        # so, the explicit param set is empty. Skip the check for
        # those — runtime is the only gate.
        if any(
            p.kind is inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        ):
            return
        assert primitive.name in fn_kwargs, (
            f"primitive {primitive.name!r} on entry {entry.kind!r} "
            f"isn't a kwarg the plant_function accepts. The form will "
            f"POST it and the adapter will ignore it. Fix: rename the "
            f"primitive OR add the kwarg to the adapter."
        )


def test_no_two_entries_share_a_kind() -> None:
    """Lock 9 #1 (uniqueness side) — registry kinds are URL slugs;
    duplicates would make /training/plant/<kind> ambiguous."""
    kinds = [e.kind for e in PLANT_REGISTRY]
    assert len(kinds) == len(set(kinds)), (
        f"duplicate plant kind: {[k for k in kinds if kinds.count(k) > 1]!r}"
    )


def test_dashboard_check_carries_one_shape() -> None:
    """`DashboardCheck` must be either matview-based OR url-based,
    never both, never neither — the parameterized e2e branches on it."""
    for entry in PLANT_REGISTRY:
        check = entry.dashboard_check
        has_matview = check.matview_name is not None
        has_url = check.url_path is not None
        assert has_matview ^ has_url, (
            f"entry {entry.kind!r} dashboard_check must set exactly one "
            f"of matview_name / url_path (currently matview={has_matview}, "
            f"url={has_url})."
        )
