"""X.2.spike.1 — unit tests for the HTML renderer.

Spike.1 proves the tree → HTML projection works. These tests verify:

1. Sheet title + description appear in the output.
2. Each visual becomes one ``<section>`` with title + subtitle.
3. Visual class name is exposed as ``data-visual-kind`` (the d3
   hydration hook for spike.2).
4. HTML is escaped at leaves (defensive against L2-supplied prose
   that might include angle brackets or quotes).
5. Output is a complete, well-formed HTML document.
6. ``app.resolve_auto_ids()`` runs before render — visuals built with
   ``visual_id=AUTO`` (the default) land as ``data-visual-id="v-kpi-
   s0-0"`` in the HTML, NOT ``"_AutoSentinel.AUTO"``. spike.2 keys
   hx-post URLs off ``data-visual-id``, so unresolved IDs would
   silently break the swap dispatch.

No live data, no chart libraries, no HTMX — those land in spike.2.
"""

from __future__ import annotations

import pytest

from tests._test_helpers import make_test_config
from recon_gen.common.html import emit_html
from recon_gen.common.ids import SheetId, VisualId
from recon_gen.common.tree.structure import Analysis, App, Sheet
from recon_gen.common.tree.visuals import KPI


_TEST_CFG = make_test_config()


def _build_app(sheet: Sheet) -> App:
    """Wrap a Sheet in the minimal App+Analysis needed by emit_html.

    emit_html calls ``app.resolve_auto_ids()`` and validates that the
    sheet is part of ``app.analysis.sheets`` — both invariants need a
    full App context, not a raw Sheet.
    """
    app = App(name="html-test", cfg=_TEST_CFG)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="html-test-analysis",
        name="HTML Test",
    ))
    analysis.add_sheet(sheet)
    return app


def _minimal_sheet() -> Sheet:
    """Tree ``Sheet`` with one KPI — the smallest non-trivial fixture."""
    sheet = Sheet(
        sheet_id=SheetId("test-sheet"),
        name="Test",
        title="Test Sheet Title",
        description="A short description.",
    )
    sheet.visuals.append(
        KPI(
            title="Open Exceptions",
            subtitle="Count of open invariant violations.",
            visual_id=VisualId("v-test-kpi"),
        ),
    )
    return sheet


def test_emit_html_includes_sheet_title() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Test Sheet Title" in out


def test_emit_html_includes_sheet_description() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "A short description." in out


def test_emit_html_includes_back_to_dashboards_link() -> None:
    """Sheet pages must surface a way back to the listing — without it,
    a dashboard tab is a dead end (sheet tabs only walk within the
    current dashboard, not back to the list of all dashboards)."""
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert 'href="/dashboards"' in out
    assert "← Dashboards" in out


def test_emit_html_emits_one_section_per_visual() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert out.count("<section") == 1


def test_emit_html_includes_visual_title_and_subtitle() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Open Exceptions" in out
    assert "Count of open invariant violations." in out


def test_emit_html_carries_visual_kind_attribute() -> None:
    """X.4 + spike.2 hook: visual class name lands as a data attribute
    so a single bootstrap JS can target d3 hydration per kind without
    reflection."""
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert 'data-visual-kind="KPI"' in out


def test_emit_html_carries_visual_id_attribute() -> None:
    """Visual id lands too — needed when spike.2's hx-get fragment
    swap targets a specific visual."""
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert 'data-visual-id="v-test-kpi"' in out


def test_emit_html_visual_div_uses_queue_last_hx_sync() -> None:
    """AA.B.5.followon — the visual-data div must declare
    ``hx-sync="this:queue last"``, not ``this:replace``.

    Why this pin matters: chain bqaak83tb proved that under
    parallel-initial-load + mid-load filter pick, ``this:replace`` lost
    the new request on the 3 slowest-rendering visuals (Closing Stored
    / Drift / Posted Money Records — the bottom 3 of 6 in DOM order).
    The data-bound-params diagnostic captured this: those 3 visuals'
    params stayed on the initial empty values while the top 3 picked
    up the new account. ``queue last`` queues the new trigger until
    the in-flight completes, then fires it — minor flicker, full
    correctness. A regression to ``this:replace`` (or any other
    strategy) would re-introduce the partial-refetch bug. Pin the
    string so a careless edit fails here, not in a brittle 5-min chain.
    """
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert 'hx-sync="this:queue last"' in out, (
        "visual-data div must use queue-last sync — see "
        "AA.B.5.followon for the bug class that 'this:replace' allowed"
    )
    assert 'hx-sync="this:replace"' not in out, (
        "regression: this:replace dropped refresh on slow visuals"
    )


