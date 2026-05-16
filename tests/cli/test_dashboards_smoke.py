"""CLI smoke for ``quicksight-gen dashboards`` — registration + help only.

X.4.a.2 acceptance net (renamed from the X.2.a.3-era ``test_serve_smoke``):
asserts ``dashboards`` is registered on ``main``, ``dashboards --help``
lists every CLI option (``--config`` / ``--l2`` / ``--host`` / ``--port``
/ ``--dev-log`` / ``--app`` / ``--stub`` / ``--docs``), and the smoke-app
builder + stub fetcher round-trip cleanly without uvicorn binding a
port.

We don't actually start the server in unit tests — that's covered
by the layer-2 e2e harness. The point here is to lock the CLI surface
so adding new options / renaming sub-apps trips a fast unit-level test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from quicksight_gen.cli import main
from quicksight_gen.common.html._smoke_app import (
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html.render import emit_html


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


@pytest.fixture
def min_config(tmp_path: Path) -> Path:
    """Minimal config.yaml — emit/registration paths don't need a DB."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: qsgen-test\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
    )
    return cfg


def test_dashboards_command_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "dashboards" in result.output, (
        f"main --help did not list 'dashboards':\n{result.output}"
    )


def test_dashboards_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["dashboards", "--help"])
    assert result.exit_code == 0, result.output
    for opt in (
        "--config", "--l2", "--host", "--port", "--dev-log", "--app",
        "--stub", "--docs",
    ):
        assert opt in result.output, (
            f"dashboards --help missing {opt!r}:\n{result.output}"
        )
    # The default is ``all`` (build the four real apps into one server,
    # same "no-arg = all" shape as ``json apply``) — not ``smoke``.
    assert "[default: all]" in result.output, result.output


def test_smoke_app_builder_emits_html(min_config: Path) -> None:
    """X.2.a.3 wiring proof, kept under the X.4.a.2 rename: the same
    builder the CLI uses produces a Sheet that emit_html accepts.
    Catches a regression where the Sheet isn't a member of the App's
    analysis (the auto-ID-resolution path in emit_html raises ValueError).
    """
    from quicksight_gen.common.config import load_config

    cfg = load_config(str(min_config))
    tree_app, sheet = build_smoke_app(cfg)
    html = emit_html(tree_app, sheet, dashboard_id="smoke")
    assert "Money Trail" in html
    assert "smoke-sankey" in html
    assert "smoke-force" in html


def test_stub_fetcher_returns_sankey_shape_for_default_visual() -> None:
    data = stub_money_trail_fetcher(
        "smoke-sankey", {"date_from": ["2026-01-01"], "date_to": ["2026-05-01"]},
    )
    assert "nodes" in data and "links" in data
    assert len(data["nodes"]) == 5
    assert len(data["links"]) == 4


def test_stub_fetcher_returns_topology_for_force_visual() -> None:
    """Visual-id-keyed branching: smoke-force returns the
    rails/accounts topology shape, not the Sankey shape."""
    data = stub_money_trail_fetcher("smoke-force", {})
    assert "nodes" in data and "links" in data
    # All nodes carry id+label+group (topology), not name (Sankey).
    for node in data["nodes"]:
        assert "id" in node and "label" in node and "group" in node
