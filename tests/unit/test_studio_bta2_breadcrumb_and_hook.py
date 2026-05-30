"""BTa.2 — P1 cluster pins.

Covers the three render-layer additions from BTa.2:

- Hook attribution in `_render_etl_run_form` (`_format_hook_attribution`)
  — last-run banner shows whether the operator's hook or the bundled
  demo placeholder ran, plus the exit code on halted runs.
- `?from=` query-param breadcrumb plumbing — `_render_gap_card` appends
  `?from=/etl/triage`; `_render_create_page` / `_render_edit_page`
  render the sticky back link + hidden form input; the save / create
  POSTs redirect to the carried target.
- Same-origin guard on `?from=` — `//evil`, backslash-prefixed
  values, and absent values all suppress the breadcrumb (no
  open-redirect surface).

Triage `link_target` rewrites (`/l2_shape/<kind>/new` instead of
`/l2_shape/<kind>/`) live in `test_l2_triage.py` already.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_editor_routes import (
    _back_breadcrumb_html,
    _from_hidden_input,
    _safe_back_target,
)
from recon_gen.common.html._studio_routes import (
    _append_from_query,
    _format_hook_attribution,
    _render_etl_run_form,
    _render_gap_card,
    make_studio_routes,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.deploy_pipeline import DeploySummary
from recon_gen.common.l2.triage import Gap, GapEvidence
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


# -- Hook attribution ------------------------------------------------------


def test_format_hook_attribution_none_marks_bundled_demo() -> None:
    """Absent etl_hook ⇒ banner flags the bundled demo placeholder
    so first-time operators don't assume their hook ran."""
    html = _format_hook_attribution(None)
    assert "bundled demo" in html
    assert "no operator hook configured" in html
    # Italicized (not code-style) so it visually reads as
    # configuration-state, not a command.
    assert "<em>" in html


def test_format_hook_attribution_empty_string_treated_as_unconfigured() -> None:
    """Empty string ⇒ same bundled-demo message (defensive: cfg's
    YAML loader may produce '' instead of None for blank fields)."""
    assert "bundled demo" in _format_hook_attribution("")


def test_format_hook_attribution_with_command_renders_code_tag() -> None:
    """Configured etl_hook renders as <code>cmd</code> so the operator
    sees the exact string that ran."""
    html = _format_hook_attribution("./bin/my_etl.py --env=test")
    assert "<code>" in html
    assert "./bin/my_etl.py --env=test" in html
    assert "bundled demo" not in html


