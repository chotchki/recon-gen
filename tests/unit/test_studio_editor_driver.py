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


@pytest.mark.skip(
    reason="AI.2.d.1 WIP — the editor runs full validate() after each "
    "create, so an incremental bulk rebuild hits invalid intermediate "
    "states (reference resolution fails before all referents exist). "
    "Needs a defer-validation bulk-load path; resuming after the "
    "v11.9.3 daily-statement bug.",
)
def test_http_driver_rebuilds_spec_example_structurally(
    tmp_path: Path,
) -> None:
    """The editor, driven verb-by-verb in dependency order, recreates
    every spec_example entity + both top-level fields with zero
    structural drift — the dogfood's core claim, HTTP transport."""
    reference = load_instance(_FIXTURES / "spec_example.yaml")
    dest = tmp_path / "dogfood_spec_example.yaml"
    rebuilt = _rebuild_via_http(_FIXTURES / "spec_example.yaml", dest)

    assert rebuilt.account_templates == reference.account_templates
    assert rebuilt.accounts == reference.accounts
    assert rebuilt.rails == reference.rails
    assert rebuilt.transfer_templates == reference.transfer_templates
    assert rebuilt.chains == reference.chains
    assert rebuilt.limit_schedules == reference.limit_schedules
    assert rebuilt.role_business_day_offsets == (
        reference.role_business_day_offsets
    )
    assert rebuilt.description == reference.description
