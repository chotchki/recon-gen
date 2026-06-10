"""CZ.5 — App2 UX for standalone-mode (banner + button label).

Pins the operator-locked design (REPLAN, 2026-06-09):

- ``cfg.etl_hook is None`` is the gate signal. The Trainer reset +
  Studio Deploy-changes paths will only DELETE rows tagged
  ``metadata.source = 'training'``; any unmarked rows are presumed
  real customer data and survive.

- The UI surfaces the protection BEFORE the operator clicks: a
  persistent banner (distinct color from the CU.3 demo-mode banner),
  a Trainer reset button label switch to the REPLAN-locked
  "Clear synthetic rows and re-seed" copy, and a visually-disabled
  Studio Deploy-changes button with a tooltip explanation.

- When ``cfg.etl_hook`` IS configured (ETL-mode), the banner is
  absent, button labels are unchanged, and Deploy-changes is the
  full clickable button — TRUNCATE + reseed is safe because the
  next ETL cycle refills.
"""

from __future__ import annotations

from dataclasses import replace as dataclass_replace
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")

from recon_gen.common.html._studio_routes import (
    STANDALONE_DEPLOY_DISABLED_TOOLTIP,
    STANDALONE_MODE_BANNER_TEXT,
    STANDALONE_RESET_BUTTON_LABEL,
    _render_data_page,
    _standalone_mode_banner,
)
from recon_gen.common.html._studio_training_v2 import (
    _render_reset_button,
    render_training_landing,
    render_training_plant_page,
)
from recon_gen.common.html._studio_training_v3 import (
    _render_session_controls,
    render_training_v3_landing,
)
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def l2_yaml(tmp_path: Path) -> Path:
    """A writable copy of the spec_example fixture so the
    L2InstanceCache loader has something concrete to hand
    ``_render_data_page``."""
    import shutil
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    return dst


# -- _standalone_mode_banner helper --------------------------------------


def test_standalone_banner_renders_locked_copy_when_etl_hook_is_none() -> None:
    """Operator-locked banner text (REPLAN). Pin against the constant
    so a rename loudly trips this test instead of silently degrading
    the customer-facing copy."""
    from html import escape

    cfg = make_test_config()  # etl_hook defaults to None
    assert cfg.etl_hook is None

    html = _standalone_mode_banner(cfg)

    assert "data-test-standalone-mode-banner" in html
    # The banner copy is HTML-escaped (apostrophes → &#x27;) so we
    # check against the escaped form to verify the literal string
    # ships intact to the page.
    assert escape(STANDALONE_MODE_BANNER_TEXT) in html
    # Banner copy must say the locked things — no rewording allowed
    # without bumping the test.
    assert "Standalone mode" in html
    assert "metadata.source=" in html  # escape() may transform the quote
    assert "cfg.etl_hook" in html


def test_standalone_banner_absent_when_etl_hook_configured() -> None:
    """Real-hook deployments don't get the banner — they're not in
    the protection regime."""
    cfg = make_test_config()
    cfg_with_hook = dataclass_replace(cfg, etl_hook="./bin/my_etl.py")

    html = _standalone_mode_banner(cfg_with_hook)
    assert html == ""


def test_standalone_banner_absent_when_embed() -> None:
    """Embedded iframe surfaces suppress chrome (mirrors the CU.3
    banner's embed=True behavior)."""
    cfg = make_test_config()
    assert cfg.etl_hook is None

    html = _standalone_mode_banner(cfg, embed=True)
    assert html == ""


def test_standalone_banner_absent_when_cfg_is_none() -> None:
    """No cfg means we don't know the gate state. Default to silent
    (consumers of _banner already handle the cfg=None branch)."""
    html = _standalone_mode_banner(None)
    assert html == ""


