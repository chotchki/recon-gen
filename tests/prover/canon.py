"""canonical_dump — deterministic byte canonicalization of a z3 assertion set.

The semantic-fingerprint proof cache (DST.2 per docs/audits/ds_z3_formal_tie_spike.md)
pins "the canonicalized SMT formula each obligation symbolically executes to".
This module is the proof-out of that pin: canonical form = alpha-normalized
constant names + AC-normalized argument order + sorted top-level assertions,
emitted as SMT-LIB2 text.

What it normalizes (refactor-stable across):
  * constant NAMES (any z3 constant renaming — alpha-normalization)
  * top-level assertion ORDER (sorted by name-blind canonical key)
  * argument order inside commutative ops (and/or/+/*/xor/=/distinct)
  * nesting of associative-commutative ops (nested and/or/+/*/xor flattened)
  * duplicate conjuncts/disjuncts (and/or args deduped — idempotent)

What it does NOT normalize (a rewrite of these reads as a CHANGED formula ->
spurious STALE -> harmless re-prove, per the design's one-sided-error stance):
  * comparison mirroring (x >= 0 vs 0 <= x), <= 2 vs < 3, x != y vs Not(x == y)
  * any genuine algebraic rewrite (distributed sums, pushed negations, ...)
  * assertion granularity (one And-of-all vs many small asserts)

Alpha-normalization strategy: z3 constant names never reach the output.
Constants get colors by iterated refinement (Weisfeiler-Leman style: color =
hash of sort, then repeatedly hash in the multiset of "how do the assertions
look with me marked") until the partition stabilizes; residual ties (structurally
symmetric constants, e.g. interchangeable row variables) are broken by
individualize-and-refine. Final names c000, c001, ... assigned in first-
occurrence order of the canonically-sorted, canonically-arg-ordered emission
walk. Symmetric-tie individualization picks by input first-occurrence order —
sound (identical output bytes) whenever the tied constants are true
automorphisms of the assertion set, which the permutation/rename self-test in
prove_props.py exercises empirically.

Trusted base note: no z3 .sexpr() is used for anything name-bearing (its let-
abbreviation counters a!N and declare-fun ordering are traversal-dependent);
.sexpr() only renders leaf numerals/true/false, which are stable.
"""
# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportArgumentType=false
from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable

import z3

sys.setrecursionlimit(1_000_000)

# Associative+commutative: flatten nested same-op then sort args.
_AC_KINDS = {z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_ADD, z3.Z3_OP_MUL, z3.Z3_OP_XOR}
# Commutative only (fixed arity / not associative-flattenable): sort args.
_SORT_KINDS = {z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT}
# Idempotent: safe to dedupe args (NOT +, *, xor — parity/multiplicity matter).
_DEDUPE_KINDS = {z3.Z3_OP_AND, z3.Z3_OP_OR}
_OP_TOKEN = {
    z3.Z3_OP_AND: "and", z3.Z3_OP_OR: "or", z3.Z3_OP_NOT: "not",
    z3.Z3_OP_IMPLIES: "=>", z3.Z3_OP_ITE: "ite", z3.Z3_OP_EQ: "=",
    z3.Z3_OP_DISTINCT: "distinct", z3.Z3_OP_ADD: "+", z3.Z3_OP_SUB: "-",
    z3.Z3_OP_MUL: "*", z3.Z3_OP_UMINUS: "-", z3.Z3_OP_IDIV: "div",
    z3.Z3_OP_MOD: "mod", z3.Z3_OP_LE: "<=", z3.Z3_OP_LT: "<",
    z3.Z3_OP_GE: ">=", z3.Z3_OP_GT: ">", z3.Z3_OP_XOR: "xor",
}


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _is_uconst(e: z3.ExprRef) -> bool:
    return (z3.is_app(e) and e.num_args() == 0
            and e.decl().kind() == z3.Z3_OP_UNINTERPRETED)


def _flatten(e: z3.ExprRef, kind: int) -> list[z3.ExprRef]:
    out: list[z3.ExprRef] = []
    for c in e.children():
        if z3.is_app(c) and c.decl().kind() == kind:
            out.extend(_flatten(c, kind))
        else:
            out.append(c)
    return out