def test_emit_html_resolves_auto_visual_ids() -> None:
    """Regression for the spike.1 footgun: visuals built with the
    default ``visual_id=AUTO`` must have IDs resolved before they
    land in HTML. Pre-fix this emitted ``data-visual-id=
    "_AutoSentinel.AUTO"`` because resolution only ran inside
    emit_analysis / emit_dashboard."""
    from recon_gen.common.tree._helpers import auto_id

    sheet = Sheet(
        sheet_id=SheetId("auto-sheet"),
        name="Auto",
        title="Auto Title",
        description="x",
    )
    # No visual_id passed — defaults to AUTO sentinel.
    sheet.visuals.append(KPI(title="K1", subtitle="t"))
    sheet.visuals.append(KPI(title="K2", subtitle="t"))

    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "_AutoSentinel" not in out
    # resolve_auto_ids feeds the position slug ``v-{kind}-s{sheet}-
    # {visual}`` through ``auto_id()`` (UUIDv5, M.4.4.10c) so the
    # final attribute value is the deterministic UUID, not the slug
    # itself.
    assert f'data-visual-id="{auto_id("v-kpi-s0-0")}"' in out
    assert f'data-visual-id="{auto_id("v-kpi-s0-1")}"' in out


def test_emit_html_rejects_sheet_not_in_app() -> None:
    """Sheet must belong to the App we pass — without that we couldn't
    resolve IDs against the right analysis. Catch the wrong-app
    footgun loudly."""
    sheet = _minimal_sheet()
    other_app = App(name="other", cfg=_TEST_CFG)
    other_app.set_analysis(Analysis(
        analysis_id_suffix="other-analysis",
        name="Other",
    ))
    with pytest.raises(ValueError, match="not part of App"):
        emit_html(other_app, sheet, dashboard_id="test-dashboard")


def test_emit_html_escapes_titles() -> None:
    """L2 instances supply prose; renderer must defend against
    angle brackets / ampersands at the leaf level."""
    sheet = Sheet(
        sheet_id=SheetId("xss-sheet"),
        name="x",
        title="<script>alert(1)</script>",
        description="A & B",
    )
    sheet.visuals.append(
        KPI(title="<b>bold</b>", subtitle="t", visual_id=VisualId("v-x")),
    )
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "A &amp; B" in out
    assert "<b>bold</b>" not in out
    assert "&lt;b&gt;bold&lt;/b&gt;" in out


def test_emit_html_returns_complete_document() -> None:
    sheet = _minimal_sheet()
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert out.startswith("<!DOCTYPE html>")
    assert "<html" in out
    assert "</html>" in out.strip()
    assert "<head>" in out
    assert "<body" in out  # may carry class attributes


def test_emit_html_handles_empty_sheet() -> None:
    """Edge: a sheet with zero visuals still emits a valid document
    (just title + description, no sections)."""
    sheet = Sheet(
        sheet_id=SheetId("empty"),
        name="Empty",
        title="Empty Sheet",
        description="No visuals yet.",
    )
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Empty Sheet" in out
    assert "<section" not in out
    assert out.startswith("<!DOCTYPE html>")


def test_emit_html_handles_visual_without_subtitle() -> None:
    """Some visual kinds have ``subtitle="t"``; the subtitle ``<p>``
    must be omitted when subtitle is unset (no empty paragraphs)."""
    sheet = Sheet(
        sheet_id=SheetId("no-subtitle"),
        name="x",
        title="No Subtitle",
        description="x",
    )
    sheet.visuals.append(
        KPI(title="Bare KPI", subtitle="t", visual_id=VisualId("v-bare")),
    )
    out = emit_html(
        _build_app(sheet), sheet, dashboard_id="test-dashboard",
    )
    assert "Bare KPI" in out
    assert 'class="subtitle"' not in out


# AA.B.5.followon.diag — emit_visual_data_fragment carries url-param echo


def test_emit_visual_data_fragment_stamps_url_params_as_data_attr() -> None:
    """When ``url_params`` is supplied, the rendered ``<script>`` tag
    carries a ``data-bound-params`` attribute with the param_*, filter_*,
    and date_from/date_to keys. Failure-capture ``dom.html`` then
    self-describes what each visual was queried with — telling
    "picked X, server returned 0 rows because X matches nothing"
    apart from "X never reached the server" without re-deploying.
    """
    from recon_gen.common.html.render import emit_visual_data_fragment

    out = emit_visual_data_fragment(
        "v-test",
        {"rows": []},
        url_params={
            "param_pL1DsAccount": ["Customer 11 (cust-011)"],
            "param_pL1DsRole": ["CustomerSubledger"],
            "date_from": [""],
            "date_to": [""],
            "page_size": ["50"],  # NOT param_/filter_/date — should be excluded
        },
    )
    assert 'data-bound-params="' in out
    assert "Customer 11 (cust-011)" in out
    assert "CustomerSubledger" in out
    assert "page_size" not in out  # excluded
    # Attribute value uses HTML-escaped JSON (quote-safe).
    assert '&quot;param_pL1DsAccount&quot;' in out


