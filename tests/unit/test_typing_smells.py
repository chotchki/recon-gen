"""X.2.o.5 — custom AST lint catching typing smells pyright doesn't flag.

Four checks today, all extensible — drop a new ``Check`` into
``CHECKS`` and the runner picks it up:

- **bare-str-id** — function parameters named like ID identifiers
  (``visual_id``, ``sheet_id``, ``dashboard_id``, ``filter_group_id``,
  ``parameter_name``) annotated as bare ``str`` instead of the
  matching NewType wrapper from ``common/ids.py``. The X.2.o.3
  sweep wrapped these on the async path; the lint keeps them
  wrapped going forward.

- **explicit-any** — explicit ``Any`` in a type annotation (parameter,
  return, AnnAssign). Pyright doesn't have ``reportExplicitAny``
  (basedpyright only), so this fills the gap. ``Any`` is sometimes
  principled (DB drivers, JSON values, ``getattr`` dispatch); those
  sites suppress per-line with a one-line WHY.

- **envvar-bypass** (Y.2.gate.b.15.lint.envvar) — direct
  ``os.environ.get`` / ``os.environ[...]`` / ``os.getenv`` /
  ``monkeypatch.setenv`` / ``monkeypatch.delenv`` calls with a
  ``QS_GEN_*`` or ``QS_E2E_*`` string literal as the first arg.
  These bypass the typed ``EnvVar`` registry at
  ``common/env_keys.py``, defeating type coercion + value
  validation + the operator-facing ``EnvVarRequired`` /
  ``EnvVarInvalid`` errors. Whitelist: the registry itself + its
  unit test.

- **why-comment** (Y.2.gate.b.15.lint.why-comment) — bare
  ``# type: ignore`` / ``# pyright: ignore`` /
  ``# typing-smell: ignore`` suppressions without a one-line
  reason. Every suppression is a small assertion that the
  surrounding code is actually fine; future-you needs the WHY to
  decide whether the suppression still holds. Required form:
  ``# <kind>: ignore[<code>]: <reason — 3+ words after the colon>``.
  Escape hatch: append ``# bare-suppression-ok`` to the same line
  for the rare cases where the error code itself is the reason.

- **determinism** (Y.2.gate.b.15.lint.determinism) — calls to
  module-level random helpers (``random.random``, ``random.choice``,
  ``random.shuffle``, etc.) OR ``random.Random()`` with no seed in
  scope-restricted seed-generating modules
  (``common/l2/seed.py``, ``common/l2/auto_scenario.py``, ``apps/``).
  These use the unseeded global ``random`` state — different runs
  produce different output, breaking hash-locked seed contracts +
  fuzz-seed reproducibility. Use ``rng = random.Random(<seed>);
  rng.X(...)`` instead.

- **boto3-direct** (Y.2.gate.b.15.lint.boto3-direct) — direct
  ``boto3.client(...)`` calls outside the 4 known production
  wrappers (``common/deploy.py``, ``common/cleanup.py``,
  ``common/browser/helpers.py``, ``_dev/runner.py``). Stray clients
  bypass the ``ManagedBy: quicksight-gen`` tagging convention →
  break ``cleanup``. Tests can freely use ``boto3.client`` (scope
  is src/ only).

- **qs-gen-prefix** (Y.2.gate.b.15.lint.qs-gen-prefix) — hardcoded
  ``"qs-gen-..."`` string literals in src code outside
  ``common/config.py``. Resource IDs flow through
  ``cfg.prefixed(name)`` which weaves in the L2 instance prefix;
  bypassing it (``f"qs-gen-foo"`` direct) defeats multi-tenant
  scoping. Docstrings are ignored.

- **no-datetime-now** (Y.2.gate.b.15.lint.no-datetime-now) —
  ``datetime.now()`` / ``datetime.utcnow()`` / ``date.today()``
  outside the 4 allowlist files (``_dev/runner.py``,
  ``cli/audit/``, ``common/sheets/app_info.py``,
  ``common/provenance.py``). Determinism leak risk for any output
  that gets compared / hash-locked / diffed.

- **no-sleep** (Y.2.gate.b.15.lint.no-sleep) — ``time.sleep(...)``
  in ``tests/e2e/``. Use ``page.wait_for_function`` /
  ``wait_for_load_state`` polls instead — sleeps cause flakes by
  either being too short (race) or too long (slow CI runs).
  Allowlist the harness fixture's startup poll.

- **json-indent** (Y.2.gate.b.15.lint.json-indent) — bare
  ``json.dumps(obj)`` in ``cli/`` + ``common/`` requires either
  ``indent=`` (human-diffable file-emit) OR ``separators=``
  (compact format — deterministic byte-for-byte for cryptographic
  fingerprint / log lines / embedded HTML payloads). The smell is
  "no deliberate format choice"; either kwarg satisfies the lint.

Suppression
-----------

Per-line: append ``# typing-smell: ignore[<check-name>]`` to the
same line as the offending annotation. Multiple check names are
comma-separated::

    cur: Any = ...  # typing-smell: ignore[explicit-any]: psycopg sync cursor
    pool: Any  # typing-smell: ignore[explicit-any,bare-str-id]

Per-file: drop ``# typing-smell: ignore-file[<check-name>]`` on
its own line anywhere in the file. Use this when an entire file
opts out of a check (e.g. ``models.py`` keeps explicit Any in QS
JSON shape returns)::

    # typing-smell: ignore-file[explicit-any]

Adding a check
--------------

1. Subclass ``Check`` (override ``find_smells`` returning
   ``Iterable[Smell]``).
2. Append the instance to ``CHECKS`` with its scoped file paths.

Scope
-----

Each check picks its own scope. ``bare-str-id`` runs on the full
pyright strict include (whatever ``pyproject.toml`` declares).
``explicit-any`` runs on a tighter subset where we want zero
unprincipled ``Any`` — start with the freshest files (``db.py``,
``_sql_executor.py``, ``_tree_fetcher.py``, ``server.py``,
``config.py``) and grow as files get cleaned.
"""