def _render(e: z3.ExprRef, name_of: Callable[[z3.ExprRef], str],
            memo: dict[int, str],
            shortcut: Callable[[z3.ExprRef], str | None] | None = None) -> str:
    """Canonical text of `e` with constants rendered via name_of.
    Deterministic: no .sexpr() except for leaf numerals/true/false.
    `shortcut` (optional) may fully render a subtree (used by the marked-render
    fast path: subtrees not containing the marked constant reuse the shared
    color render instead of re-walking)."""
    key = e.get_id()
    hit = memo.get(key)
    if hit is not None:
        return hit
    if shortcut is not None:
        s = shortcut(e)
        if s is not None:
            memo[key] = s
            return s
    if z3.is_var(e):  # de Bruijn bound var (quantifier bodies) — already canonical
        s = f"(:var {z3.get_var_index(e)})"
    elif z3.is_quantifier(e):
        q = "forall" if e.is_forall() else ("exists" if e.is_exists() else "lambda")
        sorts = " ".join(e.var_sort(i).sexpr() for i in range(e.num_vars()))
        s = f"({q} ({sorts}) {_render(e.body(), name_of, memo, shortcut)})"
    else:
        d = e.decl()
        kind = d.kind()
        if e.num_args() == 0:
            s = name_of(e) if kind == z3.Z3_OP_UNINTERPRETED else e.sexpr()
        else:
            kids = _flatten(e, kind) if kind in _AC_KINDS else list(e.children())
            rk = [_render(c, name_of, memo, shortcut) for c in kids]
            if kind in _AC_KINDS or kind in _SORT_KINDS:
                rk = sorted(set(rk)) if kind in _DEDUPE_KINDS else sorted(rk)
            if len(rk) == 1 and kind in _AC_KINDS:
                s = rk[0]  # unary and/or/+ after flatten+dedupe collapses
            else:
                s = f"({_OP_TOKEN.get(kind, d.name())} {' '.join(rk)})"
    memo[key] = s
    return s


def _const_masks(asserts: list[z3.ExprRef],
                 bit: dict[int, int]) -> dict[int, int]:
    """Per-node bitmask of which uninterpreted constants occur beneath it.
    Lets the marked render skip (reuse the clean render for) every subtree
    that doesn't contain the marked constant — the dominant cost otherwise."""
    mask: dict[int, int] = {}
    for root in asserts:
        stack: list[tuple[z3.ExprRef, bool]] = [(root, False)]
        while stack:
            n, done = stack.pop()
            nid = n.get_id()
            if nid in mask:
                continue
            if not done:
                stack.append((n, True))
                if z3.is_quantifier(n):
                    stack.append((n.body(), False))
                elif z3.is_app(n):
                    for c in n.children():
                        stack.append((c, False))
            else:
                if z3.is_quantifier(n):
                    m = mask[n.body().get_id()]
                elif _is_uconst(n):
                    m = 1 << bit[nid]
                elif z3.is_app(n):
                    m = 0
                    for c in n.children():
                        m |= mask[c.get_id()]
                else:
                    m = 0  # de Bruijn var
                mask[nid] = m
    return mask


def _walk_consts(e: z3.ExprRef, seen: set[int], out: dict[int, z3.ExprRef]) -> None:
    stack = [e]
    while stack:
        n = stack.pop()
        nid = n.get_id()
        if nid in seen:
            continue
        seen.add(nid)
        if z3.is_quantifier(n):
            stack.append(n.body())
            continue
        if _is_uconst(n):
            out[nid] = n
            continue
        if z3.is_app(n):
            # reversed so first-occurrence order matches left-to-right AST order
            stack.extend(reversed(n.children()))


