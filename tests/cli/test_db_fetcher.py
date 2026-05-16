"""X.2.a.4 — DB-backed DataFetcher unit tests.

Covers the three primitives in ``common/html/_db_fetcher.py``:

- ``_money_trail_to_sankey`` — pure shape converter (aggregation,
  index assignment, self-loop drop).
- ``_topology_to_force_graph`` — L2-instance projection (account
  nodes typed by scope, rail links per leg-role expression).
- ``make_db_fetcher`` — wiring + per-visual dispatch + the
  injected-connection escape hatch.

DB queries against a fake DB-API 2.0 cursor so the tests run
without psycopg2 / oracledb / a live database. The stub
connection records the SQL it was handed for assertions on the
date-filter clause + bound params.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from quicksight_gen.cli import main
from quicksight_gen.common.html._db_fetcher import (
    _money_trail_to_sankey,
    _topology_to_force_graph,
    make_db_fetcher,
)
from quicksight_gen.common.l2.loader import load_instance


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


# ---------------------------------------------------------------------------
# _money_trail_to_sankey
# ---------------------------------------------------------------------------


def test_sankey_assigns_indexes_in_first_seen_order() -> None:
    rows = [
        ("A", "B", 10.0),
        ("B", "C", 5.0),
        ("A", "D", 7.0),
    ]
    out = _money_trail_to_sankey(rows)
    assert [n["name"] for n in out["nodes"]] == ["A", "B", "C", "D"]
    # links keyed off the assigned indices
    by_pair = {(l["source"], l["target"]): l["value"] for l in out["links"]}
    assert by_pair[(0, 1)] == 10.0
    assert by_pair[(1, 2)] == 5.0
    assert by_pair[(0, 3)] == 7.0


def test_sankey_aggregates_duplicate_pairs() -> None:
    """Same (source, target) across multiple rows sums to one link."""
    rows = [
        ("A", "B", 10.0),
        ("A", "B", 25.0),
        ("A", "B", 5.5),
    ]
    out = _money_trail_to_sankey(rows)
    assert len(out["links"]) == 1
    assert out["links"][0]["value"] == 40.5


def test_sankey_drops_self_loops() -> None:
    """d3-sankey rejects self-edges; dropping them at the converter
    keeps the JS hydrator from crashing on noisy data."""
    rows = [
        ("A", "A", 100.0),
        ("A", "B", 50.0),
    ]
    out = _money_trail_to_sankey(rows)
    assert len(out["nodes"]) == 2
    assert len(out["links"]) == 1
    assert out["links"][0]["value"] == 50.0


def test_sankey_handles_empty_rows() -> None:
    """No matview data → empty graph, no error."""
    out = _money_trail_to_sankey([])
    assert out == {"nodes": [], "links": []}


# ---------------------------------------------------------------------------
# _topology_to_force_graph
# ---------------------------------------------------------------------------


def test_force_graph_nodes_carry_scope_as_group() -> None:
    instance = load_instance(str(_SPEC_EXAMPLE))
    out = _topology_to_force_graph(instance)
    assert out["nodes"], "expected at least one account node"
    for node in out["nodes"]:
        assert "id" in node
        assert "label" in node
        assert "group" in node
        # Scope serializes as 'internal' / 'external' / 'gl' — use as-is
        # for d3-force colouring.
        assert isinstance(node["group"], str) and node["group"]


def test_force_graph_links_reference_account_role_ids() -> None:
    """Every link endpoint must match an Account.role id, OR fall
    through to ``Account.id`` when role is unset. Unmatched ids
    would render as orphan nodes in d3-force — silently broken."""
    instance = load_instance(str(_SPEC_EXAMPLE))
    out = _topology_to_force_graph(instance)
    node_ids = {n["id"] for n in out["nodes"]}
    # AccountTemplates aren't projected as nodes (they're a class,
    # not a singleton), so rail legs that reference template roles
    # WILL produce orphan link endpoints. Capture template roles to
    # exclude from the assertion.
    template_roles = {str(t.role) for t in instance.account_templates}
    for link in out["links"]:
        for endpoint in (link["source"], link["target"]):
            if endpoint in template_roles:
                continue
            assert endpoint in node_ids, (
                f"link endpoint {endpoint!r} doesn't resolve to a "
                f"projected account node OR a known template role"
            )


# ---------------------------------------------------------------------------
# make_db_fetcher dispatch
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal DB-API 2.0 cursor stub recording execute() calls."""

    def __init__(self, rows: Sequence[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.executed_sql: str | None = None
        self.executed_params: list[Any] | None = None

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        self.executed_sql = sql
        self.executed_params = list(params) if params else []

    def fetchall(self) -> Sequence[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def cfg_with_prefix(tmp_path: Path):  # type: ignore[no-untyped-def]: returns Config with the Z.C deployment_name + db_table_prefix stamped on it
    from quicksight_gen.common.config import load_config

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: qsgen-test-inst\n"
        "db_table_prefix: test_inst\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
    )
    return load_config(str(cfg_file))


def test_db_fetcher_force_branch_skips_db(cfg_with_prefix) -> None:  # type: ignore[no-untyped-def]: cfg_with_prefix is the fixture above (Config)
    """The smoke-force path projects from the L2 instance — it must
    never open the connection (calling the factory would force the
    psycopg2 / oracledb import; the test must pass without them)."""
    instance = load_instance(str(_SPEC_EXAMPLE))
    factory_calls = 0

    def boom_factory() -> Any:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("force branch must not open a connection")

    fetcher = make_db_fetcher(
        cfg_with_prefix, instance, connection_factory=boom_factory,
    )
    out = fetcher("smoke-force", {})
    assert factory_calls == 0
    assert "nodes" in out and "links" in out


def test_db_fetcher_sankey_branch_runs_query(cfg_with_prefix) -> None:  # type: ignore[no-untyped-def]: cfg_with_prefix is the fixture above (Config)
    """smoke-sankey opens the connection, runs the SQL, and shapes
    the rows into d3-sankey form."""
    instance = load_instance(str(_SPEC_EXAMPLE))
    cursor = _FakeCursor([
        ("Customer DDA (acct-1)", "GL Control (gl-1)", 100.0),
        ("Customer DDA (acct-1)", "GL Control (gl-1)", 50.0),
        ("GL Control (gl-1)", "Concentration (conc-1)", 30.0),
    ])
    conn = _FakeConnection(cursor)

    fetcher = make_db_fetcher(
        cfg_with_prefix, instance, connection_factory=lambda: conn,
    )
    out = fetcher("smoke-sankey", {})
    assert conn.closed
    assert "test_inst_inv_money_trail_edges" in (cursor.executed_sql or "")
    assert len(out["nodes"]) == 3  # Customer DDA, GL Control, Concentration
    assert len(out["links"]) == 2  # CustomerDDA→GL summed, GL→Concentration


def test_db_fetcher_sankey_passes_date_filters_as_bound_params(  # type: ignore[no-untyped-def]: cfg_with_prefix is the fixture above (Config)
    cfg_with_prefix,
) -> None:
    """Date filters must go through bound params, NOT string
    interpolation — defends against SQL injection from the form."""
    instance = load_instance(str(_SPEC_EXAMPLE))
    cursor = _FakeCursor([])
    conn = _FakeConnection(cursor)
    fetcher = make_db_fetcher(
        cfg_with_prefix, instance, connection_factory=lambda: conn,
    )
    fetcher("smoke-sankey", {
        "date_from": ["2026-01-01"], "date_to": ["2026-12-31"],
    })
    sql = cursor.executed_sql or ""
    # The where clauses use placeholders, not literals.
    assert "posted_at >= %s" in sql
    assert "posted_at <= %s" in sql
    assert cursor.executed_params == ["2026-01-01", "2026-12-31"]


def test_db_fetcher_unknown_visual_id_raises(cfg_with_prefix) -> None:  # type: ignore[no-untyped-def]: cfg_with_prefix is the fixture above (Config)
    instance = load_instance(str(_SPEC_EXAMPLE))
    fetcher = make_db_fetcher(
        cfg_with_prefix, instance,
        connection_factory=lambda: _FakeConnection(_FakeCursor([])),
    )
    with pytest.raises(ValueError, match="no case for visual_id"):
        fetcher("not-a-visual", {})


# ---------------------------------------------------------------------------
# CLI --stub flag plumbing
# ---------------------------------------------------------------------------


def test_dashboards_help_lists_stub_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["dashboards", "--help"])
    assert result.exit_code == 0, result.output
    assert "--stub" in result.output
    assert "--no-stub" in result.output