def test_format_hook_attribution_escapes_html_in_command() -> None:
    """Defensive: cfg.etl_hook is operator-controlled, never trust it."""
    html = _format_hook_attribution("<script>alert('x')</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# -- Run form banner shape -------------------------------------------------


def test_render_etl_run_form_no_summary_says_no_runs_yet() -> None:
    html = _render_etl_run_form(last_summary=None, last_run_at=None)
    assert "No runs yet" in html
    # No hook attribution when no run has happened.
    assert "hook:" not in html


def test_render_etl_run_form_success_shows_hook_attribution() -> None:
    """Successful run banner now carries a `hook:` line so the operator
    can tell whether their hook or the bundled demo ran."""
    summary = DeploySummary(
        halted=False,
        step1_etl_hook_exit_code=0,
        step3_generator_transactions_after=42_000,
        step5_data_generation_id=7,
    )
    html = _render_etl_run_form(
        last_summary=summary,
        last_run_at=datetime(2026, 5, 30, 12, 0, 0),
        etl_hook_command="./bin/my_etl.py",
    )
    assert "● success" in html
    assert "hook:" in html
    assert "./bin/my_etl.py" in html


def test_render_etl_run_form_halted_shows_hook_and_exit_code() -> None:
    """Halted runs surface BOTH the hook attribution AND the exit code
    so the operator can tell what failed at a glance."""
    summary = DeploySummary(
        halted=True,
        halt_reason="step1_etl_hook_nonzero",
        step1_etl_hook_exit_code=42,
    )
    html = _render_etl_run_form(
        last_summary=summary,
        last_run_at=datetime(2026, 5, 30, 12, 0, 0),
        etl_hook_command="./bin/broken.sh",
    )
    assert "● HALTED" in html
    assert "step1_etl_hook_nonzero" in html
    assert "hook:" in html
    assert "./bin/broken.sh" in html
    assert "exit code:" in html
    assert ">42<" in html  # exit code rendered in a <code> tag


def test_render_etl_run_form_halted_without_hook_says_bundled_demo() -> None:
    summary = DeploySummary(
        halted=True,
        halt_reason="step3_generator_nonzero",
        step1_etl_hook_exit_code=0,
    )
    html = _render_etl_run_form(
        last_summary=summary,
        last_run_at=datetime(2026, 5, 30, 12, 0, 0),
        etl_hook_command=None,
    )
    assert "bundled demo" in html
    assert "exit code:" in html
    # Exit code 0 still surfaces (the halt happened later in the pipeline).
    assert ">0<" in html


# -- ?from= query plumbing -------------------------------------------------


def test_append_from_query_no_existing_query() -> None:
    assert _append_from_query("/l2_shape/rail/new", "/etl/triage") == (
        "/l2_shape/rail/new?from=/etl/triage"
    )


def test_append_from_query_existing_query_uses_ampersand() -> None:
    assert _append_from_query("/l2_shape/rail/new?subtype=two_leg", "/etl/triage") == (
        "/l2_shape/rail/new?subtype=two_leg&from=/etl/triage"
    )


def test_append_from_query_url_encodes_path() -> None:
    """`/etl/triage` keeps its slashes (safe='/') so the link reads
    naturally in the HTML; other special chars would be escaped."""
    assert "/etl/triage" in _append_from_query("/x", "/etl/triage")


def test_render_gap_card_links_to_breadcrumb_carrying_target() -> None:
    """Triage CTAs ship the `?from=/etl/triage` carryover so the
    editor renders the back-breadcrumb + the save POST round-trips
    back to triage."""
    gap = Gap(
        kind="unmatched_rail",
        diagnosis="Phantom rail seen 3x.",
        observed_value="phantom",
        evidence=GapEvidence(row_count=3),
        link_target="/l2_shape/rail/new",
    )
    html = _render_gap_card(gap)
    assert 'href="/l2_shape/rail/new?from=/etl/triage"' in html


# -- Back-breadcrumb same-origin guard -------------------------------------


def test_safe_back_target_accepts_same_origin_paths() -> None:
    assert _safe_back_target("/etl/triage") == "/etl/triage"
    assert _safe_back_target("/etl/probe") == "/etl/probe"
    assert _safe_back_target("/diagram") == "/diagram"


def test_safe_back_target_rejects_absent_or_blank() -> None:
    assert _safe_back_target(None) is None
    assert _safe_back_target("") is None


def test_safe_back_target_rejects_open_redirect_shapes() -> None:
    """Open-redirect guard — anything that isn't strictly same-origin
    `/path` is dropped. Covers full URLs, scheme-relative, and the
    backslash bypass some HTTP libs misparse."""
    assert _safe_back_target("http://evil.com") is None
    assert _safe_back_target("https://evil.com/etl") is None
    assert _safe_back_target("//evil.com") is None
    assert _safe_back_target("/\\evil") is None
    assert _safe_back_target("evil") is None  # not starting with /


def test_back_breadcrumb_html_renders_link_when_safe() -> None:
    html = _back_breadcrumb_html("/etl/triage")
    assert "data-test-back-breadcrumb" in html
    assert 'href="/etl/triage"' in html
    assert "← Back to Triage" in html


def test_back_breadcrumb_html_uses_friendly_label() -> None:
    """Known-path labels resolve to friendly names; unknown paths
    echo the raw path so the operator still sees something."""
    assert "← Back to Probe" in _back_breadcrumb_html("/etl/probe")
    assert "← Back to Refresh Data" in _back_breadcrumb_html("/etl/run")
    # Unknown path falls back to the raw path.
    assert "/some/custom/path" in _back_breadcrumb_html("/some/custom/path")


def test_back_breadcrumb_html_renders_empty_when_unsafe() -> None:
    """Open-redirect rejects render as empty string — caller can
    safely concatenate without a guard."""
    assert _back_breadcrumb_html(None) == ""
    assert _back_breadcrumb_html("http://evil.com") == ""
    assert _back_breadcrumb_html("") == ""


def test_from_hidden_input_emits_input_when_safe() -> None:
    html = _from_hidden_input("/etl/triage")
    assert '<input type="hidden" name="_back_from"' in html
    assert 'value="/etl/triage"' in html


def test_from_hidden_input_blank_when_unsafe() -> None:
    assert _from_hidden_input(None) == ""
    assert _from_hidden_input("//evil") == ""


# -- Editor route integration ---------------------------------------------


def test_editor_new_form_with_from_renders_breadcrumb(
    writable_l2_yaml: Path,
) -> None:
    """GET /l2_shape/transfer_template/new?from=/etl/triage renders
    the sticky back link in the page header."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(
            "/l2_shape/transfer_template/new?from=/etl/triage",
        ).text
    assert "data-test-back-breadcrumb" in body
    assert 'href="/etl/triage"' in body
    # Hidden input rides the form so the POST can round-trip the
    # carried target.
    assert 'name="_back_from"' in body
    assert 'value="/etl/triage"' in body


def test_editor_new_form_without_from_omits_breadcrumb(
    writable_l2_yaml: Path,
) -> None:
    """No ?from= ⇒ no breadcrumb, no hidden input. The editor stays
    identical to its pre-BTa.2 shape when nothing routed to it."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/l2_shape/transfer_template/new").text
    assert "data-test-back-breadcrumb" not in body
    assert 'name="_back_from"' not in body


def test_editor_new_form_drops_open_redirect_from(
    writable_l2_yaml: Path,
) -> None:
    """A poisoned ?from= (http://...) ⇒ no breadcrumb, no hidden
    input — guards against a triage CTA being tampered with."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(
            "/l2_shape/transfer_template/new?from=http://evil.com",
        ).text
    assert "data-test-back-breadcrumb" not in body
    assert 'name="_back_from"' not in body