from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# IDs we recognize as needing a NewType. Maps the snake-case
# parameter name to the matching NewType class name in common/ids.py.
ID_NEWTYPES: dict[str, str] = {
    "sheet_id": "SheetId",
    "visual_id": "VisualId",
    "filter_group_id": "FilterGroupId",
    "parameter_name": "ParameterName",
    "dashboard_id": "DashboardId",
}

_INLINE_IGNORE_RE = re.compile(
    r"#\s*typing-smell:\s*ignore\[([A-Za-z0-9_,\-\s]+)\]"
)
_FILE_IGNORE_RE = re.compile(
    r"#\s*typing-smell:\s*ignore-file\[([A-Za-z0-9_,\-\s]+)\]"
)


@dataclass(frozen=True)
class Smell:
    """One lint hit. Lineno is 1-based; checker name is the rule key."""
    file: Path
    lineno: int
    checker: str
    message: str


@dataclass
class Check:
    """One lint rule. Subclasses override ``find_smells``.

    Each Check declares its own scoped files (typically a subset of
    the pyright include list). The runner collects all smells then
    applies per-line + per-file suppression filtering.
    """
    name: str
    description: str
    files: list[Path] = field(default_factory=list)

    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Check: bare-str-id
# ---------------------------------------------------------------------------


class _BareStrIdVisitor(ast.NodeVisitor):
    """Walk function signatures, flag ID-named parameters typed as ``str``."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def _check_args(self, args: list[ast.arg]) -> None:
        for arg in args:
            if arg.annotation is None:
                continue
            ann = arg.annotation
            # Bare ``str`` annotation; or ``Optional[str]`` / ``str | None``
            # etc. — only flag the bare-str case to keep the rule tight.
            if isinstance(ann, ast.Name) and ann.id == "str":
                expected = ID_NEWTYPES.get(arg.arg)
                if expected is not None:
                    self.smells.append(Smell(
                        file=self.file,
                        lineno=arg.lineno,
                        checker="bare-str-id",
                        message=(
                            f"parameter {arg.arg!r} typed as bare ``str``; "
                            f"use ``{expected}`` from common.ids instead "
                            f"(or add ``# typing-smell: ignore[bare-str-id]`` "
                            f"with a one-line reason)"
                        ),
                    ))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_args(node.args.args)
        self._check_args(node.args.kwonlyargs)
        self._check_args(node.args.posonlyargs)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_args(node.args.args)
        self._check_args(node.args.kwonlyargs)
        self._check_args(node.args.posonlyargs)
        self.generic_visit(node)


class BareStrIdCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _BareStrIdVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: explicit-any
# ---------------------------------------------------------------------------


class _ExplicitAnyVisitor(ast.NodeVisitor):
    """Walk all type annotations, flag ``Any`` (Name or Attribute form)."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def _scan(self, ann: ast.AST | None) -> None:
        if ann is None:
            return
        for sub in ast.walk(ann):
            if isinstance(sub, ast.Name) and sub.id == "Any":
                self.smells.append(self._mk(sub.lineno))
            elif isinstance(sub, ast.Attribute) and sub.attr == "Any":
                self.smells.append(self._mk(sub.lineno))

    def _mk(self, lineno: int) -> Smell:
        return Smell(
            file=self.file,
            lineno=lineno,
            checker="explicit-any",
            message=(
                "explicit ``Any`` in annotation — replace with a real "
                "type or suppress with ``# typing-smell: ignore[explicit-any]`` "
                "and a one-line reason"
            ),
        )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._scan(node.annotation)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            self._scan(arg.annotation)
        self._scan(node.returns)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            self._scan(arg.annotation)
        self._scan(node.returns)
        self.generic_visit(node)


class ExplicitAnyCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _ExplicitAnyVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: envvar-bypass (Y.2.gate.b.15.lint.envvar)
# ---------------------------------------------------------------------------


# Names matching this pattern are owned by the env_keys.py registry.
# Anything matching that the lint encounters outside the whitelist is
# a bug going forward.
_ENV_VAR_NAME_RE = re.compile(r"^QS_(GEN|E2E)_[A-Z0-9_]+$")


def _is_qs_env_literal(node: ast.AST) -> str | None:
    """Return the env-var name if ``node`` is a ``Constant(str)`` matching
    the QS_GEN/QS_E2E pattern; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if _ENV_VAR_NAME_RE.match(node.value):
            return node.value
    return None


def _matches_attr_chain(node: ast.AST, *parts: str) -> bool:
    """True iff ``node`` is the attribute chain ``parts[0].parts[1]...``.

    Examples (with ``parts=("os", "environ", "get")``):
      - ``os.environ.get``  → True
      - ``other.environ.get`` → False
      - ``os.environ`` → False (chain too short)
    """
    if len(parts) < 2:
        return False
    cur: ast.AST = node
    for attr in reversed(parts[1:]):
        if not isinstance(cur, ast.Attribute) or cur.attr != attr:
            return False
        cur = cur.value
    return isinstance(cur, ast.Name) and cur.id == parts[0]


class _EnvVarBypassVisitor(ast.NodeVisitor):
    """Walk all Call + Subscript nodes; flag bare ``QS_*`` env access."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def _flag(self, lineno: int, name: str, shape: str) -> None:
        self.smells.append(Smell(
            file=self.file,
            lineno=lineno,
            checker="envvar-bypass",
            message=(
                f"bare {shape} access of {name!r} — use "
                f"``env_keys.{name}.get_or_none()`` (or ``.require()`` / "
                f"``.serialize(...)``) from common/env_keys.py instead. "
                f"The typed registry catches typos at import time, "
                f"validates values (paths exist, ints positive, ARNs "
                f"well-formed), and gives operator-actionable errors. "
                f"If this is genuinely a different env var that lives "
                f"outside the registry, add a ``# typing-smell: "
                f"ignore[envvar-bypass]`` with a one-line reason."
            ),
        ))

    def visit_Call(self, node: ast.Call) -> None:
        # os.environ.get(NAME, ...) / os.environ.setdefault(NAME, ...)
        if _matches_attr_chain(node.func, "os", "environ", "get") or \
                _matches_attr_chain(node.func, "os", "environ", "setdefault"):
            if node.args:
                name = _is_qs_env_literal(node.args[0])
                if name is not None:
                    self._flag(node.lineno, name, "os.environ.*()")
        # os.getenv(NAME, ...)
        elif _matches_attr_chain(node.func, "os", "getenv"):
            if node.args:
                name = _is_qs_env_literal(node.args[0])
                if name is not None:
                    self._flag(node.lineno, name, "os.getenv()")
        # monkeypatch.setenv(NAME, ...) / monkeypatch.delenv(NAME, ...)
        elif isinstance(node.func, ast.Attribute) and \
                node.func.attr in ("setenv", "delenv") and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id == "monkeypatch":
            if node.args:
                name = _is_qs_env_literal(node.args[0])
                if name is not None:
                    self._flag(
                        node.lineno, name,
                        f"monkeypatch.{node.func.attr}()",
                    )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # os.environ[NAME] (read) or os.environ[NAME] = ... (write)
        if isinstance(node.value, ast.Attribute) and \
                node.value.attr == "environ" and \
                isinstance(node.value.value, ast.Name) and \
                node.value.value.id == "os":
            # Subscript .slice on Python 3.9+ is the inner expression
            # directly (no ast.Index wrapper).
            name = _is_qs_env_literal(node.slice)
            if name is not None:
                self._flag(node.lineno, name, "os.environ[...]")
        self.generic_visit(node)


class EnvVarBypassCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _EnvVarBypassVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: why-comment (Y.2.gate.b.15.lint.why-comment)
# ---------------------------------------------------------------------------


# Match a suppression marker: ``# <kind>: ignore[code]`` or ``# <kind>: ignore``.
# Captures the optional ``[code]`` group + the trailing remainder of the line
# so we can scan the remainder for a ``: <3+ words>`` reason.
_SUPPRESSION_RE = re.compile(
    r"#\s*(?P<kind>type|pyright|typing-smell):\s*ignore"
    r"(?P<code>\[[^\]]+\])?(?P<rest>.*)$"
)

# A "reason" is a colon followed by 3+ whitespace-separated word-ish tokens
# anywhere in the trailing remainder of the line. Words are letter / digit /
# punctuation runs (what split() yields); we only care about count, not
# semantics. 3 keeps "yes." / "fine." / "x y" from passing.
_REASON_MIN_WORDS = 3

# Magic comment that opts a single line out of the why-comment check.
# Reserved for cases where the error code itself is self-explanatory (rare).
_BARE_OK_RE = re.compile(r"#\s*bare-suppression-ok\b")


def _has_reason(rest: str) -> bool:
    """True iff ``rest`` (text after ``ignore[code]``) contains a
    colon followed by ``_REASON_MIN_WORDS`` or more words.

    Accepts forms like::

        : psycopg sync cursor type
        : third-party library lacks PEP 561 stubs (X.2.o.5)

    Rejects bare ``]``-terminated suppressions and stub one-word reasons
    like ``: ok``.
    """
    idx = rest.find(":")
    if idx < 0:
        return False
    after = rest[idx + 1:].strip()
    # Drop any trailing ``# bare-suppression-ok`` or other comments on the
    # same line — they're not part of the reason text.
    if "#" in after:
        after = after.split("#", 1)[0].strip()
    if not after:
        return False
    # Count whitespace-separated tokens. Punctuation-only tokens still count
    # toward 3 — the rule is about there being prose, not about lexical
    # purity.
    return len(after.split()) >= _REASON_MIN_WORDS


class WhyCommentCheck(Check):
    """Comment-scan (NOT AST): every ``# *: ignore[*]`` needs a reason.

    Walks each file as text — suppression markers are comments, so AST
    walks miss them entirely. The unsuppressed-suppression test for
    suppressions; recursive but tractable.
    """

    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        out: list[Smell] = []
        for lineno, line in enumerate(src.splitlines(), start=1):
            m = _SUPPRESSION_RE.search(line)
            if m is None:
                continue
            # Per-line escape hatch: ``# bare-suppression-ok`` on the same
            # line opts out (sparingly — error code IS the reason).
            if _BARE_OK_RE.search(line):
                continue
            rest = m.group("rest") or ""
            if _has_reason(rest):
                continue
            kind = m.group("kind")
            code = m.group("code") or ""
            out.append(Smell(
                file=file,
                lineno=lineno,
                checker="why-comment",
                message=(
                    f"bare ``{kind}: ignore{code}`` with no WHY — append "
                    f"``: <3+ word reason>`` after the closing ``]`` "
                    f"explaining why the suppression is principled. "
                    f"For the rare case where the error code itself IS "
                    f"the reason, append ``# bare-suppression-ok`` to "
                    f"the same line."
                ),
            ))
        return out


# ---------------------------------------------------------------------------
# Check: determinism (Y.2.gate.b.15.lint.determinism)
# ---------------------------------------------------------------------------