def test_standalone_banner_color_distinct_from_demo_banner() -> None:
    """CU.3 demo banner = amber (#fff3cd background, #664d03 text);
    CZ.5 standalone-mode banner must use a different palette so the
    operator can read them as separate signals at a glance.
    """
    cfg = make_test_config()
    html = _standalone_mode_banner(cfg)

    # Demo banner amber tokens — must NOT appear here.
    assert "#fff3cd" not in html
    assert "#664d03" not in html
    # Standalone banner uses a muted-blue palette (locked at
    # implementation time; if you swap colors, swap this assertion).
    assert "#dbeafe" in html  # background
    assert "#1e3a8a" in html  # text color


# -- _render_reset_button (Trainer reset, v2 surface) --------------------


def test_trainer_reset_button_label_standalone_mode() -> None:
    """In standalone-mode the Trainer reset button label switches to
    the REPLAN-locked copy: "Clear synthetic rows and re-seed".
    """
    html = _render_reset_button(standalone_mode=True)

    assert STANDALONE_RESET_BUTTON_LABEL in html
    # Default "Reset to clean baseline" copy must NOT appear when the
    # standalone label is on — the two are mutually exclusive.
    assert "Reset to clean baseline" not in html
    # Tooltip explains what the gate actually does so the operator
    # isn't surprised by the narrower scope.
    assert "metadata.source=&#x27;training&#x27;" in html or \
        "metadata.source='training'" in html
    # Drivers + e2e tests can target this attr to assert the standalone
    # path is wired without parsing the visible label.
    assert "data-test-training-reset-standalone" in html


def test_trainer_reset_button_label_default_mode() -> None:
    """Hook-configured deployments keep the original "Reset to clean
    baseline" copy."""
    html = _render_reset_button(standalone_mode=False)

    assert "Reset to clean baseline" in html
    assert STANDALONE_RESET_BUTTON_LABEL not in html
    assert "data-test-training-reset-standalone" not in html


# -- Trainer v3 landing surface (the primary surface today) --------------


def test_training_v3_landing_renders_standalone_banner() -> None:
    """The Trainer landing page surfaces the standalone-mode banner so
    the protection signal is visible the moment the operator lands."""
    from html import escape

    html = render_training_v3_landing(
        base_prefix="spec_example",
        v_overlay_exists=False,
        standalone_mode=True,
    )

    assert "data-test-standalone-mode-banner" in html
    # Apostrophes inside the locked copy ship escaped — pin against
    # the escaped form to assert the literal text reached the page.
    assert escape(STANDALONE_MODE_BANNER_TEXT) in html


def test_training_v3_landing_omits_standalone_banner_when_hook_configured() -> None:
    """Real-hook deployments don't get the banner."""
    html = render_training_v3_landing(
        base_prefix="spec_example",
        v_overlay_exists=False,
        standalone_mode=False,
    )

    assert "data-test-standalone-mode-banner" not in html
    assert STANDALONE_MODE_BANNER_TEXT not in html


def test_training_v3_rebuild_button_label_standalone_mode() -> None:
    """In standalone-mode the "Force rebuild from base" button (the
    v3-era analogue of v2's "Reset to clean baseline") switches to the
    locked "Clear synthetic rows and re-seed" copy."""
    html = _render_session_controls(
        v_overlay_exists=True,
        standalone_mode=True,
    )

    assert STANDALONE_RESET_BUTTON_LABEL in html
    assert "Force rebuild from base" not in html
    assert "data-test-training-rebuild-standalone" in html


def test_training_v3_rebuild_button_label_default_mode() -> None:
    """Hook-configured deployments keep the "Force rebuild from base"
    copy."""
    html = _render_session_controls(
        v_overlay_exists=True,
        standalone_mode=False,
    )

    assert "Force rebuild from base" in html
    assert STANDALONE_RESET_BUTTON_LABEL not in html
    assert "data-test-training-rebuild-standalone" not in html


# -- Studio Deploy-changes button (data-shape page) ----------------------


