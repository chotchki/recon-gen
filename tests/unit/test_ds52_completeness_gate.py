"""DS.5.2 — the completeness gate: every detector matview is annotated,
every annotation is emitted + refreshed, both directions, against the
EMITTED artifact (the ground truth).

The gate has two halves that together make silent absence unrepresentable:

- **AST half** — every ``class *Invariant`` under ``common/spine/``
  (bar the ``Invariant`` Protocol itself) carries ``@math_invariant``.
  You cannot add a detector CLASS and forget the annotation.
- **Emitted-walk half** — render ``emit_schema`` + ``refresh_matviews_sql``
  for a probe instance, collect the matview names, and assert the
  partition: ``emitted matviews == annotated ∪ plumbing`` EXACTLY, plus
  ``annotated ⊆ refreshed`` and ``emitted ⊆ refreshed``. You cannot
  emit a detector MATVIEW with no annotated class (the balance_cadence_gap
  canary — inventory row 14, silent for its whole life until DS.5.1),
  and you cannot annotate a matview that isn't emitted or isn't
  refreshed.

The plumbing exclusion list is the one maintained artifact — but it fails
SAFE: an emitted matview that is neither annotated nor listed breaks the
build with "annotate it or add it to PLUMBING with a reason." The default
(unlisted, unannotated) is a hard failure, which is the opposite of a
registry that silently omits. Emitted artifacts stay the ground truth;
the annotation is only ever CHECKED against them (operator, DS.0 —
"an annotation nothing cross-checks is a registry in another costume").
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Final

import pytest

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine.math_invariant import (
    MathInvariantSpec,
    math_invariant_spec,
)
from recon_gen.common.spine.registry import ALL_INVARIANTS
from recon_gen.common.spine.residuals import MathKind
from recon_gen.common.sql import Dialect

_REPO = Path(__file__).parent.parent.parent
_SPINE_DIR = _REPO / "src" / "recon_gen" / "common" / "spine"
_SPEC = _REPO / "tests" / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"

#: Emitted matviews that are NOT invariant detectors — plumbing the
#: partition subtracts before requiring an annotation. Each carries the
#: reason it is not a detector, so "why is this exempt" is never a
#: mystery. A NEW emitted matview absent from BOTH this list and the
#: annotations breaks the build (fail-safe): annotate it (it's a
#: detector) or add it here (it's plumbing) — never silence.
_PLUMBING: Final[dict[str, str]] = {
    "current_transactions": "supersession projection (highest entry per id)",
    "current_daily_balances": "supersession projection over balance claims",
    "effective_balances": "LOCF carry-forward intermediate for the money family",
    "computed_subledger_balance": "Σ legs per account-day — drift's compute side",
    "computed_ledger_balance": "Σ children per parent-day — ledger_drift's compute side",
    "transfer_parents": "fan_in / chain intermediate (materialized parent claims)",
    "data_anchor": "the as_of frame anchor row",
    "drift_summary": "dashboard rollup over drift (presentation, not a detector)",
    "daily_statement_summary": "audit-PDF per-account statement source",
    "l1_exceptions": "the union rollup ITSELF — reads the detectors, is not one",
}

_MATVIEW_RE: Final = re.compile(
    rf"CREATE\s+(?:MATERIALIZED\s+VIEW|TABLE)\s+"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?{_PREFIX}_(\w+)\s+AS",
    re.IGNORECASE,
)


def _emitted_matviews(sql: str) -> set[str]:
    """Matview suffixes from a rendered script. DuckDB matviews are
    ``CREATE TABLE ... AS SELECT``; PG/Oracle are ``CREATE MATERIALIZED
    VIEW ... AS`` — both matched. Plain base tables (``CREATE TABLE x (``)
    don't match the ``AS`` tail, so they're excluded by construction."""
    return set(_MATVIEW_RE.findall(sql))


def _instance_matviews(l2_path: Path) -> tuple[set[str], set[str]]:
    instance = load_instance(l2_path)
    schema = emit_schema(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB)
    refresh = refresh_matviews_sql(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB)
    return _emitted_matviews(schema), _emitted_matviews(refresh)


def _annotated_matviews() -> dict[str, MathInvariantSpec]:
    out: dict[str, MathInvariantSpec] = {}
    for inv in ALL_INVARIANTS:
        spec = math_invariant_spec(inv)
        assert spec is not None, f"{inv.__name__} lost its annotation"
        out[spec.matview] = spec
    return out


# ---------------------------------------------------------------------------
# AST half: every *Invariant class carries the decorator.


def _invariant_classdefs() -> list[tuple[Path, ast.ClassDef]]:
    out: list[tuple[Path, ast.ClassDef]] = []
    for path in sorted(_SPINE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Invariant"):
                out.append((path, node))
    return out


def _has_math_invariant_decorator(node: ast.ClassDef) -> bool:
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(target, ast.Name) and target.id == "math_invariant":
            return True
    return False


def test_every_invariant_class_carries_the_annotation() -> None:
    """AST enforcement: no ``class *Invariant`` in common/spine/ may
    exist without ``@math_invariant`` — except the ``Invariant``
    Protocol, which IS the contract, not an implementation of it."""
    offenders: list[str] = []
    seen_protocol = False
    for path, node in _invariant_classdefs():
        if node.name == "Invariant":
            seen_protocol = True
            assert not _has_math_invariant_decorator(node), (
                "the Invariant Protocol must not be annotated"
            )
            continue
        if not _has_math_invariant_decorator(node):
            offenders.append(f"{path.name}::{node.name}")
    assert seen_protocol, "the Invariant Protocol scan anchor vanished"
    assert not offenders, (
        "these *Invariant classes lack @math_invariant (a detector class "
        "with no definition-site annotation is exactly the silent gap the "
        f"gate exists to kill): {offenders}"
    )


# ---------------------------------------------------------------------------
# Emitted-walk half: the partition, both directions.


def test_emitted_matviews_partition_into_annotated_plus_plumbing() -> None:
    """The core completeness claim: the emitted matview set is EXACTLY
    the annotated detectors plus the named plumbing — no leftover, no
    missing. A new detector matview with no annotation lands in the
    leftover (build breaks); a new plumbing matview must be named."""
    emitted, _ = _instance_matviews(_SPEC)
    annotated = set(_annotated_matviews())
    plumbing = set(_PLUMBING)

    unannotated = emitted - annotated - plumbing
    assert not unannotated, (
        f"emitted matviews with no annotation and not on the plumbing "
        f"list: {sorted(unannotated)}. Each is either a DETECTOR (add "
        f"@math_invariant to its Invariant class) or PLUMBING (add it to "
        f"_PLUMBING with the reason it is not a detector). This is the "
        f"balance_cadence_gap canary — an emitted matview outside every "
        f"cross-check."
    )
    plumbing_not_emitted = plumbing - emitted
    assert not plumbing_not_emitted, (
        f"plumbing entries that are no longer emitted (stale exclusion — "
        f"drop them from _PLUMBING): {sorted(plumbing_not_emitted)}"
    )
    # The partition is exact: annotated ∪ plumbing covers every emitted
    # matview, and (by the two asserts above) nothing extra.
    assert emitted == annotated | plumbing, sorted(
        emitted ^ (annotated | plumbing),
    )


def test_every_annotation_is_emitted_and_refreshed() -> None:
    """Direction 1: every annotation's matview is actually emitted AND
    in the refresh list. An annotation pointing at a renamed / dropped
    matview, or one emitted but never refreshed (→ silently stale),
    fails here."""
    emitted, refreshed = _instance_matviews(_SPEC)
    annotated = set(_annotated_matviews())
    missing_emit = annotated - emitted
    assert not missing_emit, (
        f"annotated matviews that emit_schema does not create: "
        f"{sorted(missing_emit)}"
    )
    missing_refresh = annotated - refreshed
    assert not missing_refresh, (
        f"annotated matviews that refresh_matviews_sql does not refresh "
        f"(they would silently lag the feed): {sorted(missing_refresh)}"
    )


def test_refresh_covers_every_emitted_matview() -> None:
    """refresh-names ⊇ emitted matviews — no emitted matview escapes the
    refresh pass. A matview created but never refreshed reports stale
    data forever; this catches it for plumbing AND detectors alike."""
    emitted, refreshed = _instance_matviews(_SPEC)
    unrefreshed = emitted - refreshed
    assert not unrefreshed, (
        f"emitted matviews absent from the refresh pass (they will lag "
        f"the source data): {sorted(unrefreshed)}"
    )


def test_matview_name_set_is_instance_independent() -> None:
    """The emitted matview NAMES don't depend on L2 content (only the
    data does), so the completeness partition proven on spec_example
    holds for every instance. Cross-check against sasquatch_pr."""
    spec_emitted, _ = _instance_matviews(_SPEC)
    other = _REPO / "tests" / "l2" / "sasquatch_pr.yaml"
    other_emitted, _ = _instance_matviews(other)
    assert spec_emitted == other_emitted, sorted(
        spec_emitted ^ other_emitted,
    )


# ---------------------------------------------------------------------------
# The annotation payload is internally consistent with the KATs.


def test_gate_is_born_red_on_the_pre_ds51_orphan() -> None:
    """The canary has teeth: reconstruct the pre-DS.5.1 state (the
    balance_cadence_gap matview emitted, but NEITHER an annotated
    detector NOR named plumbing — a true orphan) and confirm the
    partition flags exactly it. This is the failure DS.5 was built to
    make impossible; it stays here as a regression so the gate can
    never quietly lose its ability to detect the next orphan."""
    emitted, _ = _instance_matviews(_SPEC)
    annotated_pre = set(_annotated_matviews()) - {"balance_cadence_gap"}
    plumbing = set(_PLUMBING)
    leftover = emitted - annotated_pre - plumbing
    assert leftover == {"balance_cadence_gap"}, (
        f"the pre-DS.5.1 partition should flag balance_cadence_gap as the "
        f"lone orphan; got {sorted(leftover)}"
    )


@pytest.mark.parametrize(
    "spec",
    list(_annotated_matviews().values()),
    ids=[s.matview for s in _annotated_matviews().values()],
)
def test_annotation_kat_matches_its_declared_kind(spec: MathInvariantSpec) -> None:
    """Each non-PROBABILISTIC annotation's KAT file exists and declares
    the SAME MathKind the annotation does — the law reference and its
    vectors can't disagree on what family the invariant is."""
    if spec.kind is MathKind.PROBABILISTIC:
        assert spec.kat_file is None and spec.residual is None
        assert spec.note, "PROBABILISTIC must name its contract"
        return
    assert spec.kat_file is not None
    kat_path = _REPO / spec.kat_file
    assert kat_path.exists(), f"{spec.matview}: KAT file {spec.kat_file} missing"
    payload = json.loads(kat_path.read_text())
    assert payload["kind"] == spec.kind.name, (
        f"{spec.matview}: annotation kind {spec.kind.name} != KAT kind "
        f"{payload['kind']}"
    )
    assert payload["vectors"], f"{spec.matview}: KAT file has no vectors"