# Module-level random helpers that pull from the unseeded global
# state. Using any of these in seed-generating code (where output
# must be deterministic across runs) is a silent breakage waiting
# to happen.
_RANDOM_NONDETERMINISTIC_FUNCS = frozenset({
    "random", "randint", "choice", "shuffle", "uniform", "sample",
    "randrange", "getrandbits", "choices", "triangular",
    "betavariate", "gauss", "normalvariate", "lognormvariate",
    "vonmisesvariate", "paretovariate", "weibullvariate",
    "expovariate", "gammavariate",
})


class _DeterminismVisitor(ast.NodeVisitor):
    """Walk Call nodes; flag bare ``random.X(...)`` and ``random.Random()``
    (no-arg) calls. Seeded forms (``random.Random(42)``,
    ``rng.choice(...)``) are fine — both produce reproducible output."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id == "random":
            attr = node.func.attr
            if attr in _RANDOM_NONDETERMINISTIC_FUNCS:
                self.smells.append(Smell(
                    file=self.file,
                    lineno=node.lineno,
                    checker="determinism",
                    message=(
                        f"``random.{attr}(...)`` reads the unseeded "
                        f"global random state — different runs produce "
                        f"different output, breaking hash-locked seeds + "
                        f"fuzz-seed reproducibility. Use ``rng = "
                        f"random.Random(<seed>); rng.{attr}(...)`` "
                        f"instead, OR add ``# typing-smell: ignore"
                        f"[determinism]`` with a one-line reason."
                    ),
                ))
            elif attr == "Random" and not node.args:
                self.smells.append(Smell(
                    file=self.file,
                    lineno=node.lineno,
                    checker="determinism",
                    message=(
                        f"``random.Random()`` with no seed picks a "
                        f"random seed at construction time — different "
                        f"runs produce different output. Pass an "
                        f"explicit int seed: ``random.Random(<seed>)``."
                    ),
                ))
        self.generic_visit(node)


class DeterminismCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _DeterminismVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: boto3-direct (Y.2.gate.b.15.lint.boto3-direct)
# ---------------------------------------------------------------------------


class _Boto3DirectVisitor(ast.NodeVisitor):
    """Walk Call nodes; flag direct ``boto3.client(...)`` calls.

    Stray clients bypass the ``ManagedBy: quicksight-gen`` tagging
    convention that all production resource creation goes through;
    the cleanup verb relies on every resource carrying that tag to
    find orphans."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and \
                node.func.attr == "client" and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id == "boto3":
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="boto3-direct",
                message=(
                    "direct ``boto3.client(...)`` call — production "
                    "AWS access goes through one of the 4 known "
                    "wrappers (``common/deploy.py``, ``common/cleanup.py``, "
                    "``common/browser/helpers.py``, ``_dev/runner.py``) "
                    "so resources stay tagged ``ManagedBy: "
                    "quicksight-gen`` and ``cleanup`` finds them. If "
                    "this site is genuinely a new wrapper, add it to "
                    "the lint's allowlist; otherwise route through "
                    "an existing one. Suppress with ``# typing-smell: "
                    "ignore[boto3-direct]: <reason>`` if intentional."
                ),
            ))
        self.generic_visit(node)


class Boto3DirectCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _Boto3DirectVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: qs-gen-prefix (Y.2.gate.b.15.lint.qs-gen-prefix)
# ---------------------------------------------------------------------------


_QS_GEN_PREFIX_RE = re.compile(r"^qs-gen[\-_]")


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Collect ``id()`` of Constant nodes that are docstrings — the
    string at body[0] of a Module / ClassDef / FunctionDef /
    AsyncFunctionDef. Used to skip docstrings in pure-string lints."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                              ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


class _QsGenPrefixVisitor(ast.NodeVisitor):
    """Walk Constant string nodes; flag ``qs-gen-...`` literals
    (excluding docstrings)."""

    def __init__(self, file: Path, docstring_ids: set[int]) -> None:
        self.file = file
        self.docstring_ids = docstring_ids
        self.smells: list[Smell] = []

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        if id(node) in self.docstring_ids:
            return
        if _QS_GEN_PREFIX_RE.match(node.value):
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="qs-gen-prefix",
                message=(
                    f"hardcoded ``qs-gen-`` resource-prefix string "
                    f"({node.value!r}) — use ``cfg.prefixed(<name>)`` "
                    f"so the L2 instance prefix is woven in. Direct "
                    f"``f\"qs-gen-foo\"`` defeats multi-tenant scoping. "
                    f"Suppress with ``# typing-smell: ignore"
                    f"[qs-gen-prefix]: <reason>`` if intentional."
                ),
            ))


class QsGenPrefixCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        docstring_ids = _docstring_node_ids(tree)
        v = _QsGenPrefixVisitor(file, docstring_ids)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: no-datetime-now (Y.2.gate.b.15.lint.no-datetime-now)
# ---------------------------------------------------------------------------


class _NoDatetimeNowVisitor(ast.NodeVisitor):
    """Walk Call nodes; flag ``datetime.now()`` / ``datetime.utcnow()``
    / ``date.today()`` calls. Determinism leak risk for hash-locked
    seed contracts + e2e diffing."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id in ("datetime", "date") and \
                node.func.attr in ("now", "utcnow", "today"):
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="no-datetime-now",
                message=(
                    f"``{node.func.value.id}.{node.func.attr}()`` "
                    f"reads wall-clock time — different runs produce "
                    f"different output, breaking hash-locked seed "
                    f"contracts + diff-based e2e assertions. Pin a "
                    f"specific date / pass the anchor through, OR "
                    f"add the file to the allowlist if it's a legit "
                    f"timestamp source (e.g., audit cover-page, "
                    f"deploy stamp). Suppress with ``# typing-smell: "
                    f"ignore[no-datetime-now]: <reason>``."
                ),
            ))
        self.generic_visit(node)


class NoDatetimeNowCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _NoDatetimeNowVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: no-sleep (Y.2.gate.b.15.lint.no-sleep)
# ---------------------------------------------------------------------------


class _NoSleepVisitor(ast.NodeVisitor):
    """Walk Call nodes; flag ``time.sleep(...)``. Browser e2e should
    poll for state changes; sleeps cause flakes (too short = race;
    too long = slow CI)."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and \
                node.func.attr == "sleep" and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id == "time":
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="no-sleep",
                message=(
                    "``time.sleep(...)`` in browser e2e — use "
                    "``page.wait_for_function`` / "
                    "``wait_for_load_state`` / "
                    "``wait_for_response`` polls instead. Sleeps "
                    "cause flakes (too short → race; too long → slow "
                    "CI). If genuinely necessary (e.g., 50ms startup "
                    "poll inside a uvicorn-spinup loop), suppress "
                    "with ``# typing-smell: ignore[no-sleep]: "
                    "<reason>``."
                ),
            ))
        self.generic_visit(node)


class NoSleepCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _NoSleepVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: json-indent (Y.2.gate.b.15.lint.json-indent)
# ---------------------------------------------------------------------------


