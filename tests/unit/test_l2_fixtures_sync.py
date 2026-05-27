"""Bundled L2 fixtures must stay byte-identical to ``tests/l2/`` source.

The package ships ``src/recon_gen/_l2_fixtures/spec_example.yaml``
and ``sasquatch_pr.yaml`` so ``docs apply`` works from an installed
wheel without an operator's ``tests/`` checkout. ``tests/l2/`` remains
the source of truth (referenced by unit tests, harness, integration);
this test guards the copies from drifting.

If this test fails, copy the updated ``tests/l2/<name>.yaml`` to
``src/recon_gen/_l2_fixtures/<name>.yaml`` (the sync direction
is always tests/l2/ → bundled, never the reverse).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_L2_DIR = _REPO_ROOT / "tests" / "l2"
_BUNDLED_L2_DIR = _REPO_ROOT / "src" / "recon_gen" / "_l2_fixtures"
_DOCS_FIXTURES_DIR = (
    _REPO_ROOT / "src" / "recon_gen" / "docs" / "reference" / "fixtures"
)


@pytest.mark.parametrize("name", [DEFAULT_PREFIX, "sasquatch_pr"])
def test_bundled_l2_fixture_matches_tests_l2_source(name: str) -> None:
    src = _TESTS_L2_DIR / f"{name}.yaml"
    bundled = _BUNDLED_L2_DIR / f"{name}.yaml"
    assert src.read_bytes() == bundled.read_bytes(), (
        f"{bundled} drifted from {src}. Re-sync: "
        f"`cp {src} {bundled}`."
    )


def test_docs_reference_fixture_matches_tests_l2_source() -> None:
    """AJ.4a — the handbook's downloadable reference copy of
    ``spec_example.yaml`` (linked from ``integrator.md``) must not drift
    from the ``tests/l2/`` source of truth.

    This guards the LAST remaining packaged copy after AJ.4a collapsed
    the 4-copy sprawl: ``apps/l1_dashboard/_default_l2.yaml`` was deleted
    (all ``src/`` code now loads the single ``_l2_fixtures`` copy via
    ``common.l2.default_l2_instance``); only the docs-served reference
    file lives apart, because mkdocs links to it from inside ``docs/``.
    Only ``spec_example`` ships as a docs reference — the persona demo
    ``sasquatch_pr`` doesn't.
    """
    src = _TESTS_L2_DIR / "spec_example.yaml"
    docs_copy = _DOCS_FIXTURES_DIR / "spec_example.yaml"
    assert src.read_bytes() == docs_copy.read_bytes(), (
        f"{docs_copy} drifted from {src}. Re-sync: "
        f"`cp {src} {docs_copy}`."
    )