def test_studio_deploy_button_disabled_in_standalone_mode(
    l2_yaml: Path,
) -> None:
    """The Studio data-shape page's Deploy-changes button is rendered
    visually disabled with a tooltip when ``cfg.etl_hook is None``.
    Couples with the persistent banner: the operator sees the
    protection before they click."""
    cache = L2InstanceCache.from_path(l2_yaml)
    cfg = make_test_config()
    assert cfg.etl_hook is None

    html = _render_data_page(cache, dev_log=False, cfg=cfg)

    # The deploy button MUST carry the disabled attr + the
    # standalone-disabled test marker.
    assert "data-test-deploy-standalone-disabled" in html
    assert "id=\"deploy-btn\"" in html
    assert " disabled" in html
    # Tooltip explains the gate.
    assert "Standalone mode" in html
    # The standalone banner is also present on this page.
    assert "data-test-standalone-mode-banner" in html


def test_studio_deploy_button_enabled_when_hook_configured(
    l2_yaml: Path,
) -> None:
    """ETL-mode keeps the Deploy-changes button live — TRUNCATE +
    reseed is safe because the next ETL cycle refills."""
    cache = L2InstanceCache.from_path(l2_yaml)
    cfg = make_test_config()
    cfg_with_hook = dataclass_replace(cfg, etl_hook="./bin/my_etl.py")

    html = _render_data_page(cache, dev_log=False, cfg=cfg_with_hook)

    # The standalone-disabled marker MUST be absent and the live
    # quicksightDeploy() handler wired in.
    assert "data-test-deploy-standalone-disabled" not in html
    assert "quicksightDeploy()" in html
    # The standalone banner is also absent.
    assert "data-test-standalone-mode-banner" not in html


def test_studio_deploy_disabled_tooltip_carries_locked_copy(
    l2_yaml: Path,
) -> None:
    """The disabled-button tooltip must echo the gate framing so
    hover + banner read consistently."""
    cache = L2InstanceCache.from_path(l2_yaml)
    cfg = make_test_config()
    assert cfg.etl_hook is None

    html = _render_data_page(cache, dev_log=False, cfg=cfg)

    # Pin the locked tooltip via the module constant.
    # `STANDALONE_DEPLOY_DISABLED_TOOLTIP` is rendered into the title
    # attribute (escape()-wrapped) — assert the key phrases survive.
    assert "Standalone mode" in STANDALONE_DEPLOY_DISABLED_TOOLTIP
    assert "Deploy-changes is " in STANDALONE_DEPLOY_DISABLED_TOOLTIP
    # The HTML rendering escapes apostrophes; check the key
    # operator-facing phrase made it into the page.
    assert "cfg.etl_hook" in html


# -- v2 plant page surface (orphaned but pinned) -------------------------


def test_v2_plant_page_reset_button_standalone_label() -> None:
    """v2's per-kind plant page footer carries the same reset button
    + must follow the standalone-mode label rule. Even though v3 is
    the primary surface, v2 routes are still registered + reachable;
    pinning the label switch keeps the two surfaces consistent."""
    entry = next(iter(PLANT_REGISTRY))
    html = render_training_plant_page(
        entry,
        standalone_mode=True,
    )

    assert STANDALONE_RESET_BUTTON_LABEL in html
    assert "data-test-training-reset-standalone" in html


def test_v2_plant_page_reset_button_default_label() -> None:
    entry = next(iter(PLANT_REGISTRY))
    html = render_training_plant_page(
        entry,
        standalone_mode=False,
    )

    assert "Reset to clean baseline" in html
    assert "data-test-training-reset-standalone" not in html


def test_v2_landing_reset_button_standalone_label() -> None:
    """v2's training landing (orphaned but still registered) carries
    the reset button — label switch must follow the gate."""
    html = render_training_landing(standalone_mode=True)

    assert STANDALONE_RESET_BUTTON_LABEL in html
    assert "data-test-training-reset-standalone" in html


def test_v2_landing_reset_button_default_label() -> None:
    html = render_training_landing(standalone_mode=False)

    assert "Reset to clean baseline" in html
    assert "data-test-training-reset-standalone" not in html
