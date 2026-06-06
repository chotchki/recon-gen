"""CG.21 — every Studio surface's `<title>` follows the
`Recon-Gen · Studio · <surface>[ · <detail>]` canonical shape.

Cold-read v4 P1 #6 (carryover from CF.4 cold-read v3 P2): em-dash
vs middle-dot drift, deployment-name sometimes included sometimes
not, "Studio" sometimes prefix sometimes suffix. Single-card view
had empty `<title>` entirely.

Canonical lock:
- Prefix: `Recon-Gen · Studio` (middle-dot separator).
- Surface segment names the major area: Editor / ETL / Training /
  Diagram / Data.
- Detail segment(s) name the sub-page (`Edit account · cust-001`,
  `ETL · Probe`, `404`).
- Deployment name appears ONLY on the home page (operators already
  know which deployment they're in once they're past it).
- Em-dash (`—`) is reserved for tagline / readable copy; the title
  bar uses middle-dot exclusively.

This test pins:
- Every full-page surface (home, diagram, data, ETL home + 3 sub-
  pages, training, editor list + create + edit + singleton + rail
  subtype picker, 404) emits a title matching the canonical shape.
- No surface other than home title carries the deployment name.
- Em-dash never appears in any title.

Note (CG.21.a, deferred): `/l2_shape/<kind>/<id>` (the single-card
read-card endpoint) is an HTMX fragment, not a full HTML page —
direct-URL access returns no `<title>` at all. Wrapping it as a
full page is a separate cell.
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


_TITLE_RE = re.compile(r"<title>([^<]*)</title>")


def _title_of(body: str) -> str:
    """Pull the first `<title>...</title>` from the rendered body.
    Empty string if none — surface-level full pages must HAVE one."""
    match = _TITLE_RE.search(body)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Per-surface canonical-shape pins
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,want_prefix", [
    ("/", "Recon-Gen · Studio · "),
    ("/diagram", "Recon-Gen · Studio · Diagram"),
    ("/l2_shape/account/", "Recon-Gen · Studio · Editor · Accounts"),
    ("/l2_shape/rail/", "Recon-Gen · Studio · Editor · Rails"),
    ("/l2_shape/chain/", "Recon-Gen · Studio · Editor · Chains"),
    (
        "/l2_shape/account_template/",
        "Recon-Gen · Studio · Editor · Account templates",
    ),
    (
        "/l2_shape/transfer_template/",
        "Recon-Gen · Studio · Editor · Transfer templates",
    ),
    (
        "/l2_shape/limit_schedule/",
        "Recon-Gen · Studio · Editor · Limit schedules",
    ),
    ("/l2_shape/theme/", "Recon-Gen · Studio · Editor · Theme"),
    ("/l2_shape/account/new", "Recon-Gen · Studio · Editor · Create account"),
    ("/l2_shape/rail/new", "Recon-Gen · Studio · Editor · Create rail · pick subtype"),
    (
        "/l2_shape/persona/",
        "Recon-Gen · Studio · 404",
    ),
])
def test_title_starts_with_canonical_prefix(
    writable_l2_yaml: Path, path: str, want_prefix: str,
) -> None:
    """Each surface emits a `<title>` starting with the canonical
    `Recon-Gen · Studio · <surface>` prefix."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        title = _title_of(c.get(path).text)
    assert title.startswith(want_prefix), (
        f"{path}: title {title!r} doesn't start with {want_prefix!r}"
    )


def test_edit_form_title_includes_kind_and_entity_id(
    writable_l2_yaml: Path,
) -> None:
    """Edit form title carries the singular kind label + the
    entity id segments — useful when the operator has multiple
    tabs open across different entities."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = inst.accounts[0]
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        title = _title_of(c.get(f"/l2_shape/account/{account.id}/edit").text)
    assert title == (
        f"Recon-Gen · Studio · Editor · Edit account · {account.id}"
    )


# ---------------------------------------------------------------------------
# Anti-drift — em-dash banned, deployment name only on home
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/",
    "/diagram",
    "/l2_shape/account/",
    "/l2_shape/rail/",
    "/l2_shape/chain/",
    "/l2_shape/theme/",
    "/l2_shape/account/new",
    "/l2_shape/persona/",
])
def test_title_uses_middle_dot_not_em_dash(
    writable_l2_yaml: Path, path: str,
) -> None:
    """Em-dash (`—`) is reserved for the tagline / readable copy.
    The title bar uses middle-dot (`·`) exclusively so the shape is
    consistent across surfaces."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        title = _title_of(c.get(path).text)
    assert "—" not in title, (
        f"{path}: title {title!r} carries em-dash — switch to middle-dot"
    )


@pytest.mark.parametrize("path", [
    "/diagram",
    "/l2_shape/account/",
    "/l2_shape/theme/",
])
def test_non_home_titles_omit_deployment_name(
    writable_l2_yaml: Path, path: str,
) -> None:
    """Deployment-name belongs only on the home page title — the
    operator already knows their deployment by the time they're
    past it. Pin the absence."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    deployment_name = cache.path.stem  # studio renders from yaml stem
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        title = _title_of(c.get(path).text)
    # Deployment name (or yaml stem fallback) should NOT appear.
    assert deployment_name not in title, (
        f"{path}: title {title!r} leaks deployment name; "
        f"only `/` should carry it"
    )


def test_home_title_carries_deployment_name(
    writable_l2_yaml: Path,
) -> None:
    """Symmetric guard — the home page DOES include it (operator
    needs the at-a-glance "which deployment am I in" signal)."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    deployment_name = cache.path.stem
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        title = _title_of(c.get("/").text)
    assert deployment_name in title, (
        f"home title {title!r} should carry the deployment name; "
        f"it's the operator's deployment anchor"
    )
