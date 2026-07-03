# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false, reportArgumentType=false
"""EB.2 — the semantic-fingerprint proof cache key is SOUND.

The cache pins the CANONICALIZED SMT formula (+ z3 version + rlimit
budget) per obligation — the same "pin the meaning, not the bytes"
discipline as the semantic locks. For that to be a valid cache key the
canonicalization must be:

- **deterministic** — the same obligation fingerprints identically every
  run (else every run reads stale);
- **refactor-invariant** — a formula that's textually different but
  SEMANTICALLY identical (commutative reorder, associative renest,
  constant rename, duplicate conjunct) fingerprints the SAME (a
  no-op refactor of a residual stays proven, doesn't re-solve);
- **semantically sensitive** — a genuine change (a different comparison,
  a different constant) fingerprints DIFFERENTLY (reads stale, re-solves).

The one-sided-error guarantee the design leans on: a buggy canonicalizer
can only ever produce a spurious STALE (harmless — re-prove on-chain),
never a spurious fresh (which would skip a real re-proof). So the tests
that MUST hold are determinism + sensitivity; refactor-invariance is the
optimization, and its failure only costs a re-solve.

At the money family's ms-scale solve times the "skip re-solve on a cache
hit" optimization is a no-op (always solve — cheaper than persisting),
so nothing here persists; this proves the KEY is sound for when the
theorem set grows enough to want it, and shares the canonicalizer with
DS.7's lock discipline.
"""
from __future__ import annotations

import z3

from tests.prover.canon import fingerprint
from tests.prover.theorems import all_theorems

_OBLIGATIONS = all_theorems()


def test_fingerprint_is_deterministic() -> None:
    """Every obligation fingerprints identically on a re-compute."""
    for o in _OBLIGATIONS:
        assert fingerprint(o.assertions) == fingerprint(o.assertions), o.name


def test_fingerprint_is_refactor_invariant() -> None:
    """Semantically-identical, textually-different formulas → same key."""
    a, b, c = z3.Ints("a b c")
    # commutative reorder + associative renest + a duplicate conjunct.
    f1 = z3.And(a > 0, z3.And(b > 0, c > 0))
    f2 = z3.And(c > 0, b > 0, a > 0, b > 0)
    assert fingerprint([f1]) == fingerprint([f2])
    # constant RENAME is invisible (alpha-normalization).
    x, y = z3.Ints("x y")
    assert fingerprint([x + y == 3]) == fingerprint([a + b == 3])


def test_fingerprint_is_semantically_sensitive() -> None:
    """A genuine change flips the key (reads stale, re-solves)."""
    a = z3.Int("a")
    assert fingerprint([a > 0]) != fingerprint([a > 1])
    assert fingerprint([a > 0]) != fingerprint([a >= 0])
    b = z3.Int("b")
    assert fingerprint([a + b == 0]) != fingerprint([a - b == 0])


def test_fingerprint_collisions_are_benign() -> None:
    """Two obligations CAN share a fingerprint — and correctly do: the
    canonicalizer collapses semantically-identical formulas, so a
    balance-only residual's leg-inertness, supersession-idempotence and
    interference-freedom (all "adding X leaves min(balance,0) unchanged")
    canonicalize to ONE formula and would solve ONCE (the cache dedup
    working). What MUST hold for the cache to be sound: any obligations
    that share a fingerprint agree on their expected verdict — else one
    formula would owe two different proof outcomes."""
    from tests.prover.theorems import Obligation

    by_fp: dict[str, list[Obligation]] = {}
    for o in _OBLIGATIONS:
        by_fp.setdefault(fingerprint(o.assertions), []).append(o)
    for fp, group in by_fp.items():
        verdicts = {o.expected for o in group}
        assert len(verdicts) == 1, (
            f"fingerprint {fp} shared by obligations with DIFFERENT "
            f"verdicts: {[(o.name, o.expected) for o in group]} — the "
            f"cache would owe one formula two outcomes"
        )
