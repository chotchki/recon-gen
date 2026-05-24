"""AI.2.d.1 — StudioHttpEditorDriver rebuild round-trip (in-process).

Drives the Studio editor over a Starlette ``TestClient`` (no browser, no
real server) to recreate ``spec_example`` from an empty L2 in dependency
order, then asserts the saved YAML loads back structurally equal to the
reference. This is the AI.4 structural-equivalence gate for the HTTP
transport, pulled into the fast unit layer so the editor's round-trip
fidelity is guarded on every push (the browser/Playwright pass in AI.3
covers form render+submit on top of this).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from tests.e2e._drivers.studio_editor import (
    StudioHttpEditorDriver,
    build_editor_app,
)

TestClient = pytest.importorskip("starlette.testclient").TestClient

_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


def _empty_l2() -> L2Instance:
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def _rebuild_via_http(reference_path: Path, dest: Path) -> L2Instance:
    """Drive the HTTP editor driver to rebuild the reference L2 at dest."""
    reference = load_instance(reference_path)
    cache = L2InstanceCache(dest, _empty_l2())
    app = build_editor_app(cache)
    with TestClient(app) as client:
        driver = StudioHttpEditorDriver(client, dest)
        driver.create_l2(reference)
        driver.save_l2_to_path(dest)
    return load_instance(dest)


def _by_identifier(entities: tuple[object, ...], key: str) -> list[object]:
    """Sort a collection by the named identifier field for order-
    insensitive structural comparison. The dogfood's claim is the
    editor preserves the L2 entities + their fields; collection
    tuple ORDER isn't semantically meaningful for L2 validation,
    so we compare as identifier-sorted lists.

    BB.3 driver creates reconciler entities (TTs / aggregating
    Rails) at their first-occupant rail's position rather than
    their yaml-declared position, producing a different tuple order
    in the rebuilt instance — semantically equivalent, structurally
    differently-ordered.

    Description fields are normalized (trailing whitespace stripped)
    so yaml-block-style newline drift doesn't fail the struct
    comparison — formatting differences aren't structural.
    """
    import dataclasses as _dc
    normed: list[object] = []
    for e in entities:
        if not _dc.is_dataclass(e):
            normed.append(e)
            continue
        desc = getattr(e, "description", None)
        if isinstance(desc, str):
            stripped = desc.rstrip()
            if stripped != desc:
                e = _dc.replace(e, description=stripped)
        normed.append(e)
    return sorted(normed, key=lambda x: str(getattr(x, key)))


def _normalize_descriptions(entities: tuple[object, ...]) -> list[object]:
    """Apply description-trailing-whitespace normalization (same as
    _by_identifier) for collections compared as dicts."""
    import dataclasses as _dc
    out: list[object] = []
    for e in entities:
        if _dc.is_dataclass(e):
            desc = getattr(e, "description", None)
            if isinstance(desc, str):
                stripped = desc.rstrip()
                if stripped != desc:
                    e = _dc.replace(e, description=stripped)
        out.append(e)
    return out


def test_http_driver_rebuilds_spec_example_structurally(
    tmp_path: Path,
) -> None:
    """The editor, driven verb-by-verb in dependency order, recreates
    every spec_example entity + both top-level fields with zero
    structural drift — the dogfood's core claim, HTTP transport."""
    reference = load_instance(_FIXTURES / "spec_example.yaml")
    dest = tmp_path / "dogfood_spec_example.yaml"
    rebuilt = _rebuild_via_http(_FIXTURES / "spec_example.yaml", dest)

    assert _by_identifier(rebuilt.account_templates, "role") == _by_identifier(
        reference.account_templates, "role",
    )
    assert _by_identifier(rebuilt.accounts, "id") == _by_identifier(
        reference.accounts, "id",
    )
    assert _by_identifier(rebuilt.rails, "name") == _by_identifier(
        reference.rails, "name",
    )
    assert _by_identifier(rebuilt.transfer_templates, "name") == _by_identifier(
        reference.transfer_templates, "name",
    )
    # Chains have no single identifier; compare as parent-keyed dicts.
    rebuilt_chains_by_parent = {
        str(c.parent): c for c in _normalize_descriptions(rebuilt.chains)
    }
    reference_chains_by_parent = {
        str(c.parent): c for c in _normalize_descriptions(reference.chains)
    }
    assert rebuilt_chains_by_parent == reference_chains_by_parent
    # LimitSchedules have no single identifier; use the composite key.
    def _ls_key(ls: object) -> str:
        return f"{getattr(ls, 'parent_role')!s}::{getattr(ls, 'rail')!s}"
    rebuilt_ls = {
        _ls_key(ls): ls
        for ls in _normalize_descriptions(rebuilt.limit_schedules)
    }
    reference_ls = {
        _ls_key(ls): ls
        for ls in _normalize_descriptions(reference.limit_schedules)
    }
    assert rebuilt_ls == reference_ls
    assert rebuilt.role_business_day_offsets == (
        reference.role_business_day_offsets
    )
    assert rebuilt.description == reference.description
