"""CG.9 — section summary carries the same `▸ → 90°` rotating
chevron as the card-level summaries (CF.4.l).

Cold-read v3 P1: cards INSIDE a section had the explicit chevron,
but the SECTION `<details>` summary itself relied on the browser-
default triangle (suppressed by some Tailwind resets). Two open/
close affordances on the same page broke the visual contract.

This cell adds the same `inline-block transition-transform
group-open:rotate-90` chevron to the section summary's left edge.
`<details>` carries the `group` class so the chevron can read the
parent's `[open]` state; native browser marker suppressed via
`list-none [&::-webkit-details-marker]:hidden` so we control the
glyph.
"""

from __future__ import annotations

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


def _section_summary(body: str, kind: str) -> str:
    marker = f'data-kind="{kind}"'
    idx = body.index(marker)
    summary_start = body.index("<summary", idx)
    summary_end = body.index("</summary>", summary_start) + len("</summary>")
    return body[summary_start:summary_end]


def _section_details_open_tag(body: str, kind: str) -> str:
    """Slice the `<details ...>` opening tag for a kind so we can
    pin classes on the parent details element itself."""
    marker = f'data-kind="{kind}"'
    idx = body.index(marker)
    tag_start = body.rfind("<details", 0, idx)
    tag_end = body.index(">", idx) + 1
    return body[tag_start:tag_end]


# ---------------------------------------------------------------------------
# Every list-kind section carries the chevron
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [
    "account", "account_template", "rail",
    "transfer_template", "chain", "limit_schedule",
])
def test_list_section_summary_has_chevron(
    writable_l2_yaml: Path, kind: str,
) -> None:
    """Each list-kind section emits a `data-role="section-chevron"`
    span at the left edge of its `<summary>`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    summary = _section_summary(body, kind)
    assert 'data-role="section-chevron"' in summary
    assert "▸" in summary
    # Rotation class lives on the chevron.
    assert "group-open:rotate-90" in summary


@pytest.mark.parametrize("kind", ["theme", "instance"])
def test_singleton_section_summary_has_chevron(
    writable_l2_yaml: Path, kind: str,
) -> None:
    """Singleton sections (Theme / Instance) carry the same chevron
    so the home accordion reads consistently."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    summary = _section_summary(body, kind)
    assert 'data-role="section-chevron"' in summary
    assert "▸" in summary


# ---------------------------------------------------------------------------
# `group` on <details> + native marker suppressed
# ---------------------------------------------------------------------------

def test_details_carries_group_class_so_chevron_can_read_open(
    writable_l2_yaml: Path,
) -> None:
    """The rotating `group-open:rotate-90` variant only fires when
    the parent `<details>` has the `group` class AND is open.
    Without `group`, Tailwind looks no higher than the chevron's own
    element. Pin so a future markup edit doesn't silently break the
    rotation."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    for kind in (
        "account", "rail", "transfer_template", "chain", "theme",
    ):
        details_tag = _section_details_open_tag(body, kind)
        # `class="group ..."` — pin the token specifically, not just
        # the substring (which `class="group-open:..."` would match).
        assert ' class="group ' in details_tag, (
            f"{kind} section <details> missing the `group` class; "
            f"`group-open:rotate-90` on the chevron won't fire"
        )


def test_section_summary_suppresses_native_marker(
    writable_l2_yaml: Path,
) -> None:
    """We render our own chevron, so the browser-default triangle
    must be hidden via `list-none` + the webkit attr selector.
    Otherwise the operator sees two glyphs side by side (or worse,
    one centered vertically and the other not)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    summary = _section_summary(body, "rail")
    assert "list-none" in summary
    assert "[&::-webkit-details-marker]:hidden" in summary
