"""BTb — cold-read v3 fix pins.

Each test pins one BTb cell so the cold-read v4 sign-off has a stable
floor. Cells:

- BTb.1 — Rail subtype picker renders the back-breadcrumb when
  `?from=` is present + propagates `&from=` on the subtype links.
- BTb.2 — Editor new-form prefills the `name` field from
  `?prefill_name=…`; gap-card CTA appends `prefill_name` for
  applicable kinds.
- BTb.3 — Triage page emits a demo-plant disclosure banner when
  `cfg.etl_hook is None`.
- BTb.4 — Cancel button mid-run carries explanatory copy + tooltip.
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


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


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


# -- BTb.1 — Rail subtype picker breadcrumb + from-propagation -----------


def test_rail_subtype_picker_renders_back_breadcrumb_when_from_present(
    writable_l2_yaml: Path,
) -> None:
    """Cold-read v3 P1.1: clicking a Triage CTA for a phantom rail
    lands on `/l2_shape/rail/new` which routes to the SUBTYPE PICKER
    (not the create form). The picker page was not emitting the
    back-breadcrumb. BTb.1 wires the breadcrumb through."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(
            "/l2_shape/rail/new?from=/etl/triage",
        ).text
    assert "data-test-back-breadcrumb" in body
    assert 'href="/etl/triage"' in body
    assert "Back to Triage" in body


def test_rail_subtype_picker_propagates_from_on_subtype_links(
    writable_l2_yaml: Path,
) -> None:
    """The picker links to `?subtype=two_leg|single_leg`. Without
    `&from=` carried through, step 2 (the actual create form) would
    lose the back-breadcrumb context."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(
            "/l2_shape/rail/new?from=/etl/triage",
        ).text
    assert (
        'href="/l2_shape/rail/new?subtype=two_leg&from=/etl/triage"'
        in body
    )
    assert (
        'href="/l2_shape/rail/new?subtype=single_leg&from=/etl/triage"'
        in body
    )


def test_rail_subtype_picker_no_breadcrumb_when_from_absent(
    writable_l2_yaml: Path,
) -> None:
    """Plain /l2_shape/rail/new (no `?from=`) ⇒ no breadcrumb + plain
    subtype links."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/l2_shape/rail/new").text
    assert "data-test-back-breadcrumb" not in body
    assert 'href="/l2_shape/rail/new?subtype=two_leg"' in body
    assert "&from=" not in body


# -- BTb.2 — Name prefill end-to-end -------------------------------------


def test_editor_new_form_prefills_name_from_query_param(
    writable_l2_yaml: Path,
) -> None:
    """BTb.2: GET `/l2_shape/transfer_template/new?prefill_name=phantom_tt`
    renders the create form with `<input name="name" value="phantom_tt">`
    so the operator doesn't have to retype the offending value."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(
            "/l2_shape/transfer_template/new?from=/etl/triage&prefill_name=phantom_tt",
        ).text
    # Form ships the prefilled value in the name input.
    assert 'name="name"' in body
    assert 'value="phantom_tt"' in body


def test_editor_new_form_rail_subtype_step_prefills_name(
    writable_l2_yaml: Path,
) -> None:
    """Rail's 2-step flow: subtype picker → step 2. The prefill_name
    must survive both hops so the create form on step 2 lands with
    the name pre-filled."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        # Picker page propagates prefill_name on subtype links.
        picker_body = c.get(
            "/l2_shape/rail/new?from=/etl/triage&prefill_name=legacy_rail",
        ).text
        assert "prefill_name=legacy_rail" in picker_body
        # Step 2 with the subtype + prefill_name renders the prefilled
        # name on the form.
        step2 = c.get(
            "/l2_shape/rail/new?subtype=two_leg&from=/etl/triage&prefill_name=legacy_rail",
        ).text
        assert 'value="legacy_rail"' in step2


def test_editor_new_form_no_prefill_when_param_absent(
    writable_l2_yaml: Path,
) -> None:
    """No `prefill_name` ⇒ name input renders without an injected
    value (operator types from scratch)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/l2_shape/transfer_template/new").text
    # The form ships an empty / default name input.
    assert 'value="phantom_tt"' not in body


# -- BTb.3 — Triage demo-plant disclosure banner -------------------------


def test_triage_page_shows_demo_plant_banner_when_no_etl_hook(
    writable_l2_yaml: Path,
) -> None:
    """BTb.3: with `cfg.etl_hook is None` (bundled-demo path), the
    Triage page banner echoes the Run-page demo-overlay disclosure
    so 4,400 `Missing LimitSchedule` rows don't read as real bugs."""
    import asyncio

    from recon_gen.common.html._studio_routes import (
        _render_etl_triage_page,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    cfg = make_test_config()  # etl_hook defaults to None
    assert cfg.etl_hook is None
    body = asyncio.run(_render_etl_triage_page(
        cache, dev_log=False,
        db_pool=None, dialect=None,
        prefix_override=None, cfg=cfg,
        top_nav_html="",
    ))
    from recon_gen.common.l2.demo_etl_gaps import DEMO_GAP_ID_PREFIX

    assert "data-test-triage-demo-plant-banner" in body
    assert "Bundled-demo data" in body
    # Banner copy points operators at the demo-row prefix so they
    # can grep them out — pin against the actual constant so a
    # rename loudly fails this test.
    assert DEMO_GAP_ID_PREFIX in body


def test_triage_page_omits_demo_plant_banner_when_hook_configured(
    writable_l2_yaml: Path,
) -> None:
    """Real-hook deployments shouldn't see the demo-plant disclosure
    — their gaps ARE their gaps."""
    import asyncio
    from dataclasses import replace as dataclass_replace

    from recon_gen.common.html._studio_routes import (
        _render_etl_triage_page,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    cfg = make_test_config()
    cfg_with_hook = dataclass_replace(cfg, etl_hook="./bin/my_etl.py")
    body = asyncio.run(_render_etl_triage_page(
        cache, dev_log=False,
        db_pool=None, dialect=None,
        prefix_override=None, cfg=cfg_with_hook,
        top_nav_html="",
    ))
    assert "data-test-triage-demo-plant-banner" not in body


# -- BTb.4 — Cancel button copy + tooltip --------------------------------


def test_etl_run_cancel_button_carries_help_copy_when_running(
    writable_l2_yaml: Path,
) -> None:
    """BTb.4: cancel button now ships a `data-test-cancel-help` block
    underneath + a `title=` tooltip on the button. No modal."""
    import asyncio
    from recon_gen.common.html._studio_routes import (
        _render_etl_run_page,
    )

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    html = asyncio.run(_render_etl_run_page(
        cache, dev_log=False,
        last_summary=None, last_run_at=None,
        db_pool=None, dialect=None,
        prefix_override=None, cfg=make_test_config(),
        top_nav_html="",
        is_running=True,
    ))
    assert 'id="etl-run-cancel-btn"' in html
    assert "data-test-cancel-help" in html
    # Help copy includes the operator-locked partial-state framing.
    # Whitespace-normalize for the assertion since the rendered prose
    # wraps across multiple lines.
    normalized = " ".join(html.split())
    assert "we don't auto-clean to help with troubleshooting" in normalized
    # Tooltip on the button itself.
    assert 'title="Stops pipeline immediately' in html
