"""DQ.5 — SQLGlot column-lineage lint (TEST-TIER ONLY).

Per the DQ.0 lock, SQLGlot is a dev/test dependency — never in the wheel,
never on the emit path. This module parses the DuckDB render of every
matview body + every dataset SQL against a ``MappingSchema`` built from the
``DbObject`` catalog and asserts:

- **DQ.5.2** — every column ref (SELECT / WHERE / JOIN / GROUP BY) resolves
  against the catalog. ``qualify(validate_qualify_columns=True)`` raises on
  the first unresolved ref — the zero-coverage surface before DQ.5 (the
  old regex guard only checked the SELECT projection, one direction).
- **DQ.5.4** — every matview's qualified output projection is EXACTLY its
  declared ``DbObject.columns`` (name + order). This is the folded DQ.4.3:
  a matview ``AS <col>`` alias renamed without updating the column
  declaration (or vice-versa) fails HERE, against the real parse — the
  CR.3/CR.16 "added it in 2 of 3 places" rename class. ``qualify`` expands
  ``SELECT *`` (the Current* views) against the schema so the star cases
  are covered too.

``pytest.importorskip`` gates the whole module: the layered chain (dev
extra installed) runs it; the wheel-smoke (prod deps only) skips it as an
optional dep — the same pattern as the starlette / graphviz tests, NOT a
POLICY-2 deferral (full coverage in the environment that gates merges).
"""
from __future__ import annotations

import re
from typing import Any

import pytest

sqlglot = pytest.importorskip("sqlglot")  # DQ.5: test-tier dep; wheel-smoke skips
from sqlglot import exp  # noqa: E402
from sqlglot.optimizer.qualify import qualify  # noqa: E402

# sqlglot ships partial type stubs — its AST nodes expose ``.selects`` /
# ``.alias_or_name`` / ``.copy()`` dynamically (unknown to pyright) and
# ``exp.Expression`` isn't re-exported. WHY Any: this module is the single
# thin boundary to that untyped surface; nodes flow through as ``Any`` and
# only ever become the typed ``list[str]`` of column names the asserts read.
_Node = Any

from recon_gen.common.config import Config  # noqa: E402
from recon_gen.common.dataset_contract import BuiltDataset  # noqa: E402
from recon_gen.common.db_objects import SCHEMA_GRAPH, DbObject  # noqa: E402
from recon_gen.common.l2 import default_l2_instance  # noqa: E402
from recon_gen.common.l2.schema import emit_schema  # noqa: E402
from recon_gen.common.sql import Dialect  # noqa: E402
from tests._test_helpers import make_test_config  # noqa: E402

_PREFIX = "test"

# Coarse DbObject column type → the DuckDB type sqlglot's optimizer reads.
# Only the column NAMES matter for resolution; types are cosmetic here but
# kept honest so the schema doubles as documentation.
_DUCK_TYPE = {
    "STRING": "VARCHAR",
    "INTEGER": "BIGINT",
    "DATETIME": "TIMESTAMP",
    "DECIMAL": "DOUBLE",
    "BIT": "BOOLEAN",
}

# App-info datasets that read a SYSTEM catalog (``information_schema``),
# not a graph object — a liveness canary counting tables / a matview
# row-count roll-up. There's no graph-column lineage to check, so they're
# explicitly OUT of the catalog-lineage scope. Asserted EXACT (DQ.5.3 "no
# silent caps"): a NEW dataset that reads a non-catalog table fails the
# scope check rather than dropping out quietly. ``information_schema`` isn't
# added to the schema because sqlglot's MappingSchema demands a single
# nesting level and the graph objects are flat (1-part) names.
_SYSTEM_CATALOG_READERS: frozenset[str] = frozenset({
    "l1-app-info-liveness-ds",
    "l2ft-app-info-liveness-ds",
    "inv-app-info-liveness-ds",
    "exec-app-info-liveness-ds",
})


def _catalog_schema() -> dict[str, object]:
    """The SQLGlot MappingSchema over the COMPLETE graph catalog — every
    object (base tables, ``v_config_*`` views, matviews) by physical name."""
    return {
        str(obj.physical_name(_PREFIX)): {
            col.name: _DUCK_TYPE.get(col.type, "VARCHAR") for col in obj.columns
        }
        for obj in SCHEMA_GRAPH.objects
    }


