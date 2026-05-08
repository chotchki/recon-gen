"""Smoke tests for ``common.handbook.diagrams``.

Phase T (v8.1.0): every render_* helper now returns the **DOT source
string** (not pre-rendered SVG). The browser-side
``stylesheets/qs-graphviz-wasm.js`` shim renders client-side via
``@hpcc-js/wasm-graphviz``. These tests assert the DOT shape — node
declarations + edge syntax — so a renamed L2 primitive or a broken
builder surfaces here rather than at mkdocs-build time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quicksight_gen.common.handbook.diagrams import (
    render_conceptual,
    render_dataflow,
    render_l2_topology,
)
from quicksight_gen.common.l2.loader import load_instance


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"
_SASQUATCH_PR = _FIXTURES / "sasquatch_pr.yaml"


def _is_dot(source: str) -> bool:
    """A graphviz Digraph DOT string starts with ``digraph`` (or
    ``strict digraph``) and opens a `{` body block."""
    head = source.lstrip().split("{", 1)[0].lower()
    return "digraph" in head or "graph" in head


# -- L2-driven topology ------------------------------------------------------


class TestL2Topology:
    @pytest.mark.parametrize(
        "kind",
        ["accounts", "account_templates", "chains", "layered", "hierarchy"],
    )
    def test_renders_against_spec_example(self, kind: str):
        l2 = load_instance(_SPEC_EXAMPLE)
        dot = render_l2_topology(l2, kind)  # type: ignore[arg-type]: kind is parametrized str; Literal narrowing not inferrable
        assert _is_dot(dot), f"expected DOT source; got: {dot[:80]}"

    @pytest.mark.parametrize(
        "kind",
        ["accounts", "account_templates", "chains", "layered", "hierarchy"],
    )
    def test_renders_against_sasquatch_pr(self, kind: str):
        # Sasquatch is a richer fixture — exercises union role expressions
        # + XOR-grouped chain entries that spec_example doesn't have.
        l2 = load_instance(_SASQUATCH_PR)
        dot = render_l2_topology(l2, kind)  # type: ignore[arg-type]: kind is parametrized str; Literal narrowing not inferrable
        assert _is_dot(dot)

    def test_unknown_kind_raises(self):
        l2 = load_instance(_SPEC_EXAMPLE)
        with pytest.raises(ValueError, match="unknown topology kind"):
            render_l2_topology(l2, "bogus")  # type: ignore[arg-type]: deliberately invalid Literal value for the negative-path test

    def test_accounts_diagram_includes_account_names(self):
        l2 = load_instance(_SPEC_EXAMPLE)
        dot = render_l2_topology(l2, "accounts")
        # spec_example has Clearing Suspense, North Pool, South Pool —
        # each should appear in the DOT as a node label.
        for expected in ("Clearing Suspense", "North Pool", "South Pool"):
            assert expected in dot, f"missing account label: {expected}"

    def test_account_templates_diagram_includes_template_marker(self):
        # Templates render as ``role × N`` labels so the marker must
        # appear at least once in the DOT output for an L2 with templates.
        l2 = load_instance(_SASQUATCH_PR)
        dot = render_l2_topology(l2, "account_templates")
        assert "× N" in dot, (
            "account_templates diagram should mark templates with × N"
        )

    def test_transfer_template_diagram_renders_against_sasquatch_pr(self):
        # sasquatch_pr declares two TransferTemplates: InternalTransferCycle
        # (3 legs incl. one Variable closure) and MerchantSettlementCycle
        # (1 leg, TransferKey-grouped). Both should render without raising
        # and the DOT should mention the template name + at least one of
        # its leg rails.
        l2 = load_instance(_SASQUATCH_PR)
        for template in l2.transfer_templates:
            dot = render_l2_topology(
                l2, "transfer_template", name=str(template.name),
            )
            assert _is_dot(dot)
            assert str(template.name) in dot, (
                f"transfer_template diagram missing the template name "
                f"{template.name!r} in the DOT"
            )
            for leg in template.leg_rails:
                assert str(leg) in dot, (
                    f"transfer_template diagram for {template.name!r} "
                    f"missing leg-rail {leg!r}"
                )

    def test_transfer_template_diagram_requires_name(self):
        # Defensive: the dispatch arm should reject the missing-name
        # case with a clear error rather than silently rendering nothing.
        l2 = load_instance(_SASQUATCH_PR)
        with pytest.raises(ValueError, match="requires a name"):
            render_l2_topology(l2, "transfer_template")

    def test_transfer_template_diagram_unknown_name_raises(self):
        l2 = load_instance(_SASQUATCH_PR)
        with pytest.raises(ValueError, match="no TransferTemplate named"):
            render_l2_topology(
                l2, "transfer_template", name="DoesNotExist",
            )

    def test_diagrams_bundle_parallel_rails_per_direction(self):
        # Parallel rails sharing the same (src, dst) direction should
        # collapse into one labeled edge instead of N parallel lines.
        # Direction stays split (a Customer→External rail and an
        # External→Customer rail produce distinct edges).
        l2 = load_instance(_SASQUATCH_PR)
        dot = render_l2_topology(l2, "account_templates")
        # Count `->` edge declarations in the DOT source. Pre-bundle the
        # template diagram emitted ~9 edges; post-bundle ≤8 keeps the win
        # obvious without coupling to the exact rail topology.
        edge_count = dot.count(" -> ")
        assert edge_count <= 8, (
            f"Templates diagram emitted {edge_count} edges; expected ≤8 "
            f"after parallel-rail bundling.\n{dot}"
        )

    def test_account_templates_diagram_renders_singleton_cross_edges(self):
        # Regression guard: an earlier filter required BOTH ends of a
        # rail to be templates, which dropped every template ↔ singleton
        # rail (the common case) and left only SingleLegRail self-loops
        # on template nodes — a useless diagram.
        l2 = load_instance(_SASQUATCH_PR)
        dot = render_l2_topology(l2, "account_templates")
        assert "Cash Concentration Master" in dot, (
            "account_templates diagram dropped a template→singleton rail "
            "(ZBASweep). The diagram should render singleton endpoints "
            "for any template-touching rail, not only template→template."
        )

    def test_chains_diagram_renders_when_chains_present(self):
        l2 = load_instance(_SASQUATCH_PR)
        dot = render_l2_topology(l2, "chains")
        assert _is_dot(dot)

    def test_hierarchy_diagram_includes_template_marker(self):
        l2 = load_instance(_SASQUATCH_PR)
        dot = render_l2_topology(l2, "hierarchy")
        assert "× N" in dot, "hierarchy diagram should mark templates with × N"

    def test_hierarchy_template_edges_reach_their_parent(self):
        # Regression: the original ``tmpl::`` node-id prefix collided
        # with Graphviz's ``node:port`` syntax in edge endpoints.
        # Walk the DOT and assert each template's parent_role chain
        # produces a real edge whose tail is the template node, not
        # ``tmpl``.
        from quicksight_gen.common.handbook.diagrams import (
            _build_hierarchy_graph,
        )

        l2 = load_instance(_SASQUATCH_PR)
        dot = _build_hierarchy_graph(l2).source

        # Sasquatch has CustomerDDA → DDAControl among others. The
        # rendered DOT must contain that exact edge with the expected
        # template node id, NOT a port-syntax artifact like
        # ``tmpl:"":CustomerDDA``.
        assert "tmpl__CustomerDDA -> " in dot, (
            f"expected 'tmpl__CustomerDDA -> ...' edge in DOT; got:\n{dot}"
        )
        # And the broken form must NOT appear anywhere.
        assert ":CustomerDDA" not in dot, (
            f"DOT contains port-syntax artifact ':CustomerDDA' — node id "
            f"prefix is interacting with Graphviz port parsing again.\n{dot}"
        )


# -- Per-app dataflow --------------------------------------------------------


class TestDataflow:
    @pytest.mark.parametrize(
        "app",
        ["l1_dashboard", "l2_flow_tracing", "investigation", "executives"],
    )
    def test_renders_for_every_shipped_app(self, app: str):
        dot = render_dataflow(app)
        assert _is_dot(dot)

    def test_l1_dashboard_includes_known_dataset_identifiers(self):
        dot = render_dataflow("l1_dashboard")
        # Spot-check that *something* dataset-ish appears in the DOT.
        assert "drift" in dot.lower() or "transactions" in dot.lower()

    def test_unknown_app_raises_keyerror(self):
        with pytest.raises(KeyError):
            render_dataflow("not_a_real_app")


# -- Hand-authored conceptual ------------------------------------------------


class TestConceptual:
    def test_double_entry_renders(self):
        dot = render_conceptual("double-entry")
        assert _is_dot(dot)
        # Sanity check — the file's text should mention some node label.
        assert "Money" in dot or "money" in dot

    def test_unknown_diagram_raises_keyerror_with_catalog(self):
        with pytest.raises(KeyError, match="No conceptual diagram"):
            render_conceptual("not-a-real-diagram")
