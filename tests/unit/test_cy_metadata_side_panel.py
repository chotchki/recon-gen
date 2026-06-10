"""CY.5 — row-metadata side-panel renderer + route tests.

Covers the ``GET /dashboards/{dashboard_id}/sheets/{sheet_id}/rows/metadata``
route registered in ``common/html/server.py::make_app`` and the
``render_metadata_panel`` helper in ``common/html/_side_panel.py``.

Pins (per PLAN.md CY.5 operator-locked spec):

- Route returns 200 + the collapsible ``<details>`` tree fragment for a
  typical row metadata dict (Copy + Expand all + Collapse all buttons,
  ``role="complementary"``).
- Empty / null branches render exactly the operator-locked empty-state
  fragment (no toolbar).
- 404 for unknown ``dashboard_id``, unknown ``sheet_id``, and a known
  sheet whose Table visual has ``metadata_popup=False``.
- ``<details>`` open-by-default for depth ≤ 2; closed for deeper levels.
- Primitive leaves render as JSON literals with ``data-json-leaf``
  attribute (``"value"`` / ``true`` / ``null`` / ``42``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, cast

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    isolated_dataset_registries,
    register_contract,
)
from recon_gen.common.html._side_panel import render_metadata_panel
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    stub_money_trail_fetcher,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.ids import SheetId, VisualId
from recon_gen.common.tree.datasets import Dataset
from recon_gen.common.tree.structure import Analysis, App, Sheet
from recon_gen.common.tree.visuals import Dim, Table
from tests._test_helpers import make_test_config


# -- render_metadata_panel unit tests --------------------------------------


def test_render_metadata_panel_empty_dict_returns_empty_state() -> None:
    """``metadata == {}`` short-circuits to the operator-locked empty
    fragment — no toolbar."""
    html = render_metadata_panel({}, transaction_id="txn-1")
    assert html == (
        '<p class="text-secondary-fg italic">No metadata for this row.</p>'
    )
    # No toolbar surface bleeds through.
    assert "data-metadata-copy" not in html
    assert "data-metadata-expand-all" not in html
    assert "data-metadata-collapse-all" not in html


def test_render_metadata_panel_empty_list_returns_empty_state() -> None:
    html = render_metadata_panel([], transaction_id="txn-2")
    assert "No metadata for this row." in html
    assert "data-metadata-copy" not in html


def test_render_metadata_panel_none_returns_empty_state() -> None:
    html = render_metadata_panel(None, transaction_id="txn-3")
    assert "No metadata for this row." in html


def test_render_metadata_panel_renders_complementary_role() -> None:
    """The rendered panel carries ``role="complementary"`` per PLAN.md
    CY.5 ARIA contract — surfaces the side-panel container's role to
    assistive tech."""
    html = render_metadata_panel(
        {"a": 1}, transaction_id="txn-aria",
    )
    assert 'role="complementary"' in html


def test_render_metadata_panel_includes_toolbar_buttons() -> None:
    """Copy / Expand all / Collapse all buttons + ARIA labels stamped."""
    html = render_metadata_panel(
        {"k": "v"}, transaction_id="txn-buttons",
    )
    assert "data-metadata-copy" in html
    assert "data-metadata-expand-all" in html
    assert "data-metadata-collapse-all" in html
    assert 'aria-label="Copy JSON"' in html
    assert 'aria-label="Expand all"' in html
    assert 'aria-label="Collapse all"' in html


def test_render_metadata_panel_renders_details_tree() -> None:
    """Nested dict turns into one ``<details data-json-node>`` per
    object/array node (primitives render as ``<span data-json-leaf>``,
    NOT as a ``<details>``).

    Shape: ``{"outer": {"middle": {"inner": "value"}}}`` — the
    top-level dict iterates its entries (no wrapper details), then
    each non-primitive value renders a ``<details>``. So:

    - ``"outer"`` (value is a dict) → ``<details>``
    - ``"middle"`` (value is a dict) → ``<details>``
    - ``"inner"`` (value is a primitive) → ``<span data-json-leaf>``

    Two ``<details>`` blocks total.
    """
    html = render_metadata_panel(
        {"outer": {"middle": {"inner": "value"}}},
        transaction_id="txn-tree",
    )
    assert "<details data-json-node" in html
    assert html.count("<details data-json-node") == 2
    # Leaf surfaces inside the deepest <details>.
    assert 'data-json-leaf' in html


def test_render_metadata_panel_primitive_leaves_carry_data_json_leaf() -> None:
    """Primitive leaves render as ``<span data-json-leaf>{literal}</span>``
    (JSON-literal notation — strings keep their quotes, booleans bare,
    null bare, numbers bare)."""
    html = render_metadata_panel(
        {
            "s": "hello",
            "b": True,
            "n": None,
            "i": 42,
        },
        transaction_id="txn-leaves",
    )
    # String leaves render the JSON literal (with surrounding quotes).
    assert '<span data-json-leaf>&quot;hello&quot;</span>' in html
    # Boolean → bare ``true``.
    assert '<span data-json-leaf>true</span>' in html
    # Null → bare ``null``.
    assert '<span data-json-leaf>null</span>' in html
    # Integer → bare ``42``.
    assert '<span data-json-leaf>42</span>' in html


def test_render_metadata_panel_transaction_id_in_header() -> None:
    """The header carries the transaction id so the operator sees
    which row's metadata they're inspecting."""
    html = render_metadata_panel(
        {"k": "v"}, transaction_id="txn-12345",
    )
    assert "Row metadata" in html
    assert "txn-12345" in html


def test_render_metadata_panel_hidden_raw_textarea_carries_pretty_json() -> None:
    """The Copy button reads from a hidden textarea — pretty-printed
    JSON so the operator gets a readable paste."""
    payload = {"a": 1, "b": [1, 2]}
    html = render_metadata_panel(payload, transaction_id="txn-raw")
    # Pretty-printed (indent=2) JSON appears inside the hidden textarea.
    assert "data-metadata-raw" in html
    # The textarea body holds the formatted JSON; html-escape leaves
    # the structure intact for the textarea-contents lookup below.
    match = re.search(
        r'<textarea data-metadata-raw[^>]*>(.*?)</textarea>',
        html, re.DOTALL,
    )
    assert match is not None, "expected hidden raw textarea"
    # The escape() of json.dumps(payload, indent=2) reproduces the
    # exact textarea body — verify the round-trip succeeds.
    from html import escape as _esc
    assert match.group(1) == _esc(json.dumps(payload, indent=2, default=str))


# -- Depth-based open/closed default ---------------------------------------


def _expected_open_state(payload: Any, *, depth: int) -> dict[str, bool]:
    """Walk the payload and build a mapping of summary-text → expected
    open state per PLAN.md CY.5 rule: depth ≤ 2 = open, deeper = closed.

    Returns ``{summary_label: open}``. Helpful for cross-checking the
    rendered HTML walk against the json-derived expectation.
    """
    out: dict[str, bool] = {}
    if isinstance(payload, dict):
        d = cast(dict[Any, Any], payload)
        for k, v in d.items():
            label = json.dumps(k)
            if isinstance(v, (dict, list)):
                out[label] = depth <= 2
                # Recurse into children, tagging by their own labels.
                out.update(_expected_open_state(v, depth=depth + 1))
    elif isinstance(payload, list):
        lst = cast(list[Any], payload)
        for idx, v in enumerate(lst):
            label = f"[{idx}]"
            if isinstance(v, (dict, list)):
                out[label] = depth <= 2
                out.update(_expected_open_state(v, depth=depth + 1))
    return out


def test_render_metadata_panel_details_open_state_matches_depth_rule() -> None:
    """``<details open>`` for depth ≤ 2 (top two levels); closed deeper.

    Walk the rendered fragment, collect each ``<details>`` block's open
    attribute, and verify it matches the depth-counter recursion over
    the source JSON.
    """
    payload: dict[str, Any] = {
        "top1": {
            "lvl2": {
                "lvl3": {
                    "lvl4": "deep",
                },
            },
        },
        "top2": [
            {"lvl2_list": "x"},
        ],
    }
    html = render_metadata_panel(payload, transaction_id="txn-depth")
    expected = _expected_open_state(payload, depth=1)
    # Pull every <details> + its summary in render order.
    details_re = re.compile(
        r'<details data-json-node( open)?>\s*<summary[^>]*>(.*?)</summary>',
        re.DOTALL,
    )
    rendered: list[tuple[str, bool]] = []
    for m in details_re.finditer(html):
        is_open = m.group(1) is not None
        # The summary HTML has the key label inside a span. Grab the
        # first quoted-label or [N] form.
        summary_html = m.group(2)
        key_match = re.search(
            r'(&quot;[^&]+&quot;|\[\d+\])', summary_html,
        )
        if key_match:
            raw = key_match.group(1)
            # Convert &quot; back to " so it matches json.dumps form.
            label = raw.replace("&quot;", '"')
            rendered.append((label, is_open))
    # Every <details> rendered must match the depth-driven expectation.
    for label, is_open in rendered:
        if label in expected:
            assert expected[label] == is_open, (
                f"label {label!r}: expected open={expected[label]}, "
                f"got open={is_open}"
            )


# -- Route handler tests ---------------------------------------------------


@pytest.fixture
def metadata_dashboard_fixture() -> Iterator[
    tuple[Any, str, str, str, str]
]:
    """Build a Starlette ``make_app`` with two dashboards:

    - ``with-meta``: one sheet (``rows-sheet``) carrying a Table with
      ``metadata_popup=True``;
    - ``no-meta``: one sheet (``no-meta-sheet``) carrying a Table with
      ``metadata_popup=False``.

    Yields ``(app, dash_with_meta_id, sheet_with_meta_id,
    dash_without_meta_id, sheet_without_meta_id)``.

    The contract registry is wiped to an isolated state for the
    duration of the fixture so the construction-time contract check
    sees only our test datasets.
    """
    cfg = make_test_config()
    with isolated_dataset_registries():
        ds_with_meta = Dataset(
            identifier="cy5-with-meta",
            arn="arn:aws:quicksight:::dataset/cy5-with-meta",
        )
        register_contract(
            ds_with_meta.identifier,
            DatasetContract(columns=[
                ColumnSpec("id", "STRING"),
                ColumnSpec("metadata", "STRING"),
            ]),
        )
        ds_no_meta = Dataset(
            identifier="cy5-no-meta",
            arn="arn:aws:quicksight:::dataset/cy5-no-meta",
        )
        register_contract(
            ds_no_meta.identifier,
            DatasetContract(columns=[
                ColumnSpec("id", "STRING"),
            ]),
        )

        # Dashboard 1 — metadata_popup=True.
        app_with = App(name="cy5-with-meta-app", cfg=cfg)
        analysis_with = app_with.set_analysis(
            Analysis(
                analysis_id_suffix="cy5-with-meta-analysis",
                name="CY5 With Meta",
            )
        )
        sheet_with = analysis_with.add_sheet(
            Sheet(
                sheet_id=SheetId("rows-sheet"),
                name="Rows",
                title="Rows",
                description="Sheet with metadata_popup=True Table.",
            )
        )
        sheet_with.visuals.append(
            Table(
                visual_id=VisualId("rows-tbl"),
                title="Rows Detail",
                subtitle=(
                    "Per-row Table with metadata_popup=True; surfaces "
                    "the CY.5 side-panel route."
                ),
                columns=[
                    Dim(
                        dataset=ds_with_meta, field_id="f-id", column="id",
                    ),
                ],
                metadata_popup=True,
            )
        )

        # Dashboard 2 — metadata_popup=False.
        app_no = App(name="cy5-no-meta-app", cfg=cfg)
        analysis_no = app_no.set_analysis(
            Analysis(
                analysis_id_suffix="cy5-no-meta-analysis",
                name="CY5 No Meta",
            )
        )
        sheet_no = analysis_no.add_sheet(
            Sheet(
                sheet_id=SheetId("no-meta-sheet"),
                name="NoMeta",
                title="NoMeta",
                description="Sheet without metadata_popup.",
            )
        )
        sheet_no.visuals.append(
            Table(
                visual_id=VisualId("no-meta-tbl"),
                title="No Meta Detail",
                subtitle="Plain Table; no metadata popup.",
                columns=[
                    Dim(
                        dataset=ds_no_meta, field_id="f-id", column="id",
                    ),
                ],
                metadata_popup=False,
            )
        )

        served_with = ServedDashboard(
            tree_app=app_with, sheet=sheet_with, title="with-meta",
            data_fetcher=stub_money_trail_fetcher,
            filter_specs=SMOKE_FILTER_SPECS,
        )
        served_no = ServedDashboard(
            tree_app=app_no, sheet=sheet_no, title="no-meta",
            data_fetcher=stub_money_trail_fetcher,
            filter_specs=SMOKE_FILTER_SPECS,
        )
        app = make_app(
            dashboards={
                "with-meta": served_with,
                "no-meta": served_no,
            },
        )
        yield app, "with-meta", "rows-sheet", "no-meta", "no-meta-sheet"


def test_route_returns_200_and_details_tree_for_typical_metadata(
    metadata_dashboard_fixture: tuple[Any, str, str, str, str],
) -> None:
    """Typical metadata dict → 200 + fragment carrying ``<details
    data-json-node>`` + Copy / Expand all / Collapse all buttons."""
    app, dash_id, sheet_id, _, _ = metadata_dashboard_fixture
    metadata_json = json.dumps(
        {"trace_id": "abc-123", "amount": 42, "details": {"nested": True}},
    )
    with TestClient(app) as c:  # type: ignore[arg-type]
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={
                "metadata": metadata_json,
                "transaction_id": "txn-200",
            },
        )
    assert resp.status_code == 200
    body = resp.text
    assert "<details data-json-node" in body
    # Toolbar buttons stamped.
    assert "data-metadata-copy" in body
    assert "data-metadata-expand-all" in body
    assert "data-metadata-collapse-all" in body
    # ARIA contract.
    assert 'role="complementary"' in body
    # Transaction id surfaces in the header.
    assert "txn-200" in body


def test_route_empty_metadata_renders_empty_state_no_toolbar(
    metadata_dashboard_fixture: tuple[Any, str, str, str, str],
) -> None:
    """Empty / null metadata → operator-locked empty-state fragment.
    No toolbar (no Copy / no expand-all / no collapse-all)."""
    app, dash_id, sheet_id, _, _ = metadata_dashboard_fixture
    expected_empty = (
        '<p class="text-secondary-fg italic">No metadata for this row.</p>'
    )
    with TestClient(app) as c:  # type: ignore[arg-type]
        # 1. metadata param missing entirely.
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
        )
        assert resp.status_code == 200
        assert resp.text.strip() == expected_empty
        # 2. metadata = "" (empty string).
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={"metadata": ""},
        )
        assert resp.status_code == 200
        assert resp.text.strip() == expected_empty
        # 3. metadata = "null".
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={"metadata": "null"},
        )
        assert resp.status_code == 200
        assert resp.text.strip() == expected_empty
        # 4. metadata = "{}".
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={"metadata": "{}"},
        )
        assert resp.status_code == 200
        assert resp.text.strip() == expected_empty
        # 5. metadata = "[]".
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={"metadata": "[]"},
        )
        assert resp.status_code == 200
        assert resp.text.strip() == expected_empty


def test_route_404_for_unknown_dashboard(
    metadata_dashboard_fixture: tuple[Any, str, str, str, str],
) -> None:
    """Unknown dashboard_id → 404."""
    app, _, sheet_id, _, _ = metadata_dashboard_fixture
    with TestClient(app) as c:  # type: ignore[arg-type]
        resp = c.get(
            f"/dashboards/no-such-dash/sheets/{sheet_id}/rows/metadata",
            params={"metadata": "{}"},
        )
    assert resp.status_code == 404


def test_route_404_for_unknown_sheet(
    metadata_dashboard_fixture: tuple[Any, str, str, str, str],
) -> None:
    """Unknown sheet_id (for an existing dashboard) → 404."""
    app, dash_id, _, _, _ = metadata_dashboard_fixture
    with TestClient(app) as c:  # type: ignore[arg-type]
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/no-such-sheet/rows/metadata",
            params={"metadata": "{}"},
        )
    assert resp.status_code == 404


def test_route_404_when_sheet_table_lacks_metadata_popup(
    metadata_dashboard_fixture: tuple[Any, str, str, str, str],
) -> None:
    """Known sheet whose Table visual has ``metadata_popup=False`` → 404.
    Surfaces accidental wiring elsewhere as 404, not a silent 200."""
    app, _, _, dash_no, sheet_no = metadata_dashboard_fixture
    with TestClient(app) as c:  # type: ignore[arg-type]
        resp = c.get(
            f"/dashboards/{dash_no}/sheets/{sheet_no}/rows/metadata",
            params={"metadata": "{}"},
        )
    assert resp.status_code == 404


def test_route_500_on_malformed_metadata_json(
    metadata_dashboard_fixture: tuple[Any, str, str, str, str],
) -> None:
    """JSONDecodeError → 500 with an explicit 'metadata JSON parse
    failed' message. Defense in depth behind the DB IS-JSON constraint
    (per PLAN.md CY.5 operator lock 8 — no silent fallback)."""
    app, dash_id, sheet_id, _, _ = metadata_dashboard_fixture
    with TestClient(app) as c:  # type: ignore[arg-type]
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={"metadata": "{not valid json"},
        )
    assert resp.status_code == 500
    assert "metadata JSON parse failed" in resp.text


def test_route_typical_payload_carries_leaves_and_data_attrs(
    metadata_dashboard_fixture: tuple[Any, str, str, str, str],
) -> None:
    """End-to-end through the route: a typical row payload renders both
    the ``<details data-json-node>`` containers and the per-leaf
    ``<span data-json-leaf>`` annotations the JS tree walker keys on."""
    app, dash_id, sheet_id, _, _ = metadata_dashboard_fixture
    payload = {
        "trace_id": "abc-123",
        "is_settled": True,
        "amount": 12,
        "nullable": None,
    }
    metadata_json = json.dumps(payload)
    with TestClient(app) as c:  # type: ignore[arg-type]
        resp = c.get(
            f"/dashboards/{dash_id}/sheets/{sheet_id}/rows/metadata",
            params={
                "metadata": metadata_json,
                "transaction_id": "txn-leaves",
            },
        )
    body = resp.text
    assert resp.status_code == 200
    assert 'data-json-leaf' in body
    # JSON literal forms reach the rendered body (escaped quotes for
    # the string).
    assert '&quot;abc-123&quot;' in body
    assert ">true<" in body
    assert ">null<" in body
    assert ">12<" in body