def _reads_only_system_catalog(sql: str) -> bool:
    """True when every table the SQL reads is a non-graph system catalog
    (``information_schema.*``) — nothing to lineage against the catalog."""
    tables = {
        t.name for t in sqlglot.parse_one(
            _normalize_params(sql), dialect="duckdb"
        ).find_all(exp.Table)
    }
    catalog = {str(o.physical_name(_PREFIX)) for o in SCHEMA_GRAPH.objects}
    return bool(tables) and tables.isdisjoint(catalog)


def _normalize_params(sql: str) -> str:
    """Read-only pre-pass: ``<<$pParam>>`` QS-CustomSql placeholders → NULL.

    Every ``<<$...>>`` sits in a value position (``col = <<$p>>``,
    ``CAST(<<$p>> AS ...)``, ``IN (<<$p>>)``), so replacing it with a NULL
    literal keeps the SQL parseable AND leaves every column reference intact
    for the resolver. Shipped bytes are untouched — this transforms only the
    in-memory copy the lint parses.
    """
    return re.sub(r"<<\$\w+>>", "NULL", sql)


def _matview_creates() -> list[tuple[str, _Node, DbObject]]:
    """(name, SELECT/UNION body, DbObject) for every matview CREATE in the
    DuckDB emit_schema render (matviews land as ``CREATE TABLE … AS``)."""
    sql = emit_schema(default_l2_instance(), prefix=_PREFIX, dialect=Dialect.DUCKDB)
    phys = {str(o.physical_name(_PREFIX)): o for o in SCHEMA_GRAPH.matviews()}
    out: list[tuple[str, _Node, DbObject]] = []
    for stmt in sqlglot.parse(sql, dialect="duckdb"):
        if not isinstance(stmt, exp.Create) or (stmt.kind or "").upper() != "TABLE":
            continue  # skip CREATE INDEX / SEQUENCE on the same names
        tbl = stmt.this.find(exp.Table) if stmt.this else None
        if tbl is None or tbl.name not in phys or stmt.expression is None:
            continue
        out.append((tbl.name, stmt.expression, phys[tbl.name]))
    return out


def _projection_names(qualified: _Node) -> list[str]:
    """The output column names of a qualified SELECT / UNION, in order
    (``.selects`` handles both — a set-op reports its left SELECT's aliases)."""
    return [str(proj.alias_or_name) for proj in qualified.selects]


def _all_built_datasets() -> list[BuiltDataset]:
    """Every built dataset across the 4 apps (prefix == ``_PREFIX``)."""
    from recon_gen.apps.executives.datasets import build_all_datasets as _exec
    from recon_gen.apps.investigation.datasets import build_all_datasets as _inv
    from recon_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets as _l1,
    )
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_all_l2_flow_tracing_datasets as _l2ft,
    )

    cfg: Config = make_test_config()
    l2 = default_l2_instance()
    return _l1(cfg, l2) + _l2ft(cfg, l2) + _inv(cfg, l2) + _exec(cfg)


def test_dq5_2_matview_bodies_resolve_against_the_catalog() -> None:
    """DQ.5.2 — every matview body's column refs (SELECT / WHERE / JOIN /
    GROUP BY) resolve against the catalog. A typo'd or renamed upstream
    column fails at the parse, not as a runtime 'column does not exist'."""
    schema = _catalog_schema()
    creates = _matview_creates()
    assert len(creates) == len(SCHEMA_GRAPH.matviews()), (
        f"only {len(creates)} matview CREATEs parsed from emit_schema; "
        f"expected {len(SCHEMA_GRAPH.matviews())} — the parse regressed"
    )
    problems: list[str] = []
    for name, body, _obj in creates:
        try:
            qualify(
                body.copy(), dialect="duckdb", schema=schema,
                validate_qualify_columns=True,
            )
        except Exception as exc:  # sqlglot OptimizeError + parse errors
            problems.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not problems, "matview bodies with unresolved column refs:\n" + "\n".join(
        problems
    )


