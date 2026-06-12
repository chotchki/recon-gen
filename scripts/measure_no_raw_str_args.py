"""Measure blast radius of a hypothetical `no-raw-str-args` lint.

Walks ``src/recon_gen/**`` and for every function/method parameter
annotated as a bare ``str`` (or ``str | None`` / ``Optional[str]``),
emits a hit. Groups by module, by parameter name, and prints sample
hits. Report-only — no asserts. Used to spec the BC.1 D8 family
extension before deciding ship-now vs whitelist vs defer.

Usage::

    .venv/bin/python scripts/measure_no_raw_str_args.py
"""
from __future__ import annotations

import ast
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src/recon_gen"


@dataclass(frozen=True)
class Hit:
    file: Path
    lineno: int
    func: str
    param: str
    annotation: str  # "str" or "str | None" / "Optional[str]"


def _annotation_str_shape(ann: ast.AST | None) -> str | None:
    """If ``ann`` is a bare-``str`` shape we'd flag, return a label;
    else None.

    Catches:
    - ``str`` (Name)
    - ``str | None`` (BinOp Union)
    - ``Optional[str]`` (Subscript of Optional)
    Does NOT catch ``list[str]`` / ``dict[str, X]`` / ``Mapping[str, X]``
    — those are container shapes where the str is structural, not policy-
    carrying.
    """
    if ann is None:
        return None
    if isinstance(ann, ast.Name) and ann.id == "str":
        return "str"
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        for side in (ann.left, ann.right):
            sub = _annotation_str_shape(side)
            if sub is not None:
                # Mark the union shape distinctly
                return f"{sub} | None" if sub == "str" else sub
        return None
    if isinstance(ann, ast.Subscript):
        outer = ann.value
        if isinstance(outer, ast.Name) and outer.id == "Optional":
            inner = _annotation_str_shape(ann.slice)
            if inner is not None:
                return "Optional[str]"
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, file: Path) -> None:
        self.file = file
        self.hits: list[Hit] = []
        self._func_stack: list[str] = []

    def _check_args(self, args: list[ast.arg], func_name: str) -> None:
        for arg in args:
            # Skip ``self`` / ``cls`` (no annotation, but be defensive)
            if arg.arg in ("self", "cls"):
                continue
            shape = _annotation_str_shape(arg.annotation)
            if shape is None:
                continue
            self.hits.append(Hit(
                file=self.file,
                lineno=arg.lineno,
                func=func_name,
                param=arg.arg,
                annotation=shape,
            ))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        full_name = ".".join(self._func_stack + [node.name])
        self._check_args(node.args.args, full_name)
        self._check_args(node.args.kwonlyargs, full_name)
        self._check_args(node.args.posonlyargs, full_name)
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        full_name = ".".join(self._func_stack + [node.name])
        self._check_args(node.args.args, full_name)
        self._check_args(node.args.kwonlyargs, full_name)
        self._check_args(node.args.posonlyargs, full_name)
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()


def collect(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        v = _Visitor(path)
        v.visit(tree)
        hits.extend(v.hits)
    return hits


def main() -> int:
    hits = collect(SRC_ROOT)
    print(f"Total hits: {len(hits)}")
    print()

    # Group by module (first 4 path components after src/recon_gen/)
    by_module: dict[str, int] = Counter()
    for h in hits:
        rel = h.file.relative_to(REPO_ROOT)
        # Bucket per parent dir relative to src/recon_gen/
        parts = rel.parts[2:]  # drop "src", "recon_gen"
        if len(parts) >= 2:
            bucket = "/".join(parts[:-1])
        else:
            bucket = "<root>"
        by_module[bucket] += 1

    print("Top 15 modules by hit count:")
    for mod, count in sorted(
        by_module.items(), key=lambda x: -x[1]
    )[:15]:
        print(f"  {count:5d}  {mod}")
    print()

    # Param-name distribution
    by_param: Counter[str] = Counter(h.param for h in hits)
    print("Top 30 parameter names:")
    for name, count in by_param.most_common(30):
        print(f"  {count:5d}  {name}")
    print()

    # Annotation shape breakdown
    by_shape: Counter[str] = Counter(h.annotation for h in hits)
    print("Annotation shapes:")
    for shape, count in by_shape.most_common():
        print(f"  {count:5d}  {shape}")
    print()

    # Sample: 10 worst-offender modules' first hit each
    print("Sample hits (first hit per top-10 module):")
    seen_modules: set[str] = set()
    for h in hits:
        rel = h.file.relative_to(REPO_ROOT)
        parts = rel.parts[2:]
        bucket = "/".join(parts[:-1]) if len(parts) >= 2 else "<root>"
        if bucket not in seen_modules and len(seen_modules) < 10:
            seen_modules.add(bucket)
            print(f"  {rel}:{h.lineno}  {h.func}({h.param}: {h.annotation})")

    print()
    # Sample: hits that look like enum-shaped (matching audit's targets)
    enum_shaped = {
        "status", "amount_direction", "direction", "scope",
        "origin", "transfer_type", "supersedes", "cadence",
        "kind", "subtype", "role_kind", "account_kind",
        "completion", "fan_in",
    }
    print("Enum-shaped hits (load-bearing per untyped_enum_audit):")
    enum_hits = [h for h in hits if h.param in enum_shaped]
    print(f"  Total: {len(enum_hits)}")
    by_enum_param: Counter[str] = Counter(h.param for h in enum_hits)
    for name, count in by_enum_param.most_common():
        print(f"  {count:5d}  {name}")
    print()
    print("First 15 enum-shaped hits in detail:")
    for h in enum_hits[:15]:
        rel = h.file.relative_to(REPO_ROOT)
        print(f"  {rel}:{h.lineno}  {h.func}({h.param}: {h.annotation})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
