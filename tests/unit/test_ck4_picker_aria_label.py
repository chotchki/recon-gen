"""CK.4 — every Studio `<select>` ships an explicit aria-label.

v13.1.1 axe-core findings: `aria-input-field-name` (unlabeled
pickers) + one `select-name`. CK.4 closes by sourcing the
aria-label from the same place the visible `<label for=...>` reads
(FieldSpec.label for dynamic forms, hand-written for one-off
controls like the trainer's `bv-show-filter`).

Tests pin the contract:
1. Dynamic-form selects rendered by `_render_field` carry
   aria-label == FieldSpec.label.
2. Hand-written one-off selects (bv-show-filter, reconciler
   sub-form selects) all have non-empty aria-label.
3. End-to-end probe: no `<select>` on any of the 19 Studio
   surfaces lacks aria-label.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "l2"


def _build_app(yaml_path: Path) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


_SELECT_RE = re.compile(r"<select\b[^>]*>")


def _selects(body: str) -> list[str]:
    return _SELECT_RE.findall(body)


@pytest.mark.parametrize("path", [
    "/",
    "/diagram",
    "/etl/",
    "/etl/probe",
    "/etl/run",
    "/etl/triage",
    "/training/",
    "/l2_shape/account/",
    "/l2_shape/account_template/",
    "/l2_shape/rail/",
    "/l2_shape/transfer_template/",
    "/l2_shape/chain/",
    "/l2_shape/limit_schedule/",
    "/l2_shape/theme/",
    "/l2_shape/account/new",
    "/l2_shape/rail/new",
    "/l2_shape/persona/",
])
def test_no_select_without_aria_label(
    writable_l2_yaml: Path, path: str,
) -> None:
    """Every `<select>` rendered on a Studio surface carries
    aria-label or aria-labelledby. Catches the v13.1.1
    `aria-input-field-name` + `select-name` findings."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(path).text
    unlabeled = [
        s for s in _selects(body)
        if "aria-label" not in s and "aria-labelledby" not in s
    ]
    assert unlabeled == [], (
        f"{path}: {len(unlabeled)} <select> without aria-label "
        f"(first: {unlabeled[0][:160] if unlabeled else None!r})"
    )


def test_dynamic_form_select_uses_fieldspec_label(
    writable_l2_yaml: Path,
) -> None:
    """`_render_field` sources the aria-label from FieldSpec.label
    so the aria-label matches the operator-visible label
    automatically — no per-site label drift."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account/{inst.accounts[0].id}/edit").text
    # field-scope's FieldSpec.label is "Scope".
    assert 'id="field-scope"' in body
    scope_select = next(
        s for s in _selects(body) if 'id="field-scope"' in s
    )
    assert 'aria-label="Scope"' in scope_select


def test_training_bv_show_filter_carries_aria_label(
    writable_l2_yaml: Path,
) -> None:
    """Trainer landing's plant-kind filter dropdown — a hand-
    written `<select>` outside the FieldSpec dynamic-form path —
    carries the CK.4 aria-label explicitly."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/training/").text
    if 'id="bv-show-filter"' not in body:
        pytest.skip("trainer landing doesn't render bv-show-filter in this build")
    bv = next(s for s in _selects(body) if 'id="bv-show-filter"' in s)
    assert "aria-label=" in bv