def test_dq5_4_matview_projection_matches_declared_columns() -> None:
    """DQ.5.4 (folds DQ.4.3) — every matview's qualified output projection
    equals its declared ``DbObject.columns`` (name + order). Catches the
    'renamed the AS alias but not the column declaration (or vice-versa)'
    class against the REAL parse — including ``SELECT *`` cases, which
    qualify expands against the schema."""
    schema = _catalog_schema()
    problems: list[str] = []
    for name, body, obj in _matview_creates():
        qualified = qualify(
            body.copy(), dialect="duckdb", schema=schema,
            validate_qualify_columns=True,
        )
        emitted = _projection_names(qualified)
        declared = [c.name for c in obj.columns]
        if emitted != declared:
            problems.append(
                f"{name}:\n    parsed:   {emitted}\n    declared: {declared}"
            )
    assert not problems, (
        "matview projection ≠ declared DbObject.columns (DQ.5.4 lineage):\n"
        + "\n".join(problems)
    )


def test_dq5_2_dataset_sql_resolves_against_the_catalog() -> None:
    """DQ.5.2 — every dataset SQL (params normalized) resolves against the
    catalog. This is the surface the old regex projection guard never
    covered: WHERE / JOIN / GROUP-BY column refs, not just the SELECT.

    System-catalog readers (``information_schema``) are out of scope — the
    partition is asserted EXACT so a new graph-reading dataset can't fall
    out of coverage silently (DQ.5.3 no-silent-caps)."""
    schema = _catalog_schema()
    datasets = _all_built_datasets()
    out_of_scope = {
        bd.visual_identifier for bd in datasets
        if _reads_only_system_catalog(bd.sql)
    }
    assert out_of_scope == _SYSTEM_CATALOG_READERS, (
        "the set of datasets reading only a system catalog drifted — a new "
        "dataset reads a non-graph table (add it + confirm it's really "
        "system-only), or a canary changed id:\n"
        f"  now:      {sorted(out_of_scope)}\n"
        f"  expected: {sorted(_SYSTEM_CATALOG_READERS)}"
    )
    problems: list[str] = []
    for bd in datasets:
        if bd.visual_identifier in _SYSTEM_CATALOG_READERS:
            continue
        try:
            qualify(
                sqlglot.parse_one(_normalize_params(bd.sql), dialect="duckdb"),
                dialect="duckdb", schema=schema, validate_qualify_columns=True,
            )
        except Exception as exc:
            problems.append(f"{bd.visual_identifier}: {type(exc).__name__}: {exc}")
    assert not problems, "dataset SQL with unresolved column refs:\n" + "\n".join(
        problems
    )


def test_dq5_3_dataset_projection_matches_its_contract() -> None:
    """DQ.5.3 — every dataset's parsed SELECT projection is EXACTLY its
    ``DatasetContract`` columns (name + order). Replaces the CR.16 regex
    projection guard (``test_dataset_sql_contract_projection.py``) with a
    real parse: the regex only checked that each declared column APPEARED
    somewhere in the SELECT text (a superset, order-blind, alias-fooled);
    this is exact equality via ``qualify`` (which also expands ``*``), so a
    projection that drops / reorders / mis-aliases a contract column fails
    at the unit tier instead of ORA-00904 at query time."""
    schema = _catalog_schema()
    problems: list[str] = []
    for bd in _all_built_datasets():
        parsed = sqlglot.parse_one(_normalize_params(bd.sql), dialect="duckdb")
        # System-catalog readers can't qualify against the graph schema, but
        # their projections carry no ``*`` to expand — parse-only suffices.
        node = (
            parsed if bd.visual_identifier in _SYSTEM_CATALOG_READERS
            else qualify(
                parsed, dialect="duckdb", schema=schema,
                validate_qualify_columns=True,
            )
        )
        projected = _projection_names(node)
        declared = [c.name for c in bd.contract.columns]
        if projected != declared:
            problems.append(
                f"{bd.visual_identifier}:\n    projected: {projected}"
                f"\n    contract:  {declared}"
            )
    assert not problems, (
        "dataset SQL projection ≠ its DatasetContract (DQ.5.3):\n"
        + "\n".join(problems)
    )