def test_emit_visual_data_fragment_omits_attr_when_no_params_supplied() -> None:
    """``url_params=None`` → no ``data-bound-params`` attr (preserves
    the pre-AA.B.5.followon.diag fragment shape for callers that
    don't care about the diagnostic)."""
    from recon_gen.common.html.render import emit_visual_data_fragment

    out = emit_visual_data_fragment("v-test", {"rows": []})
    assert "data-bound-params" not in out
    assert "chart-data" in out  # still emits the JSON payload script


def test_emit_visual_data_fragment_collapses_single_value_lists() -> None:
    """Single-element lists (the common single-valued param case) get
    collapsed to a bare string in the attr JSON; multi-element lists
    (multi-valued ``IN`` expansion) stay as arrays."""
    from recon_gen.common.html.render import emit_visual_data_fragment

    out = emit_visual_data_fragment(
        "v-test",
        {},
        url_params={
            "param_pSingle": ["only"],
            "param_pMulti": ["a", "b", "c"],
        },
    )
    # AA.A.9.race — compact JSON form (no space after colon) keeps the
    # server's serialization byte-identical to JS's ``JSON.stringify``
    # output so the bootstrap.js requested/rendered comparison reduces
    # to a string equality check.
    # Single → bare string "only"
    assert '&quot;param_pSingle&quot;:&quot;only&quot;' in out
    # Multi → array
    assert '&quot;param_pMulti&quot;:[&quot;a&quot;' in out


class TestQsRichtextToHtml:
    """AH.2 — App2's projection of the QS ``<text-box>`` rich-text XML
    must render every tag QS itself renders (the App2↔QS parity
    contract). Vocabulary confirmed by round-tripping a hand-authored
    QS UI text box via ``describe-analysis-definition``: bold/italic/
    strike/underline are bare ``<b>/<i>/<s>/<u>`` tags (NOT
    ``<inline>`` attrs), and ``<inline>`` additionally carries
    ``background-color`` + ``font-family``.
    """

    @staticmethod
    def _project(content: str) -> str:
        from recon_gen.common.html.render import _qs_richtext_to_html

        return _qs_richtext_to_html(content)

    def test_bold_italic_strike_underline_tags_survive(self) -> None:
        # Pre-AH.2 these fell through the unknown-tag branch → rendered
        # as plain text (the styling silently dropped on App2 only).
        out = self._project(
            "<text-box><b>b</b><i>i</i><s>s</s><u>u</u></text-box>"
        )
        assert "<b>b</b>" in out
        assert "<i>i</i>" in out
        assert "<s>s</s>" in out
        assert "<u>u</u>" in out

    def test_inline_background_color_becomes_span_style(self) -> None:
        out = self._project(
            '<text-box><inline background-color="#ff0606">x</inline></text-box>'
        )
        assert 'background-color: #ff0606' in out
        assert "<span" in out

    def test_inline_font_family_becomes_span_style(self) -> None:
        out = self._project(
            '<text-box><inline font-family="Noto Sans">x</inline></text-box>'
        )
        assert "font-family: Noto Sans" in out

    def test_inline_combines_all_supported_attrs(self) -> None:
        out = self._project(
            '<text-box><inline color="#111" font-size="20px" '
            'background-color="#eee" font-family="Menlo">x</inline></text-box>'
        )
        for frag in (
            "color: #111",
            "font-size: 20px",
            "background-color: #eee",
            "font-family: Menlo",
        ):
            assert frag in out, frag

    def test_bullet_list_gets_tailwind_list_utilities(self) -> None:
        # Tailwind Preflight resets list-style:none, so the <ul> must
        # carry the list-disc/pl-6 utilities (compiled from render.py's
        # literals) to render markers. ql-indent-0 (top level) → no li
        # class; the QS-specific class is dropped.
        out = self._project(
            '<text-box><ul><li class="ql-indent-0">one</li>'
            '<li class="ql-indent-0">two</li></ul></text-box>'
        )
        assert '<ul class="list-disc pl-6 my-2">' in out
        assert out.count("<li>") == 2  # both top-level → bare <li>
        assert "ql-indent-0" not in out

    def test_nested_bullets_get_per_level_indent(self) -> None:
        # QS encodes nesting as a flat list with ql-indent-N; each level
        # projects to a left-margin utility so nested bullets indent.
        out = self._project(
            '<text-box><ul>'
            '<li class="ql-indent-0">top</li>'
            '<li class="ql-indent-1">nest 1</li>'
            '<li class="ql-indent-2">nest 2</li>'
            '</ul></text-box>'
        )
        assert '<li class="ml-[1.5rem]">nest 1</li>' in out
        assert '<li class="ml-[3rem]">nest 2</li>' in out
        # Top level stays bare (no indent class).
        assert "<li>top</li>" in out

    def test_full_authored_box_round_trips(self) -> None:
        # Regression mirror of the hand-authored QS sample (every
        # markup type the operator could find). All formatting must
        # reach the HTML projection.
        content = (
            '<text-box>'
            '<inline color="#2e5090" font-size="20px">L2 Coverage</inline>'
            '<br/>'
            '<ul>'
            '<li class="ql-indent-0"><b>6 internal</b> accounts</li>'
            '<li class="ql-indent-0"><i>1 account</i> templates</li>'
            '<li class="ql-indent-0"><s>6 rails</s> patterns</li>'
            '<li class="ql-indent-0"><u>2 transfer</u> templates</li>'
            '<li class="ql-indent-0">'
            '<inline background-color="#ff0606">1 chains</inline> flows</li>'
            '<li class="ql-indent-0">'
            '<inline font-family="Noto Sans">1 limit</inline> schedules</li>'
            '</ul>'
            '</text-box>'
        )
        out = self._project(content)
        assert "<b>6 internal</b>" in out
        assert "<i>1 account</i>" in out
        assert "<s>6 rails</s>" in out
        assert "<u>2 transfer</u>" in out
        assert "background-color: #ff0606" in out
        assert "font-family: Noto Sans" in out
        assert "color: #2e5090" in out
        assert out.count("<li>") == 6

    def test_block_align_becomes_text_align_div(self) -> None:
        out = self._project(
            '<text-box><block align="center">mid</block>'
            '<block align="right">end</block></text-box>'
        )
        assert '<div class="text-center">mid</div>' in out
        assert '<div class="text-right">end</div>' in out

    def test_expression_degrades_to_literal_text(self) -> None:
        # App2 has no live parameter state in the text-box render path,
        # so the placeholder shows its literal source rather than
        # silently vanishing.
        out = self._project(
            "<text-box><expression>${pL1UnbundledRail}</expression></text-box>"
        )
        assert "${pL1UnbundledRail}" in out


