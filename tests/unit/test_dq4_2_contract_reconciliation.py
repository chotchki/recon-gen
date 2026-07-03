"""DQ.4.2 — every DatasetContract must RECONCILE with the emitted columns
of the matview(s) its dataset reads FROM.

The contract is a DOWNSTREAM (dataset) projection over one or more
matviews. This gate asserts that for every column the contract shares (by
name) with a source matview's ``DbObject.columns``, the annotations
reconcile under the DQ.4.2 rule (docs/audits/dq_4_column_map.md):

  STRICT (fails the build):
    - STORAGE — a contract money column must be DOLLARS (the dataset SQL
      pre-divides via cents_to_dollars_sql); a CENTS leak is the BG.7 100x
      render bug. Money source (CENTS) → contract DOLLARS is the allowed,
      documented widen.
    - coarse TYPE — equal, modulo the money widen INTEGER/CENTS →
      DECIMAL/DOLLARS.
  ADVISORY (enrichment-compatible, non-gating):
    - SHAPE — a contract MAY enrich a drill shape onto a None-source
      (Current*/base tables carry no drill shape; the reading contract
      adds ACCOUNT_ID / RAIL_NAME / DATETIME_DAY for its own wiring).
      Allowed iff source.shape is None, contract.shape is None, or
      source.shape.can_assign_to(contract.shape). A genuine cross-type
      error (rail_name tagged ACCOUNT_ID against a RAIL_NAME source) still
      fails.
    - CURRENCY — the house convention is currency=False on every contract
      money column (SQL owns $-formatting), so it's non-gating.

This validates the shape/currency/storage annotations in db_objects.py
that the DuckDB-introspection test (DQ.4.1) can't see. It does NOT gate a
matview RENAME — that's the contract_from `keep` import-resolution
mechanism (DQ.4.2 A / the literal-collapse), a follow-on.

Sources are DERIVED from each dataset's actual SQL (FROM/JOIN of a graph
object), so there's no parallel list to drift.
"""
from __future__ import annotations

import re

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import BuiltDataset, ColumnSpec, Storage
from recon_gen.common.db_objects import DbObject, SCHEMA_GRAPH
from recon_gen.common.l2 import default_l2_instance
from tests._test_helpers import make_test_config


def _all_built_datasets() -> tuple[list[BuiltDataset], Config]:
    from recon_gen.apps.executives.datasets import build_all_datasets as _exec
    from recon_gen.apps.investigation.datasets import build_all_datasets as _inv
    from recon_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets as _l1,
    )
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_all_l2_flow_tracing_datasets as _l2ft,
    )

    cfg = make_test_config()
    l2 = default_l2_instance()
    built = _l1(cfg, l2) + _l2ft(cfg, l2) + _inv(cfg, l2) + _exec(cfg)
    return built, cfg


_BY_ID: dict[str, DbObject] = {str(o.obj_id): o for o in SCHEMA_GRAPH.objects}


def _sources_in(sql: str, prefix: str) -> set[str]:
    """The graph-object ids the SQL reads FROM / JOINs (prefix-stripped)."""
    sources: set[str] = set()
    for m in re.finditer(rf"(?:FROM|JOIN)\s+{re.escape(prefix)}_(\w+)", sql):
        oid = m.group(1)
        if oid in _BY_ID:
            sources.add(oid)
    return sources


def _reconcile(src_col: ColumnSpec, contract_col: ColumnSpec) -> str | None:
    """None if the shared column reconciles, else a problem description."""
    src_money = src_col.currency and src_col.storage is Storage.CENTS
    if src_money:
        # SQL pre-divides: contract money must land DOLLARS, widened to DECIMAL.
        if contract_col.storage is not Storage.DOLLARS:
            return (
                f"storage {contract_col.storage.value} on a money column whose "
                f"source emits CENTS — a CENTS leak is the BG.7 100x render bug; "
                f"the dataset SQL must /100 and the contract land DOLLARS"
            )
        if contract_col.type not in ("DECIMAL", src_col.type):
            return f"type {contract_col.type} vs money source {src_col.type} (expected the DECIMAL widen)"
    else:
        if contract_col.storage is not src_col.storage:
            return f"storage {contract_col.storage.value} vs source {src_col.storage.value}"
        if contract_col.type != src_col.type:
            return f"type {contract_col.type} vs source {src_col.type}"
    # ADVISORY shape — enrichment allowed; a genuine cross-type tag is not.
    if contract_col.shape is not None and src_col.shape is not None:
        if not src_col.shape.can_assign_to(contract_col.shape):
            return f"shape {src_col.shape.name} can't assign to contract {contract_col.shape.name}"
    return None


def test_dq4_2_contracts_reconcile_with_source_matviews() -> None:
    built, cfg = _all_built_datasets()
    prefix = cfg.db.table_prefix
    problems: list[str] = []
    checked = 0
    for bd in built:
        sources = _sources_in(bd.sql, prefix)
        if not sources:
            continue  # base-only / pure-compute dataset — no matview to reconcile
        src_cols = {
            c.name: c
            for sid in sources
            for c in _BY_ID[sid].columns
        }
        for col in bd.contract.columns:
            src = src_cols.get(col.name)
            if src is None:
                continue  # dataset-computed column — not a shared/emitted col
            checked += 1
            issue = _reconcile(src, col)
            if issue is not None:
                problems.append(f"{bd.visual_identifier}.{col.name}: {issue}")

    # Floor guards against a silent regression in the source-parse / build
    # (263 shared columns across 57 sourced datasets at time of writing).
    assert checked >= 200, (
        f"only {checked} shared contract/matview columns checked (expected "
        f"~263) — the source parse or the dataset build regressed"
    )
    assert not problems, (
        "DatasetContract ↔ source-matview reconciliation failures (DQ.4.2):\n"
        + "\n".join(problems)
    )


def test_dq4_2_reconciliation_rule_catches_a_cents_leak() -> None:
    """Plant the BG.7 100x bug — a contract money column mis-tagged CENTS
    against a CENTS source — and prove the STRICT storage gate fires."""
    from dataclasses import replace

    drift = _BY_ID["drift"]
    src = next(c for c in drift.columns if c.name == "stored_balance")  # money, CENTS
    bad = replace(src, storage=Storage.CENTS)  # a contract that forgot to /100
    assert _reconcile(src, bad) is not None, (
        "the reconciliation gate failed to catch a CENTS-leak money column — "
        "the storage check is dead"
    )
    good = replace(src, type="DECIMAL", currency=False, storage=Storage.DOLLARS)
    assert _reconcile(src, good) is None, (
        "the reconciliation gate rejected the legitimate cents→dollars widen"
    )