def canonical_dump(obj: z3.Solver | Iterable[z3.BoolRef]) -> bytes:
    """Deterministic SMT-LIB2 byte string for a solver or assertion iterable."""
    if isinstance(obj, z3.Solver):
        raw = list(obj.assertions())
    else:
        raw = list(obj)
    # dedupe by ast id (z3 hash-conses: structurally identical => same id)
    asserts: list[z3.ExprRef] = []
    seen_a: set[int] = set()
    for a in raw:
        if a.get_id() not in seen_a:
            seen_a.add(a.get_id())
            asserts.append(a)

    # constants + input first-occurrence order + containment map
    consts: dict[int, z3.ExprRef] = {}
    seen_nodes: set[int] = set()
    containing: dict[int, list[int]] = defaultdict(list)
    for ai, a in enumerate(asserts):
        local: dict[int, z3.ExprRef] = {}
        _walk_consts(a, set(), local)
        for cid in local:
            containing[cid].append(ai)
        _walk_consts(a, seen_nodes, consts)  # consts accumulates in input order
    input_order = {cid: i for i, cid in enumerate(consts)}
    bit = {cid: i for i, cid in enumerate(consts)}
    mask = _const_masks(asserts, bit)

    color: dict[int, str] = {cid: _h("sort|" + c.sort().sexpr())
                             for cid, c in consts.items()}

    def color_name(e: z3.ExprRef) -> str:
        return f"«{color[e.get_id()]}»"

    def partition() -> frozenset[frozenset[int]]:
        g: dict[str, list[int]] = defaultdict(list)
        for cid, col in color.items():
            g[col].append(cid)
        return frozenset(frozenset(v) for v in g.values())

    def refine() -> None:
        while True:
            before = partition()
            ambiguous = [cid for cls in before if len(cls) > 1 for cid in cls]
            if not ambiguous:
                return
            clean_memo: dict[int, str] = {}  # per-round shared color render
            new: dict[int, str] = {}
            for cid in ambiguous:
                cbit = 1 << bit[cid]

                def marked(e: z3.ExprRef, _cid: int = cid) -> str:
                    return "«!»" if e.get_id() == _cid else color_name(e)

                def skip(e: z3.ExprRef, _cbit: int = cbit) -> str | None:
                    if mask.get(e.get_id(), 0) & _cbit:
                        return None  # contains the mark — walk it
                    return _render(e, color_name, clean_memo)

                sig = sorted(_render(asserts[ai], marked, {}, shortcut=skip)
                             for ai in containing[cid])
                new[cid] = _h(color[cid] + "\x01" + "\x00".join(sig))
            color.update(new)
            if partition() == before:
                return  # stable partition: symmetric ties remain

    refine()
    # individualize residual symmetric ties (see module docstring for soundness)
    while True:
        tied = [sorted(cls, key=lambda cid: input_order[cid])
                for cls in partition() if len(cls) > 1]
        if not tied:
            break
        cls = min(tied, key=lambda ids: color[ids[0]])
        color[cls[0]] = _h("indiv|" + color[cls[0]])
        refine()

    # canonical assertion order: sort by name-blind (color) render
    ckey_memo: dict[int, str] = {}
    ordered = sorted(asserts, key=lambda a: _render(a, color_name, ckey_memo))

    # final naming: first occurrence in the canonical emission walk
    names: dict[int, str] = {}

    def final_name(e: z3.ExprRef) -> str:
        cid = e.get_id()
        if cid not in names:
            names[cid] = f"c{len(names):03d}"
        return names[cid]

    def emit(e: z3.ExprRef, memo: dict[int, str]) -> str:
        key = e.get_id()
        hit = memo.get(key)
        if hit is not None:
            return hit
        if z3.is_var(e):
            s = f"(:var {z3.get_var_index(e)})"
        elif z3.is_quantifier(e):
            q = "forall" if e.is_forall() else ("exists" if e.is_exists() else "lambda")
            sorts = " ".join(e.var_sort(i).sexpr() for i in range(e.num_vars()))
            s = f"({q} ({sorts}) {emit(e.body(), memo)})"
        else:
            d = e.decl()
            kind = d.kind()
            if e.num_args() == 0:
                s = final_name(e) if kind == z3.Z3_OP_UNINTERPRETED else e.sexpr()
            else:
                kids = _flatten(e, kind) if kind in _AC_KINDS else list(e.children())
                if kind in _AC_KINDS or kind in _SORT_KINDS:
                    # order args by the name-blind key; dedupe by ast id where safe
                    if kind in _DEDUPE_KINDS:
                        uniq: dict[int, z3.ExprRef] = {}
                        for c in kids:
                            uniq.setdefault(c.get_id(), c)
                        kids = list(uniq.values())
                    kids = sorted(kids, key=lambda c: _render(c, color_name, ckey_memo))
                rk = [emit(c, memo) for c in kids]
                if len(rk) == 1 and kind in _AC_KINDS:
                    s = rk[0]
                else:
                    s = f"({_OP_TOKEN.get(kind, d.name())} {' '.join(rk)})"
        memo[key] = s
        return s

    emit_memo: dict[int, str] = {}
    assert_lines = [f"(assert {emit(a, emit_memo)})" for a in ordered]
    decl_lines = [
        f"(declare-fun {names[cid]} () {consts[cid].sort().sexpr()})"
        for cid in sorted(names, key=lambda cid: names[cid])
    ]
    text = "; canonical-smt2 v1\n" + "\n".join(decl_lines + assert_lines) + "\n"
    return text.encode("utf-8")


def fingerprint(obj: z3.Solver | Iterable[z3.BoolRef]) -> str:
    """sha256 (16 hex chars) of the canonical dump — the cache key half that
    pins the formula; z3 version + rlimit budget are pinned alongside by the
    obligation table, per the design's fingerprint = formula + version + budget."""
    return hashlib.sha256(canonical_dump(obj)).hexdigest()[:16]