class TestParameterDropdownCascade:
    """BR.1 — App2 cascade refresh on cascading dropdowns.

    The renderer emits ``hx-get`` / ``hx-trigger`` / ``hx-target`` /
    ``hx-swap`` on the ``<select>`` only when ``cascade_source_param``
    is set. Static-enum + parameter-free LinkedValues dropdowns stay
    inert (no HTMX wiring overhead). The JS handler in bootstrap.js
    looks for ``data-cascade-source-param`` to detect which swaps need
    Tom Select re-init.
    """

    def test_cascade_attrs_emit_when_source_set(self) -> None:
        from recon_gen.common.html.render import (
            ParameterDropdownSpec, _render_parameter_dropdown,
        )
        from recon_gen.apps.l1_dashboard.datasets import (
            DS_L1_DS_ACCOUNTS, P_L1_DS_ACCOUNT_DSP, P_L1_DS_ROLE_DSP,
        )
        spec = ParameterDropdownSpec(
            name=P_L1_DS_ACCOUNT_DSP, label="Account",
            options=("Acct A", "Acct B"),
            options_dataset=DS_L1_DS_ACCOUNTS,
            options_column="account_display",
            cascade_source_param=P_L1_DS_ROLE_DSP,
        )
        out = _render_parameter_dropdown(spec)
        assert f'hx-get="dropdown-options/{DS_L1_DS_ACCOUNTS}/account_display"' in out
        assert f"hx-trigger=\"change from:[name='param_{P_L1_DS_ROLE_DSP}']" in out
        assert 'hx-target="this"' in out
        assert 'hx-swap="innerHTML"' in out
        assert f'data-cascade-source-param="{P_L1_DS_ROLE_DSP}"' in out

    def test_no_cascade_attrs_when_source_unset(self) -> None:
        from recon_gen.common.html.render import (
            ParameterDropdownSpec, _render_parameter_dropdown,
        )
        from recon_gen.apps.l1_dashboard.datasets import (
            DS_L1_DS_ROLES, P_L1_DS_ROLE_DSP,
        )
        spec = ParameterDropdownSpec(
            name=P_L1_DS_ROLE_DSP, label="Role",
            options=("SouthPool", "NorthPool"),
            options_dataset=DS_L1_DS_ROLES,
            options_column="account_role",
        )
        out = _render_parameter_dropdown(spec)
        assert "hx-get=" not in out
        assert "hx-trigger=" not in out
        assert "data-cascade-source-param" not in out