class _JsonIndentVisitor(ast.NodeVisitor):
    """Walk Call nodes; flag ``json.dumps(obj)`` without ``indent=`` OR
    ``separators=``. Either kwarg signals a deliberate format choice
    (indent for human-diffable file emit; separators for compact
    deterministic output). Bare ``json.dumps()`` is ambiguous."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and \
                node.func.attr == "dumps" and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id in ("json", "_json"):
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg}
            if "indent" not in kwarg_names and "separators" not in kwarg_names:
                self.smells.append(Smell(
                    file=self.file,
                    lineno=node.lineno,
                    checker="json-indent",
                    message=(
                        "``json.dumps(obj)`` without ``indent=`` or "
                        "``separators=`` — make the format choice "
                        "deliberate. Use ``indent=2`` for human-"
                        "diffable file emit (most CLI write paths), "
                        "or ``separators=(\",\", \":\")`` for compact "
                        "deterministic output (cryptographic "
                        "fingerprints, log lines, embedded HTML "
                        "payloads). Suppress with ``# typing-smell: "
                        "ignore[json-indent]: <reason>`` if intentional."
                    ),
                ))
        self.generic_visit(node)


class JsonIndentCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _JsonIndentVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Suppression filtering
# ---------------------------------------------------------------------------


def _line_suppressors(line: str) -> set[str]:
    m = _INLINE_IGNORE_RE.search(line)
    if not m:
        return set()
    return {tok.strip() for tok in m.group(1).split(",") if tok.strip()}


def _file_suppressors(src: str) -> set[str]:
    out: set[str] = set()
    for line in src.splitlines():
        m = _FILE_IGNORE_RE.search(line)
        if m:
            for tok in m.group(1).split(","):
                if tok.strip():
                    out.add(tok.strip())
    return out


def _is_suppressed(smell: Smell, lines: list[str], file_supp: set[str]) -> bool:
    if smell.checker in file_supp:
        return True
    if 0 < smell.lineno <= len(lines):
        if smell.checker in _line_suppressors(lines[smell.lineno - 1]):
            return True
    return False


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _expand_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(p.rglob("*.py")))
        else:
            out.append(p)
    return out


def _read_pyright_include() -> list[Path]:
    data = tomllib.loads(PYPROJECT.read_text())
    rel = data["tool"]["pyright"]["include"]
    return [REPO_ROOT / r for r in rel]


def _build_checks() -> list[Check]:
    pyright_scope = _read_pyright_include()
    # Tighter scope for explicit-any: the freshest async files where
    # X.2.o just landed. Models.py / l2 / tree have legacy Any uses
    # the tree-pattern relies on (Visual subtype dispatch, AWS JSON
    # shapes); they get the file-level opt-out below if needed.
    explicit_any_scope = [
        REPO_ROOT / "src/quicksight_gen/common/db.py",
        REPO_ROOT / "src/quicksight_gen/common/html/_sql_executor.py",
        REPO_ROOT / "src/quicksight_gen/common/html/_tree_fetcher.py",
        REPO_ROOT / "src/quicksight_gen/common/html/server.py",
        REPO_ROOT / "src/quicksight_gen/common/config.py",
    ]
    # envvar-bypass spans src/ + tests/ (both have env access). The
    # registry itself + its unit test are the two legit consumers of
    # raw os.environ — whitelisted via path exclusion below.
    envvar_scope = [
        p for p in (
            _expand_paths([REPO_ROOT / "src/quicksight_gen"])
            + _expand_paths([REPO_ROOT / "tests"])
        )
        if p.name != "env_keys.py"
        and p.name != "test_env_keys.py"
    ]
    # why-comment spans the same src/ + tests/ surface as envvar-bypass.
    # Whitelist the lint module itself (its docstring shows example
    # suppression syntax that would self-flag) and the env_keys test
    # (defensive — its negative tests don't currently use suppression
    # markers, but keep parity with envvar-bypass's exclusions).
    why_comment_scope = [
        p for p in (
            _expand_paths([REPO_ROOT / "src/quicksight_gen"])
            + _expand_paths([REPO_ROOT / "tests"])
        )
        if p.name != "test_typing_smells.py"
        and p.name != "test_env_keys.py"
    ]
    return [
        BareStrIdCheck(
            name="bare-str-id",
            description=(
                "function parameters named like IDs must use the matching "
                "NewType from common/ids.py instead of bare ``str``"
            ),
            files=_expand_paths(pyright_scope),
        ),
        ExplicitAnyCheck(
            name="explicit-any",
            description=(
                "explicit ``Any`` in annotations is a smell — replace with "
                "a real type or suppress per-line with a WHY"
            ),
            files=explicit_any_scope,
        ),
        EnvVarBypassCheck(
            name="envvar-bypass",
            description=(
                "bare os.environ.get / os.environ[...] / os.getenv / "
                "monkeypatch.setenv|delenv with a QS_GEN_/QS_E2E_ "
                "string literal — use the typed EnvVar registry at "
                "common/env_keys.py instead"
            ),
            files=envvar_scope,
        ),
        WhyCommentCheck(
            name="why-comment",
            description=(
                "every ``# type: ignore`` / ``# pyright: ignore`` / "
                "``# typing-smell: ignore`` must end with ``: <3+ word "
                "reason>`` explaining why the suppression is principled "
                "(escape hatch: ``# bare-suppression-ok`` on the same line)"
            ),
            files=why_comment_scope,
        ),
        DeterminismCheck(
            name="determinism",
            description=(
                "module-level ``random.X(...)`` / ``random.Random()`` "
                "in seed-generating code reads unseeded global state; "
                "use ``rng = random.Random(<seed>); rng.X(...)`` "
                "instead so hash-locked seeds + fuzz reproducibility "
                "stay deterministic"
            ),
            files=_expand_paths([
                REPO_ROOT / "src/quicksight_gen/common/l2/seed.py",
                REPO_ROOT / "src/quicksight_gen/common/l2/auto_scenario.py",
                REPO_ROOT / "src/quicksight_gen/apps",
            ]),
        ),
        Boto3DirectCheck(
            name="boto3-direct",
            description=(
                "direct ``boto3.client(...)`` outside the 4 known "
                "production wrappers — bypasses the ManagedBy tagging "
                "convention; route through ``common/deploy.py``, "
                "``common/cleanup.py``, ``common/browser/helpers.py``, "
                "or ``_dev/runner.py`` instead"
            ),
            files=[
                p for p in _expand_paths(
                    [REPO_ROOT / "src/quicksight_gen"]
                )
                if p != REPO_ROOT / "src/quicksight_gen/common/deploy.py"
                and p != REPO_ROOT / "src/quicksight_gen/common/cleanup.py"
                and p != REPO_ROOT / "src/quicksight_gen/common/browser/helpers.py"
                and p != REPO_ROOT / "src/quicksight_gen/_dev/runner.py"
            ],
        ),
        QsGenPrefixCheck(
            name="qs-gen-prefix",
            description=(
                "hardcoded ``qs-gen-...`` resource-prefix literal in "
                "src code — use ``cfg.prefixed(<name>)`` so the L2 "
                "instance prefix is woven in (multi-tenant scoping)"
            ),
            files=[
                p for p in _expand_paths(
                    [REPO_ROOT / "src/quicksight_gen"]
                )
                if p != REPO_ROOT / "src/quicksight_gen/common/config.py"
            ],
        ),
        NoDatetimeNowCheck(
            name="no-datetime-now",
            description=(
                "``datetime.now()`` / ``date.today()`` outside the "
                "4 allowlist files (runner, audit, app_info, "
                "provenance) — determinism leak risk for "
                "hash-locked / diff-based output"
            ),
            files=[
                p for p in _expand_paths(
                    [REPO_ROOT / "src/quicksight_gen"]
                )
                if not str(p.relative_to(REPO_ROOT)).startswith(
                    "src/quicksight_gen/_dev/runner"
                )
                and not str(p.relative_to(REPO_ROOT)).startswith(
                    "src/quicksight_gen/cli/audit/"
                )
                and p != REPO_ROOT / "src/quicksight_gen/common/sheets/app_info.py"
                and p != REPO_ROOT / "src/quicksight_gen/common/provenance.py"
            ],
        ),
        NoSleepCheck(
            name="no-sleep",
            description=(
                "``time.sleep(...)`` in browser e2e — use Playwright "
                "wait_* polls instead; sleeps are flake sources"
            ),
            files=_expand_paths([REPO_ROOT / "tests/e2e"]),
        ),
        JsonIndentCheck(
            name="json-indent",
            description=(
                "bare ``json.dumps(obj)`` requires either "
                "``indent=`` (human-diffable) or ``separators=`` "
                "(compact deterministic) — make the format choice "
                "deliberate"
            ),
            files=_expand_paths([
                REPO_ROOT / "src/quicksight_gen/cli",
                REPO_ROOT / "src/quicksight_gen/common",
            ]),
        ),
    ]


def _collect_smells() -> list[Smell]:
    out: list[Smell] = []
    for check in _build_checks():
        for file in check.files:
            src = file.read_text()
            tree = ast.parse(src)
            file_supp = _file_suppressors(src)
            lines = src.splitlines()
            for smell in check.find_smells(src, tree, file):
                if _is_suppressed(smell, lines, file_supp):
                    continue
                out.append(smell)
    return out


def test_no_typing_smells() -> None:
    """The only test in this module — assert zero unsuppressed smells.

    Failure prints every smell with file:line and the check that
    flagged it. To fix: rewrite the annotation OR add a per-line
    ``# typing-smell: ignore[<check-name>]`` with a one-line reason.
    """
    smells = _collect_smells()
    if not smells:
        return
    lines = ["typing smells found:"]
    for s in smells:
        rel = s.file.relative_to(REPO_ROOT)
        lines.append(f"  {rel}:{s.lineno} [{s.checker}] {s.message}")
    pytest.fail("\n".join(lines))
