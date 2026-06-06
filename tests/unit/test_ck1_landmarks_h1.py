"""CK.1 — every Studio surface ships exactly one `<main>` landmark
and exactly one `<h1>` element.

The v13.1.1 design review flagged "no landmarks / `<main>`
app-wide + missing `<h1>` on several studio pages" as the audit's
#7 highest-leverage systemic fix. CK.1 closed that on the 6 pages
the static probe surfaced: home page (added `<main>` over the
existing h1), diagram (sr-only h1 + main; the CG.10 vertical-
budget exemption stands for the visible trainer-style header), and
the three ETL sub-pages (added the standard trainer-style header
strip with h1 + wrapped content in `<main>`).

This test pins the contract per page. Pages that already had both
landmarks (CG.6 list pages, CG.20 unknown-kind chrome, training,
data) are guarded too so a future markup edit can't quietly drop
them.
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


def _strip_comments(body: str) -> str:
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)


_MAIN_RE = re.compile(r"<main\b")
_H1_RE = re.compile(r"<h1\b")


@pytest.mark.parametrize("path", [
    "/",
    "/diagram",
    "/etl/",
    "/etl/probe",
    "/etl/run",
    "/etl/triage",
    "/l2_shape/account/",
    "/l2_shape/account_template/",
    "/l2_shape/rail/",
    "/l2_shape/transfer_template/",
    "/l2_shape/chain/",
    "/l2_shape/limit_schedule/",
    "/l2_shape/theme/",
    "/l2_shape/account/new",
    "/l2_shape/rail/new",
    "/l2_shape/account/cust-001/edit",  # 404, exercises CG.20 chrome via 4xx
    "/l2_shape/persona/",
])
def test_page_has_exactly_one_main(
    writable_l2_yaml: Path, path: str,
) -> None:
    """Every Studio page emits exactly one `<main>` landmark. Pages
    that 404 (cust-001 doesn't exist in spec_example, persona is a
    deliberately-rejected kind) go through the CG.20 unknown-kind
    chrome which also wraps content in `<main>`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = _strip_comments(c.get(path).text)
    n = len(_MAIN_RE.findall(body))
    assert n == 1, f"{path}: expected 1 <main> landmark, got {n}"


@pytest.mark.parametrize("path", [
    "/",
    "/diagram",
    "/etl/",
    "/etl/probe",
    "/etl/run",
    "/etl/triage",
    "/l2_shape/account/",
    "/l2_shape/rail/",
    "/l2_shape/chain/",
    "/l2_shape/account/new",
    "/l2_shape/rail/new",
    "/l2_shape/theme/",
    "/l2_shape/persona/",
])
def test_page_has_exactly_one_h1(
    writable_l2_yaml: Path, path: str,
) -> None:
    """Every Studio page emits exactly one `<h1>` element."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = _strip_comments(c.get(path).text)
    n = len(_H1_RE.findall(body))
    assert n == 1, f"{path}: expected 1 <h1> element, got {n}"


def test_diagram_h1_uses_sr_only(writable_l2_yaml: Path) -> None:
    """The diagram page's h1 is `sr-only` (visually hidden) so the
    canvas keeps its vertical budget. CG.10 documents the
    no-header-strip exemption; CK.1's h1 lives in markup for screen
    readers only."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = _strip_comments(c.get("/diagram").text)
    match = re.search(r"<h1\b[^>]*>([^<]+)</h1>", body)
    assert match is not None, "diagram page should have an h1"
    h1_tag = body[match.start():match.start() + 200]
    assert "sr-only" in h1_tag, (
        f"diagram h1 should carry sr-only class; got {h1_tag!r}"
    )
