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
  ``RECON_GEN_*`` or ``RECON_E2E_*`` string literal as the first arg.
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
  ``boto3.client(...)`` calls outside the 5 known production
  wrappers (``common/deploy.py``, ``common/cleanup.py``,
  ``common/browser/helpers.py``, ``common/aws_rds.py``,
  ``_dev/runner.py``). Stray clients bypass the
  ``ManagedBy: recon-gen`` tagging convention → break
  ``cleanup``. Tests can freely use ``boto3.client`` (scope is
  src/ only).

- **recon-prefix** (Y.2.gate.b.15.lint.recon-prefix, originally
  ``qs-gen-prefix`` — renamed at AC.B.1) — hardcoded
  ``"recon-<env>-..."`` deployment-prefix string literals in src
  code outside ``common/config.py``. Resource IDs flow through
  ``cfg.prefixed(name)`` which weaves in the operator's
  ``deployment_name``; bypassing it (``f"recon-prod-foo"`` direct)
  defeats multi-tenant scoping. Bare ``recon-gen`` (package /
  CLI binary mentions) is allowed — the regex requires
  ``recon-<env>-`` with a trailing dash. Docstrings are ignored.

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

- **no-playwright-leak** (X.2.q.5) — ``import playwright`` /
  ``from playwright[.x] import …`` OR
  ``from recon_gen.common.browser{.helpers|.screenshot} import …``
  (the Playwright-primitives layer; the AWS-only helpers
  ``get_user_arn`` / ``generate_dashboard_embed_url`` are exempt) in
  any ``tests/e2e/`` file outside the driver layer
  (``tests/e2e/_drivers/``). Playwright stays sealed behind
  ``DashboardDriver`` — e2e tests talk driver verbs (``open`` /
  ``goto_sheet`` / ``table_rows`` / ``pick_filter`` / ``screenshot`` /
  …), not ``Page`` / ``webkit_page`` / ``wait_for_*``. The
  ``_PLAYWRIGHT_LEAK_LEGACY`` set is the X.2.q.3 migration backlog —
  it can only shrink; porting a test removes its name. ``tests/js/``
  (the JS-unit harness, which drives Playwright directly by design)
  isn't under ``tests/e2e/`` so it's out of scope automatically.

- **no-hidden-in-e2e** (BH.24-class, 2026-05-25) — any string literal
  containing the substring ``hidden`` (case-insensitive) in a
  ``tests/e2e/test_*.py`` file. v11.21.0 cold-read finding #2 + AI.12
  WebKit fill-on-hidden quirk are both "hidden input drives the wire,
  events/state get fragile around it" — driving hidden DOM details
  from a test body bypasses the user-facing locator contract (see
  ``feedback_browser_drivers_user_facing_locators``). The driver
  layer (``tests/e2e/_drivers/``) is allowed to bridge to hidden
  inputs; renderer-emission unit tests (``tests/unit/test_html_*.py``)
  legitimately assert the HTML wire format. Suppress a one-off with
  ``# typing-smell: ignore[no-hidden-in-e2e]: <reason>``.

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
_ENV_VAR_NAME_RE = re.compile(r"^(QS|RECON)_(GEN|E2E)_[A-Z0-9_]+$")


def _is_qs_env_literal(node: ast.AST) -> str | None:
    """Return the env-var name if ``node`` is a ``Constant(str)`` matching
    the QS_GEN/QS_E2E (legacy) or RECON_GEN/RECON_E2E (canonical)
    pattern; else None. AC.B.3 grace period — registry's typed
    fallback handles legacy reads, but new bypass calls with either
    prefix are still smells."""
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

    Stray clients bypass the ``ManagedBy: recon-gen`` tagging
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
                    "AWS access goes through one of the 5 known "
                    "wrappers (``common/deploy.py``, ``common/cleanup.py``, "
                    "``common/browser/helpers.py``, ``common/aws_rds.py``, "
                    "``_dev/runner.py``) so resources stay tagged "
                    "``ManagedBy: recon-gen`` and ``cleanup`` finds "
                    "them. If this site is genuinely a new wrapper, add "
                    "it to the lint's allowlist; otherwise route through "
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
# Check: recon-prefix (Y.2.gate.b.15.lint.recon-prefix, originally
# qs-gen-prefix; renamed at AC.B.1 alongside the qsgen- → recon-
# deployment prefix sweep)
# ---------------------------------------------------------------------------


# Match deployment-style prefixes like ``recon-prod-foo`` / ``recon-myorg-prod-bar``
# — recon, followed by an env-name segment, followed by a resource segment.
# Deliberately does NOT match ``recon-gen`` (the package / CLI binary name)
# or bare ``recon-`` mentions: the trailing ``-`` after the env segment is
# the discriminator.
_RECON_PREFIX_RE = re.compile(r"^recon-[a-z]+-")


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


class _ReconPrefixVisitor(ast.NodeVisitor):
    """Walk Constant string nodes; flag ``recon-<env>-...`` literals
    (deployment-prefix style; excludes docstrings + bare
    ``recon-gen`` package/binary mentions)."""

    def __init__(self, file: Path, docstring_ids: set[int]) -> None:
        self.file = file
        self.docstring_ids = docstring_ids
        self.smells: list[Smell] = []

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        if id(node) in self.docstring_ids:
            return
        if _RECON_PREFIX_RE.match(node.value):
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="recon-prefix",
                message=(
                    f"hardcoded ``recon-<env>-`` deployment-prefix string "
                    f"({node.value!r}) — use ``cfg.prefixed(<name>)`` "
                    f"so the operator's deployment_name is woven in. "
                    f"Direct ``f\"recon-prod-foo\"`` defeats multi-tenant "
                    f"scoping. Suppress with ``# typing-smell: ignore"
                    f"[recon-prefix]: <reason>`` if intentional."
                ),
            ))


class ReconPrefixCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        docstring_ids = _docstring_node_ids(tree)
        v = _ReconPrefixVisitor(file, docstring_ids)
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


class _TestModuleNondeterminismVisitor:
    """Walk Module top-level (NOT inside functions/classes); flag
    ``random.X()`` / ``secrets.X()`` / ``datetime.X()`` calls.

    Why: such calls produce a different value per pytest-xdist worker
    process. If the result lands in a parametrize id (via
    ``@pytest.fixture(params=...)`` over a module-level constant),
    workers register different test IDs and pytest-xdist refuses to
    start with "Different tests were collected between gw0 and gwN".
    Even when not in parametrize, module-level non-determinism in
    tests is almost always a smell — fixtures are the right tool.

    Caught the m.5 fix-up bug: ``tests/data/test_l2_seed_contract.py
    ::FUZZ_SEED = secrets.randbits(32)`` at module level (b.15.lint
    .determinism didn't fire because that lint scopes to ``src/``).

    Implementation note: we manually walk ``tree.body`` rather than
    use ``ast.NodeVisitor.generic_visit`` so we don't descend into
    function/class bodies. Module-level non-determinism is the smell;
    inside-function calls are usually fine.
    """

    _STDLIB_RNG_MODULES = frozenset({"random", "secrets"})
    _DATETIME_NOW_ATTRS = frozenset({"now", "utcnow", "today"})

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_module(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            self._visit_top_level(stmt)

    def _visit_top_level(self, node: ast.AST) -> None:
        """Top-level dispatch: function/class defs are NOT descended
        (their bodies execute per-call, not at import) — only their
        decorators + class attributes + function arg defaults count
        as module-import-time evaluation."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                self._walk_expr(deco)
            for default in node.args.defaults + node.args.kw_defaults:
                if default is not None:
                    self._walk_expr(default)
            return
        if isinstance(node, ast.ClassDef):
            for deco in node.decorator_list:
                self._walk_expr(deco)
            for base in node.bases:
                self._walk_expr(base)
            for class_stmt in node.body:
                # Class attribute assignments are import-time;
                # methods (FunctionDef) are not.
                if not isinstance(class_stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self._walk_expr(class_stmt)
            return
        self._walk_expr(node)

    def _walk_expr(self, node: ast.AST) -> None:
        """Walk an expression / statement, flagging nondeterminism
        calls. Recurses into children but bails on nested function/
        class defs (their bodies are per-call)."""
        if isinstance(node, ast.Call):
            self._maybe_flag(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Nested def — recurse via _visit_top_level which
                # handles decorators / defaults / class attrs correctly.
                self._visit_top_level(child)
                continue
            self._walk_expr(child)

    def _maybe_flag(self, node: ast.Call) -> None:
        # `random.X(...)` or `secrets.X(...)`
        if isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id in self._STDLIB_RNG_MODULES:
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="test-module-nondeterminism",
                message=(
                    f"``{node.func.value.id}.{node.func.attr}(...)`` at "
                    f"module top level — produces a different value per "
                    f"pytest-xdist worker process; if the result feeds a "
                    f"parametrize id, xdist refuses to start with "
                    f"'Different tests collected between gw0 and gwN'. "
                    f"Move the call into a fixture (function- or "
                    f"session-scoped), OR pin via env at "
                    f"``tests/conftest.py::pytest_configure`` so all "
                    f"workers see the same seed. Suppress with "
                    f"``# typing-smell: ignore[test-module-"
                    f"nondeterminism]: <reason>`` if intentional."
                ),
            ))
        # `datetime.now()` / `date.today()` etc. (covered by
        # no-datetime-now in src/, but tests/ aren't in that lint's
        # scope, so we re-check here.)
        elif isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and \
                node.func.value.id in ("datetime", "date") and \
                node.func.attr in self._DATETIME_NOW_ATTRS:
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="test-module-nondeterminism",
                message=(
                    f"``{node.func.value.id}.{node.func.attr}()`` at "
                    f"module top level — wall-clock time at import is "
                    f"unstable across runs and across xdist workers. "
                    f"Pin a specific date / use a fixture / move to "
                    f"conftest. Suppress with "
                    f"``# typing-smell: ignore[test-module-"
                    f"nondeterminism]: <reason>``."
                ),
            ))


class ModuleNondeterminismCheck(Check):  # NOT prefixed "Test" — pytest collects "Test*" classes
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        if not isinstance(tree, ast.Module):
            return []
        v = _TestModuleNondeterminismVisitor(file)
        v.visit_module(tree)
        return v.smells


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


class _TreeDataclassVisitor(ast.NodeVisitor):
    """Walk ClassDef nodes; flag ``@dataclass`` (or ``@dataclass(...)``)
    in ``common/tree/`` that doesn't specify ``frozen=True`` or
    ``eq=False``. Tree nodes are either mutable parents in the
    object-ref graph (``eq=False`` so identity is the equality
    semantics — two distinct sheets with the same name are not equal)
    or value-type leaves (``frozen=True`` — Column / formatting
    primitives are hashable + immutable). Default ``@dataclass`` gives
    structural equality on mutable state, which silently breaks both
    contracts."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for dec in node.decorator_list:
            # ``@dataclass`` (Name) — bare, no kwargs
            if isinstance(dec, ast.Name) and dec.id == "dataclass":
                self.smells.append(self._smell(node, "bare ``@dataclass``"))
            # ``@dataclass(...)`` (Call)
            elif isinstance(dec, ast.Call) and \
                    isinstance(dec.func, ast.Name) and \
                    dec.func.id == "dataclass":
                kwargs = {kw.arg: kw.value for kw in dec.keywords if kw.arg}
                if not self._has_frozen_or_eq_false(kwargs):
                    self.smells.append(self._smell(
                        node, "``@dataclass(...)`` missing ``frozen=True`` or ``eq=False``",
                    ))
        self.generic_visit(node)

    def _has_frozen_or_eq_false(
        self, kwargs: dict[str, ast.expr],
    ) -> bool:
        if "frozen" in kwargs:
            v = kwargs["frozen"]
            if isinstance(v, ast.Constant) and v.value is True:
                return True
        if "eq" in kwargs:
            v = kwargs["eq"]
            if isinstance(v, ast.Constant) and v.value is False:
                return True
        return False

    def _smell(self, node: ast.ClassDef, what: str) -> Smell:
        return Smell(
            file=self.file,
            lineno=node.lineno,
            checker="tree-dataclass",
            message=(
                f"{what} on tree-pattern class ``{node.name}`` — "
                "tree nodes are object-ref-identified (``eq=False``) "
                "or value-type leaves (``frozen=True``); default "
                "structural equality on a mutable tree node breaks "
                "the cross-ref graph (two distinct sheets with the "
                "same name would compare equal). Pick ``eq=False`` "
                "for mutable parents or ``frozen=True`` for value "
                "leaves."
            ),
        )


class TreeDataclassCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _TreeDataclassVisitor(file)
        v.visit(tree)
        return v.smells


_QS_CREATE_FUNCS = frozenset({
    "create_data_set",
    "create_analysis",
    "create_dashboard",
    "create_theme",
    "create_data_source",
})


class _CreateTagsVisitor(ast.NodeVisitor):
    """Walk Call nodes; flag ``<x>.create_data_set/_analysis/_dashboard/
    _theme/_data_source(...)`` calls in deploy.py that don't either pass
    ``Tags=`` directly OR spread a payload dict via ``**name`` (where
    Tags is expected to live in the JSON payload). Pairs with
    ``boto3-direct`` — that lint catches new ``boto3.client()``
    instantiations outside the allowlist; this lint catches new
    ``client.create_X(...)`` shapes in the allowlisted boto3 files
    that would skip the ManagedBy: recon-gen tagging convention."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and \
                node.func.attr in _QS_CREATE_FUNCS:
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg}
            has_spread = any(kw.arg is None for kw in node.keywords)
            if "Tags" not in kwarg_names and not has_spread:
                self.smells.append(Smell(
                    file=self.file,
                    lineno=node.lineno,
                    checker="create-tags",
                    message=(
                        f"``{node.func.attr}(...)`` without ``Tags=`` "
                        "or ``**payload`` spread — boto3 QuickSight "
                        "create_* calls must carry the ``ManagedBy: "
                        "recon-gen`` tag (plus per-instance + "
                        "extra tags) so the cleanup CLI can find + "
                        "delete the resource later. Either pass "
                        "``Tags=[...]`` directly or build the dict "
                        "via ``build_*`` and spread with "
                        "``**payload``."
                    ),
                ))
        self.generic_visit(node)


class CreateTagsCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _CreateTagsVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: no-playwright-leak (X.2.q.5)
# ---------------------------------------------------------------------------


# Names that live in ``common/browser/helpers.py`` but aren't
# Playwright-coupled — AWS plumbing that happens to share the file.
# A test importing ONLY these is fine; importing ``webkit_page`` /
# ``wait_for_*`` / ``screenshot`` / etc. is bypassing the driver layer.
_NON_PLAYWRIGHT_BROWSER_HELPERS = frozenset({
    "get_user_arn",
    "generate_dashboard_embed_url",
    # AA.A.qs-triage.5.followon — failure-capture sidecar; writes
    # `<capture_dir>/sql_trace.txt`. No Playwright coupling. Lives in
    # helpers.py because that's where the rest of the per-test capture
    # primitives (`_capture_path`, `_test_id_from_pytest_env`) already are.
    "record_sql_trace",
})

_BROWSER_HELPERS_MOD = "recon_gen.common.browser.helpers"
_BROWSER_SCREENSHOT_MOD = "recon_gen.common.browser.screenshot"
_BROWSER_PKG = "recon_gen.common.browser"

# X.2.q.3 migration backlog — ``tests/e2e/`` files that still drive
# Playwright directly (via ``common/browser/helpers``) instead of through
# ``DashboardDriver``. The lint excludes these; a port REMOVES a name from
# this set (and the lint then enforces it stays ported). New e2e tests
# are NOT added here — they use ``DashboardDriver``. The set can only
# shrink; a non-empty entry is a visible "not yet migrated" TODO.
# (Ported off the set as X.2.q.3 progresses — git history records which:
#  exec/l1/inv dashboard_renders + sheet_visuals + tree_validator, then
#  l1/inv filters + l2ft rails/chains/templates dropdowns, then the drill
#  trio (inv_drilldown / l1_cross_sheet_drill / l2ft_metadata_cascade), done.)
_PLAYWRIGHT_LEAK_LEGACY: frozenset[str] = frozenset()


class _NoPlaywrightLeakVisitor(ast.NodeVisitor):
    """Flag (a) ``import playwright[...]`` / ``from playwright[...] import``
    and (b) ``from recon_gen.common.browser{.helpers|.screenshot}
    import …`` (except the AWS-only helper names) in a browser-e2e test —
    Playwright stays sealed behind the ``DashboardDriver`` layer."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def _msg(self, what: str) -> str:
        return (
            f"{what} in a browser-e2e test — Playwright stays sealed "
            "behind the ``DashboardDriver`` layer (``tests/e2e/_drivers/``). "
            "e2e tests talk ``DashboardDriver`` verbs (``open`` / "
            "``goto_sheet`` / ``table_rows`` / ``pick_filter`` / "
            "``screenshot`` / …), not ``Page`` / ``webkit_page`` / "
            "``wait_for_*`` — see X.2.q. If this file hasn't been ported "
            "onto a driver fixture yet, add it to ``_PLAYWRIGHT_LEAK_LEGACY`` "
            "in this module (the X.2.q.3 migration backlog) — but prefer "
            "porting. Suppress a one-off with ``# typing-smell: ignore"
            "[no-playwright-leak]: <reason>``."
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "playwright" or alias.name.startswith("playwright."):
                self.smells.append(Smell(
                    file=self.file, lineno=node.lineno,
                    checker="no-playwright-leak",
                    message=self._msg(f"``import {alias.name}``"),
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod == "playwright" or mod.startswith("playwright."):
            self.smells.append(Smell(
                file=self.file, lineno=node.lineno,
                checker="no-playwright-leak",
                message=self._msg(f"``from {mod} import …``"),
            ))
        elif mod in (_BROWSER_SCREENSHOT_MOD, _BROWSER_PKG):
            self.smells.append(Smell(
                file=self.file, lineno=node.lineno,
                checker="no-playwright-leak",
                message=self._msg(f"``from {mod} import …``"),
            ))
        elif mod == _BROWSER_HELPERS_MOD:
            imported = {a.name for a in node.names}
            offenders = imported - _NON_PLAYWRIGHT_BROWSER_HELPERS
            if offenders:
                self.smells.append(Smell(
                    file=self.file, lineno=node.lineno,
                    checker="no-playwright-leak",
                    message=self._msg(
                        f"``from {mod} import {', '.join(sorted(offenders))}``"
                    ),
                ))
        self.generic_visit(node)


class NoPlaywrightLeakCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _NoPlaywrightLeakVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: no-hidden-in-e2e (BH.24-class, 2026-05-25 — per user "I kinda
# want to have you write an AST to scream if the string 'hidden' appears
# in any test")
# ---------------------------------------------------------------------------
#
# v11.21.0 cold-read finding #2 + AI.12's WebKit fill-on-hidden quirk are
# both the same bug class: "hidden input drives the wire, events/state
# get fragile around it." The fix in both cases is to drive USER-FACING
# elements (the visible widget label, the dropdown option text) and let
# the driver layer bridge to whatever hidden inputs the renderer uses.
#
# Scope: ``tests/e2e/test_*.py`` — top-level e2e test bodies. NOT the
# driver implementations (``tests/e2e/_drivers/`` legitimately writes to
# hidden inputs to bridge the visible widget → form submission wire).
# NOT unit tests (``tests/unit/test_html_*.py`` asserts the renderer
# EMITS the right hidden-input HTML — different concern). NOT JS tests
# (``tests/js/`` tests the select→hidden sync directly).
#
# Per ``feedback_browser_drivers_user_facing_locators``: tests should
# locate by labels / ARIA roles / visible text, never by hidden DOM
# state.

_HIDDEN_RE = re.compile(r"hidden", re.IGNORECASE)


class _NoHiddenInE2EVisitor(ast.NodeVisitor):
    """Walk Constant string nodes; flag any string containing the
    substring ``hidden`` (case-insensitive). Skips docstrings + ignore-
    line suppressions."""

    def __init__(self, file: Path, docstring_ids: set[int]) -> None:
        self.file = file
        self.docstring_ids = docstring_ids
        self.smells: list[Smell] = []

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        if id(node) in self.docstring_ids:
            return
        if not _HIDDEN_RE.search(node.value):
            return
        self.smells.append(Smell(
            file=self.file,
            lineno=node.lineno,
            checker="no-hidden-in-e2e",
            message=(
                f"e2e test references {node.value!r} containing "
                f"``hidden`` — tests should drive USER-FACING locators "
                f"(label text / ARIA role / visible widget) and let the "
                f"driver layer bridge to hidden inputs. v11.21.0 cold-"
                f"read finding #2 + AI.12 WebKit fill-on-hidden quirk "
                f"are both this bug class. If THIS test really needs to "
                f"poke at a hidden DOM detail (e.g. asserting a "
                f"renderer-emission unit-test moved to e2e by mistake), "
                f"either move it to ``tests/unit/`` or suppress with "
                f"``# typing-smell: ignore[no-hidden-in-e2e]: <reason>``."
            ),
        ))


class NoHiddenInE2ECheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        docstring_ids = _docstring_node_ids(tree)
        v = _NoHiddenInE2EVisitor(file, docstring_ids)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: no-naked-interval-ctor (BC.1, D7)
# ---------------------------------------------------------------------------


# The typed interval / plant-schedule wrappers. Bare ``Cls(...)`` calls
# outside ``common/intervals.py`` are off-limits — wiring sites must
# use one of the named-convention classmethods (``.closed()``,
# ``.single_day()``, ``.at_window_end()``, etc.) so the call site
# declares its policy. Per
# ``feedback_invariants_in_types``: types bring meaning with them,
# convention hides it.
_INTERVAL_TYPES: frozenset[str] = frozenset({
    "DateInterval",
    "DateTimeInterval",
    "SingleDayPlant",
    "MultiDayPlant",
})


class _NoNakedIntervalCtorVisitor(ast.NodeVisitor):
    """Flag bare ``Cls(...)`` construction of an interval / plant-schedule
    type — wiring sites must call one of the named classmethods."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def visit_Call(self, node: ast.Call) -> None:
        callee = node.func
        # ``DateInterval(...)`` — bare Name call. Forbidden outside the
        # wrapper module.
        if isinstance(callee, ast.Name) and callee.id in _INTERVAL_TYPES:
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="no-naked-interval-ctor",
                message=(
                    f"bare ``{callee.id}(...)`` constructor — wiring "
                    f"sites must use a named-convention classmethod "
                    f"(``.closed()`` / ``.single_day()`` / "
                    f"``.trailing_days_ending_yesterday()`` / "
                    f"``.at_window_end()`` / ``.spans()`` / etc.) so "
                    f"the call site declares its policy. See "
                    f"common/intervals.py for the full constructor "
                    f"surface."
                ),
            ))
        self.generic_visit(node)


class NoNakedIntervalCtorCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _NoNakedIntervalCtorVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: no-raw-temporal-args (BC.1, D8 — STAGED DISABLED until end of BC.5)
# ---------------------------------------------------------------------------


# When BC.5 finishes wrapping every src/ callsite that takes a raw
# ``date`` / ``datetime`` param, register this check in ``_build_checks``
# (the registration block carries the "ENABLE AT BC.5" marker). Until
# then, the lint exists as code but doesn't run — otherwise BC.1 reds
# the whole tree before BC has anywhere to migrate to.
_TEMPORAL_NAMES: frozenset[str] = frozenset({"date", "datetime"})


def _annotation_mentions_temporal(ann: ast.AST | None) -> str | None:
    """If ``ann`` is one of the temporal annotation shapes we forbid as a
    parameter type, return the offending name; otherwise None.

    Catches:
    - ``date`` / ``datetime`` (bare Name)
    - ``date | None`` / ``datetime | None`` (BinOp Union)
    - ``Optional[date]`` / ``Optional[datetime]`` (Subscript of Optional)
    - ``list[date]`` etc. — only if the innermost name is temporal AND
      it's the sole arg (so ``list[date]`` flags but ``dict[str, date]``
      doesn't, since the date is then a value-shape not a policy-carrier).
    Conservative — false-negatives are fine since the wrap migration
    pulls these into typed wrappers anyway.
    """
    if ann is None:
        return None
    if isinstance(ann, ast.Name) and ann.id in _TEMPORAL_NAMES:
        return ann.id
    # ``date | None`` / ``datetime | None`` — PEP 604 union.
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        for side in (ann.left, ann.right):
            sub = _annotation_mentions_temporal(side)
            if sub is not None:
                return sub
    # ``Optional[date]`` etc.
    if isinstance(ann, ast.Subscript):
        outer = ann.value
        if isinstance(outer, ast.Name) and outer.id == "Optional":
            return _annotation_mentions_temporal(ann.slice)
    return None


class _NoRawTemporalArgsVisitor(ast.NodeVisitor):
    """Flag function/method parameters annotated ``date`` / ``datetime``
    (or ``... | None`` variants). Wrap in ``DateInterval`` /
    ``DateTimeInterval`` / ``SingleDayPlant`` / ``MultiDayPlant`` /
    ``RunContext`` (BD)."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.smells: list[Smell] = []

    def _check_args(self, args: list[ast.arg]) -> None:
        for arg in args:
            offender = _annotation_mentions_temporal(arg.annotation)
            if offender is not None:
                self.smells.append(Smell(
                    file=self.file,
                    lineno=arg.lineno,
                    checker="no-raw-temporal-args",
                    message=(
                        f"parameter {arg.arg!r} typed as raw ``{offender}``; "
                        f"wrap in ``DateInterval`` / ``DateTimeInterval`` / "
                        f"``SingleDayPlant`` / ``MultiDayPlant`` / "
                        f"``RunContext`` instead (see common/intervals.py). "
                        f"Dataclass fields are exempt (point values, not "
                        f"params). Suppress with ``# typing-smell: ignore"
                        f"[no-raw-temporal-args]: <reason>``."
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


class NoRawTemporalArgsCheck(Check):
    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        v = _NoRawTemporalArgsVisitor(file)
        v.visit(tree)
        return v.smells


# ---------------------------------------------------------------------------
# Check: no-test-src-sql-duplication  (BE.1, approach 1 of BE.0 spike)
# ---------------------------------------------------------------------------


def _sql_fingerprint(s: str) -> str:
    """Normalize a string literal for cross-file duplication matching.

    Whitespace-collapse + strip + lowercase. Tests routinely re-indent
    the same SQL when copy-paste lands inside a fixture function;
    normalization makes the fingerprint robust to layout differences
    without giving up the "byte-equivalent contents" guarantee.
    """
    return re.sub(r"\s+", " ", s).strip().lower()


# SQL-shape filter: only flag string literals that look like SQL,
# not arbitrary long strings (docstrings, error messages, ASCII-art).
# Same filter the BE.0 spike used to land its 0-hit baseline at
# threshold 100 — without this, every long module docstring would
# trip. The regex is intentionally broad: any one of these tokens
# is enough signal that the literal carries SQL semantics worth
# guarding for drift. Case-insensitive.
_SQL_SHAPE_RE = re.compile(
    r"\b("
    r"SELECT|FROM|WHERE|INSERT\s+INTO|UPDATE\s+\w+\s+SET"
    r"|CREATE\s+(TABLE|VIEW|MATERIALIZED\s+VIEW|INDEX)"
    r"|DROP\s+(TABLE|VIEW|MATERIALIZED\s+VIEW|INDEX)"
    r"|JOIN|GROUP\s+BY|ORDER\s+BY|HAVING|UNION"
    r"|<<\$p"  # QS dataset parameter placeholder
    r")\b",
    re.IGNORECASE,
)


def _looks_like_sql(value: str) -> bool:
    """True iff ``value`` contains at least one SQL-shape token.

    The BE.1 lint scope is "long SQL copied across src/ ↔ tests/" —
    arbitrary long strings (e.g. module docstrings, error message
    templates, ASCII-art help text) aren't in scope. Skipping
    everything that doesn't smell like SQL keeps the rule tight and
    avoids the docstring-FP class.
    """
    return bool(_SQL_SHAPE_RE.search(value))


@dataclass
class NoTestSrcSqlDuplicationCheck(Check):
    """BE.1, approach 1 of BE.0's spike — flag string literals in
    ``tests/`` that ALSO appear verbatim (mod whitespace) in
    ``src/recon_gen/``. Catches the "test inlines a long SQL that
    drifted from production" regression class.

    Threshold ``min_length`` defaults to 100 chars (the spike's 0-hit
    baseline). Cuts later (BE.4) lower it to 50 once the sweep
    migrates the 5 known 50-90-char hits.

    Implementation: lazily build a src-side index (fingerprint →
    location) on first ``find_smells`` call, then per-test-file walk
    the AST for matching literals.

    Allowlist via the existing sibling-comment convention:
    ``# typing-smell: ignore[no-test-src-sql-duplication]: <why>``.
    Always require a WHY — the lint exists to catch drift, an
    allowlisted dup needs justification for why the contract is
    deliberately decoupled.
    """
    min_length: int = 100
    src_root: Path = field(default_factory=lambda: REPO_ROOT / "src/recon_gen")
    # Lazily-populated cache: fingerprint → list of (src_file, lineno).
    # Cleared between `_build_checks()` invocations because the Check
    # is reconstructed each call; intentional — pytest sessions are
    # short-lived enough that a fresh index per run is cheap.
    _src_index: dict[str, tuple[Path, int]] = field(
        default_factory=dict, init=False, repr=False,
    )

    def _build_src_index(self) -> None:
        if self._src_index:
            return
        for p in sorted(self.src_root.rglob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                ):
                    continue
                if len(node.value) < self.min_length:
                    continue
                if not _looks_like_sql(node.value):
                    continue
                fp = _sql_fingerprint(node.value)
                # Only record the FIRST occurrence — the message names
                # one site to migrate from, so a second site is just
                # noise. Duplications within src/ itself are a separate
                # concern (would be caught by a future BE.X dedup-
                # within-src lint).
                self._src_index.setdefault(fp, (p, node.lineno))

    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        self._build_src_index()
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
            ):
                continue
            if len(node.value) < self.min_length:
                continue
            if not _looks_like_sql(node.value):
                continue
            fp = _sql_fingerprint(node.value)
            hit = self._src_index.get(fp)
            if hit is None:
                continue
            src_file, src_lineno = hit
            rel_src = src_file.relative_to(REPO_ROOT)
            yield Smell(
                file=file,
                lineno=node.lineno,
                checker="no-test-src-sql-duplication",
                message=(
                    f"string literal (≥{self.min_length} chars, whitespace-"
                    f"normalized) also appears in {rel_src}:{src_lineno} — "
                    f"import from src/ instead of copying; if the test must "
                    f"hold its own contract independent of src/, suppress "
                    f"with ``# typing-smell: ignore[no-test-src-sql-"
                    f"duplication]: <why>``"
                ),
            )


# ---------------------------------------------------------------------------
# Check: no-inline-production-constants  (BE.2, approach 3 of BE.0 spike)
# ---------------------------------------------------------------------------


# UPPER_SNAKE module-level constant name pattern. Matches both public
# (``DRILL_RESET_SENTINEL_VALUE``) and private (``_DRIFT_NAME``)
# styles — the spike confirmed src uses both. First-char allows the
# optional leading underscore; rest is `[A-Z][A-Z0-9_]*`.
_UPPER_SNAKE_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")


def _collect_src_module_constants(
    src_root: Path,
) -> dict[str, tuple[Path, int, str]]:
    """Index ``src_root``'s module-level UPPER_SNAKE string constants.

    Walks every ``*.py`` under ``src_root`` and indexes top-level
    assignments of the shape ``NAME = "value"`` where ``NAME`` is
    UPPER_SNAKE (private leading-underscore allowed) and ``value``
    is a plain string of length 3-200 (the spike's filter range —
    too-short hits are false positives on tokens like "x"; too-long
    cross into the SQL-duplication territory BE.1 already covers).

    Returns ``{value: (file, lineno, name)}``. First occurrence
    wins on collisions (rare — two src files assigning the same
    UPPER_SNAKE value to different names is itself a code smell).
    """
    out: dict[str, tuple[Path, int, str]] = {}
    for p in sorted(src_root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        # Module-level only — class attributes + function-local
        # constants don't apply (the spike scoped to module-level
        # to keep the FP rate manageable). Walk tree.body, not
        # ast.walk.
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if not _UPPER_SNAKE_RE.match(target.id):
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            value = node.value.value
            if not isinstance(value, str):
                continue
            if not (3 <= len(value) <= 200):
                continue
            out.setdefault(value, (p, node.lineno, target.id))
    return out


class _NoInlineProductionConstantsVisitor(ast.NodeVisitor):
    """Walk ``ast.Assert`` nodes; flag string literals matching an
    indexed src constant.

    Why scoped to ``ast.Assert``: the lint's purpose is to catch
    "test inlines a production constant in an assertion" — bare
    string literals at module scope (test-fixture data, etc.) are
    a different drift class. Asserts narrow to the high-signal
    zone the spike measured.
    """

    def __init__(
        self,
        file: Path,
        src_index: dict[str, tuple[Path, int, str]],
    ) -> None:
        self.file = file
        self.src_index = src_index
        self.smells: list[Smell] = []
        # De-duplicate per-line: an assert with the same literal
        # repeated (e.g. `assert x == "foo" or y == "foo"`) only
        # surfaces ONE smell per line.
        self._seen: set[tuple[int, str]] = set()

    def _scan_for_literals(self, root: ast.AST) -> None:
        for node in ast.walk(root):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
            ):
                continue
            hit = self.src_index.get(node.value)
            if hit is None:
                continue
            key = (node.lineno, node.value)
            if key in self._seen:
                continue
            self._seen.add(key)
            src_file, src_lineno, src_name = hit
            rel_src = src_file.relative_to(REPO_ROOT)
            self.smells.append(Smell(
                file=self.file,
                lineno=node.lineno,
                checker="no-inline-production-constants",
                message=(
                    f"string literal {node.value!r} matches "
                    f"production constant ``{src_name}`` declared "
                    f"at {rel_src}:{src_lineno} — import from src/ "
                    f"so a rename in production fires the test "
                    f"loudly (instead of leaving the test asserting "
                    f"the stale value silently). Allowlist with "
                    f"``# typing-smell: ignore[no-inline-production-"
                    f"constants]: <why>`` for deliberate "
                    f"contract-independence cases (rare)"
                ),
            ))

    def visit_Assert(self, node: ast.Assert) -> None:
        self._scan_for_literals(node.test)
        if node.msg is not None:
            self._scan_for_literals(node.msg)
        # Don't generic_visit — nested asserts are vanishingly rare
        # and walking them again risks double-counting. The walk in
        # _scan_for_literals already descends into nested expressions.


@dataclass
class NoInlineProductionConstantsCheck(Check):
    """BE.2, approach 3 of BE.0's spike — flag string literals inside
    ``ast.Assert`` statements that match a known src/ UPPER_SNAKE
    module-level constant. Catches the "test inlines a production
    constant" drift class (sheet names, dataset IDs, sentinel
    values, etc.) — a rename in src silently leaves the test
    asserting the stale string.

    **STAGED DISABLED in this commit** — the BE.0 spike measured 144
    current hits across the corpus. Enabling now would red the whole
    tree. BE.4 sweeps these into either imports (preferred, ~60),
    allowlists with WHY (~40), or src refactors (~10). After BE.4's
    sweep, the registration in ``_build_checks`` un-comments and the
    lint enforces 0 hits going forward.

    The planted-fixture smoke test invokes the Check directly so the
    staged-disabled state doesn't degrade lint-stay-wired confidence.
    """
    src_root: Path = field(default_factory=lambda: REPO_ROOT / "src/recon_gen")
    _src_index: dict[str, tuple[Path, int, str]] = field(
        default_factory=dict, init=False, repr=False,
    )

    def _build_src_index(self) -> None:
        if self._src_index:
            return
        self._src_index = _collect_src_module_constants(self.src_root)

    def find_smells(self, src: str, tree: ast.AST, file: Path) -> Iterable[Smell]:
        self._build_src_index()
        v = _NoInlineProductionConstantsVisitor(file, self._src_index)
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
        REPO_ROOT / "src/recon_gen/common/db.py",
        REPO_ROOT / "src/recon_gen/common/html/_sql_executor.py",
        REPO_ROOT / "src/recon_gen/common/html/_tree_fetcher.py",
        REPO_ROOT / "src/recon_gen/common/html/server.py",
        REPO_ROOT / "src/recon_gen/common/config.py",
    ]
    # envvar-bypass spans src/ + tests/ (both have env access). The
    # registry itself + its unit test are the two legit consumers of
    # raw os.environ — whitelisted via path exclusion below.
    envvar_scope = [
        p for p in (
            _expand_paths([REPO_ROOT / "src/recon_gen"])
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
            _expand_paths([REPO_ROOT / "src/recon_gen"])
            + _expand_paths([REPO_ROOT / "tests"])
        )
        if p.name != "test_typing_smells.py"
        and p.name != "test_env_keys.py"
    ]
    # no-playwright-leak: every ``tests/e2e/`` file EXCEPT the driver layer
    # (which legitimately wraps Playwright) and the X.2.q.3 migration
    # backlog (``_PLAYWRIGHT_LEAK_LEGACY`` — shrinks as files get ported).
    # ``tests/js/`` is its own JS-unit harness and isn't under ``tests/e2e/``,
    # so it's out of scope automatically.
    e2e_drivers_dir = REPO_ROOT / "tests/e2e/_drivers"
    no_playwright_scope = [
        p for p in _expand_paths([REPO_ROOT / "tests/e2e"])
        if e2e_drivers_dir not in p.parents
        and str(p.relative_to(REPO_ROOT)) not in _PLAYWRIGHT_LEAK_LEGACY
    ]
    # no-hidden-in-e2e: scoped TIGHTLY to ``tests/e2e/test_*.py`` (top-
    # level e2e test bodies) so the driver layer's legitimate hidden-
    # input bridging stays exempt + the renderer-emission unit tests
    # (under ``tests/unit/``) keep asserting the HTML wire format
    # (which legitimately contains the string "hidden"). BH.24-class
    # lint per user 2026-05-25.
    no_hidden_scope = [
        p for p in _expand_paths([REPO_ROOT / "tests/e2e"])
        if p.parent == REPO_ROOT / "tests/e2e"
        and p.name.startswith("test_")
        and p.suffix == ".py"
    ]
    # BC.1 D7 — no-naked-interval-ctor: spans src/ + tests/, except the
    # wrapper module itself (where the bare constructors ARE the
    # underlying primitives that the named classmethods delegate to).
    intervals_module = REPO_ROOT / "src/recon_gen/common/intervals.py"
    _naked_interval_scope = [
        p for p in (
            _expand_paths([REPO_ROOT / "src/recon_gen"])
            + _expand_paths([REPO_ROOT / "tests"])
        )
        if p != intervals_module
    ]
    # BC.1 D8 (STAGED DISABLED until end of BC.5) — no-raw-temporal-args:
    # src/recon_gen/** function/method param annotations. Built but
    # currently unused — the registration is commented out below.
    _raw_temporal_scope = [
        p for p in _expand_paths([REPO_ROOT / "src/recon_gen"])
        if p != intervals_module
    ]
    # Keep _raw_temporal_scope referenced so the linter doesn't complain
    # about an unused local while the check is staged disabled.
    _ = _raw_temporal_scope
    # BE.1 — no-test-src-sql-duplication: every ``tests/`` .py file
    # EXCEPT the planted-fixture directory (which deliberately holds
    # the planted duplicate that the smoke test invokes the visitor
    # against directly). Per BE.0 D8: the fixtures sit OUTSIDE the
    # lint's normal scope so they don't self-trip the rule.
    fixtures_dir = REPO_ROOT / "tests/unit/_fixtures"
    no_test_src_sql_dup_scope = [
        p for p in _expand_paths([REPO_ROOT / "tests"])
        if fixtures_dir not in p.parents
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
                "monkeypatch.setenv|delenv with a RECON_GEN_/RECON_E2E_ "
                "(or legacy QS_GEN_/QS_E2E_) string literal — use the "
                "typed EnvVar registry at common/env_keys.py instead"
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
                REPO_ROOT / "src/recon_gen/common/l2/seed.py",
                REPO_ROOT / "src/recon_gen/common/l2/auto_scenario.py",
                REPO_ROOT / "src/recon_gen/apps",
            ]),
        ),
        Boto3DirectCheck(
            name="boto3-direct",
            description=(
                "direct ``boto3.client(...)`` outside the 5 known "
                "production wrappers — bypasses the ManagedBy tagging "
                "convention; route through ``common/deploy.py``, "
                "``common/cleanup.py``, ``common/browser/helpers.py``, "
                "``common/aws_rds.py``, or ``_dev/runner.py`` instead"
            ),
            files=[
                p for p in _expand_paths(
                    [REPO_ROOT / "src/recon_gen"]
                )
                if p != REPO_ROOT / "src/recon_gen/common/deploy.py"
                and p != REPO_ROOT / "src/recon_gen/common/cleanup.py"
                and p != REPO_ROOT / "src/recon_gen/common/browser/helpers.py"
                and p != REPO_ROOT / "src/recon_gen/common/aws_rds.py"
                and p != REPO_ROOT / "src/recon_gen/_dev/runner.py"
            ],
        ),
        ReconPrefixCheck(
            name="recon-prefix",
            description=(
                "hardcoded ``recon-<env>-...`` deployment-prefix literal "
                "in src code — use ``cfg.prefixed(<name>)`` so the "
                "operator's deployment_name is woven in (multi-tenant "
                "scoping). Bare ``recon-gen`` (package/CLI) is allowed."
            ),
            files=[
                p for p in _expand_paths(
                    [REPO_ROOT / "src/recon_gen"]
                )
                if p != REPO_ROOT / "src/recon_gen/common/config.py"
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
                    [REPO_ROOT / "src/recon_gen"]
                )
                if not str(p.relative_to(REPO_ROOT)).startswith(
                    "src/recon_gen/_dev/runner"
                )
                and not str(p.relative_to(REPO_ROOT)).startswith(
                    "src/recon_gen/cli/audit/"
                )
                and p != REPO_ROOT / "src/recon_gen/common/sheets/app_info.py"
                and p != REPO_ROOT / "src/recon_gen/common/provenance.py"
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
        ModuleNondeterminismCheck(
            name="test-module-nondeterminism",
            description=(
                "``random.X()`` / ``secrets.X()`` / "
                "``datetime.now()`` at module top level in any "
                "test file — produces a different value per "
                "pytest-xdist worker process. Caught the m.5 fix-up "
                "bug where ``test_l2_seed_contract.py`` ran "
                "``secrets.randbits(32)`` at import → xdist workers "
                "disagreed on parametrize ids → 'Different tests "
                "collected between gw0 and gwN'."
            ),
            files=_expand_paths([REPO_ROOT / "tests"]),
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
                REPO_ROOT / "src/recon_gen/cli",
                REPO_ROOT / "src/recon_gen/common",
            ]),
        ),
        TreeDataclassCheck(
            name="tree-dataclass",
            description=(
                "tree-pattern dataclasses must specify "
                "``frozen=True`` (value-type leaves) or "
                "``eq=False`` (mutable parents in the cross-ref "
                "graph) — bare ``@dataclass`` gives structural "
                "equality on mutable state, breaking object-ref "
                "identity"
            ),
            files=_expand_paths([REPO_ROOT / "src/recon_gen/common/tree"]),
        ),
        CreateTagsCheck(
            name="create-tags",
            description=(
                "boto3 QuickSight ``create_*`` calls must carry "
                "the ``ManagedBy: recon-gen`` tag — pass "
                "``Tags=[...]`` directly or spread a built "
                "payload via ``**payload``"
            ),
            files=[REPO_ROOT / "src/recon_gen/common/deploy.py"],
        ),
        NoPlaywrightLeakCheck(
            name="no-playwright-leak",
            description=(
                "``import playwright`` / ``from playwright import`` / "
                "``from common.browser.helpers|screenshot import`` (the "
                "Playwright-primitives layer) in a ``tests/e2e/`` file "
                "outside the driver layer (``tests/e2e/_drivers/``). "
                "Playwright stays sealed behind ``DashboardDriver`` — "
                "tests talk driver verbs, not ``Page`` / ``webkit_page`` "
                "/ ``wait_for_*``. The ``_PLAYWRIGHT_LEAK_LEGACY`` set is "
                "the X.2.q.3 migration backlog (it can only shrink)."
            ),
            files=no_playwright_scope,
        ),
        NoHiddenInE2ECheck(
            name="no-hidden-in-e2e",
            description=(
                "any string literal containing ``hidden`` (case-"
                "insensitive) in a ``tests/e2e/test_*.py`` file. "
                "v11.21.0 cold-read finding #2 + AI.12's WebKit fill-"
                "on-hidden quirk are the same bug class — hidden DOM "
                "details get fragile around. Drive user-facing locators; "
                "let the driver layer bridge to hidden inputs."
            ),
            files=no_hidden_scope,
        ),
        NoNakedIntervalCtorCheck(
            name="no-naked-interval-ctor",
            description=(
                "bare ``DateInterval(...)`` / ``DateTimeInterval(...)`` / "
                "``SingleDayPlant(...)`` / ``MultiDayPlant(...)`` "
                "construction outside ``common/intervals.py`` — wiring "
                "sites must use a named-convention classmethod so the "
                "call site declares its policy (closed vs trailing, "
                "single-day vs multi-day, at_window_end vs spans). Per "
                "feedback_invariants_in_types: types bring meaning with "
                "them, convention hides it."
            ),
            files=_naked_interval_scope,
        ),
        NoTestSrcSqlDuplicationCheck(
            name="no-test-src-sql-duplication",
            description=(
                "string literal in ``tests/`` (whitespace-normalized, "
                "≥100 chars) that also appears in ``src/recon_gen/`` — "
                "the test should import the constant from src/ instead "
                "of copying. Catches the BE-class regression: production "
                "SQL drifts but the test's copy doesn't, so the test "
                "keeps passing against a stale contract. Allowlist via "
                "``# typing-smell: ignore[no-test-src-sql-duplication]: "
                "<why>`` when the test deliberately holds a contract "
                "independent of src/ (rare). BE.0 spike measured the "
                "current corpus at 0 hits — this lint locks that "
                "baseline as a future-drift guard."
            ),
            files=no_test_src_sql_dup_scope,
        ),
        # BC.1, D8 — STAGED DISABLED until end of BC.5. To enable: drop
        # the ``# `` comment from the registration below. Enabling it
        # before the BC.4/BC.5 migration completes will red the whole
        # tree (every callsite that takes a raw ``date`` / ``datetime``
        # parameter — and there are dozens). The migration wraps each
        # callsite into a ``DateInterval`` / ``DateTimeInterval`` /
        # ``SingleDayPlant`` / ``MultiDayPlant`` / ``RunContext`` (BD).
        # NoRawTemporalArgsCheck(
        #     name="no-raw-temporal-args",
        #     description=(
        #         "function/method parameter annotated raw ``date`` / "
        #         "``datetime`` in src/recon_gen/** — wrap in "
        #         "``DateInterval`` / ``DateTimeInterval`` / "
        #         "``SingleDayPlant`` / ``MultiDayPlant`` / "
        #         "``RunContext`` (BD). Dataclass fields exempt (point "
        #         "values, not policy)."
        #     ),
        #     files=_raw_temporal_scope,
        # ),
        # BE.2 — ENABLED 2026-05-26 (BE.4.C). The Phase A spike found
        # 144 hits; Phase B's three parallel agents migrated 129 to
        # direct imports + flagged 15 for principal review; Phase C
        # applied per-line suppressions with WHY on 11 illustrative-
        # literal cases + refactored 4 chart-renderer fixtures to
        # neutral strings. Net: 0 unsuppressed hits. The lint now
        # locks the migrated baseline + catches future drift.
        NoInlineProductionConstantsCheck(
            name="no-inline-production-constants",
            description=(
                "string literal inside an ``assert`` in tests/ that "
                "matches a module-level UPPER_SNAKE constant in "
                "src/recon_gen/** — import the constant instead of "
                "inlining the value, so a rename in production fires "
                "the test loudly. Catches the provenance-drift class "
                "(sheet names, dataset IDs, sentinel values). BE.4 "
                "swept the corpus to 0 unsuppressed hits on "
                "2026-05-26. Allowlist via ``# typing-smell: ignore"
                "[no-inline-production-constants]: <why>`` for "
                "deliberate contract-independence cases."
            ),
            files=no_test_src_sql_dup_scope,
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


# ---------------------------------------------------------------------------
# Planted-fixture smoke tests (BE.0 D8)
# ---------------------------------------------------------------------------
#
# Each lint that's intended to land at 0 hits in the real corpus
# (BE.1, the future BE.2, etc.) needs a positive smoke test against a
# planted-violation fixture. Without it, a regex breakage, AST-walker
# traversal bug, or indexing-loop bug could silently flip the lint
# from "catches drift" to "always-empty" and we'd never know.
#
# Pattern: physical fixture files under ``tests/unit/_fixtures/`` that
# are EXCLUDED from the lint's normal ``check.files`` scope (via the
# ``fixtures_dir`` filter in ``_build_checks``). The smoke test then
# invokes the visitor directly on the fixture content + asserts the
# expected hit count. Both directions of the contract — the lint
# scope excludes fixtures (so a real run is unaffected); the smoke
# test invokes directly (so a regression flips the smoke red).


def test_be_1_no_test_src_sql_duplication_finds_planted_dup() -> None:
    """BE.0 D8 smoke test for ``no-test-src-sql-duplication``.

    Construct the check pointed at ``tests/unit/_fixtures/`` as both
    the src-side index source AND the file to walk; assert it finds
    exactly the planted duplicate.

    If this regresses (visitor stops walking, fingerprint normalizer
    drifts, src-index lookup breaks), the smoke goes red even when
    the real-corpus lint reports 0 (which it always should — that's
    the point of the planted-fixture invariant).
    """
    fixtures_dir = REPO_ROOT / "tests/unit/_fixtures"
    test_fixture = fixtures_dir / "be_1_planted_test.py"
    src_fixture = fixtures_dir / "be_1_planted_src.py"
    assert test_fixture.exists(), (
        f"BE.1 smoke fixture missing: {test_fixture} — re-create per "
        f"BE.0 D8's planted-fixture contract"
    )
    assert src_fixture.exists(), (
        f"BE.1 smoke fixture missing: {src_fixture}"
    )

    check = NoTestSrcSqlDuplicationCheck(
        name="no-test-src-sql-duplication",
        description="smoke-test instance",
        files=[test_fixture],
        # Point the src-side index at the fixtures dir so the planted
        # _src.py file's PLANTED_SRC_SQL literal is indexed and the
        # planted _test.py file's identical literal trips the rule.
        src_root=fixtures_dir,
    )
    src = test_fixture.read_text(encoding="utf-8")
    tree = ast.parse(src)
    smells = list(check.find_smells(src, tree, test_fixture))

    assert len(smells) == 1, (
        f"BE.1 smoke expected exactly 1 hit on the planted fixture; "
        f"got {len(smells)}: {smells!r}. Either the visitor stopped "
        f"walking, the fingerprint normalizer drifted, or the src "
        f"index lookup broke."
    )
    smell = smells[0]
    assert smell.checker == "no-test-src-sql-duplication"
    assert "be_1_planted_src.py" in smell.message, (
        f"BE.1 smoke: expected the message to name the src-side "
        f"fixture, got {smell.message!r}"
    )


def test_be_2_no_inline_production_constants_finds_planted_dup() -> None:
    """BE.0 D8 smoke test for ``no-inline-production-constants``.

    Construct the check pointed at ``tests/unit/_fixtures/`` as the
    src-side index source AND walk ``be_2_planted_test.py``; assert
    it finds exactly the two planted inline duplicates (one for the
    public constant, one for the private).

    Critically the lint stays STAGED DISABLED in ``_build_checks()``
    until BE.4's sweep — this smoke is the ONLY signal that the
    visitor stays wired during the staged-disabled period. Without
    it, an AST-walker regression could silently flip the visitor to
    "always-empty" and we'd only catch it months later when
    enabling.
    """
    fixtures_dir = REPO_ROOT / "tests/unit/_fixtures"
    test_fixture = fixtures_dir / "be_2_planted_test.py"
    src_fixture = fixtures_dir / "be_2_planted_src.py"
    assert test_fixture.exists(), (
        f"BE.2 smoke fixture missing: {test_fixture}"
    )
    assert src_fixture.exists(), (
        f"BE.2 smoke fixture missing: {src_fixture}"
    )

    check = NoInlineProductionConstantsCheck(
        name="no-inline-production-constants",
        description="smoke-test instance",
        files=[test_fixture],
        src_root=fixtures_dir,
    )
    src = test_fixture.read_text(encoding="utf-8")
    tree = ast.parse(src)
    smells = list(check.find_smells(src, tree, test_fixture))

    # 4 hits expected: 2 per function (the actual value + the
    # equality-comparison RHS), times 2 functions. The dedup-by-line
    # filter only suppresses repeated literals on the SAME line — the
    # assignment + assert on different lines both surface.
    #
    # Actually 4 is overly conservative; the function body is:
    #   actual = "be_2_planted_sentinel_value"      # line N
    #   assert actual == "be_2_planted_sentinel_value", "..."  # line N+1
    # Only the assert's literal is inside an Assert node; the
    # assignment's literal is at function scope, not under an Assert.
    # So 1 hit per function, 2 functions = 2 hits.
    assert len(smells) == 2, (
        f"BE.2 smoke expected exactly 2 hits on the planted fixture "
        f"(one per planted-inline assert); got {len(smells)}: "
        f"{[(s.lineno, s.message[:60]) for s in smells]!r}. Either "
        f"the Assert-walker regressed, the UPPER_SNAKE name regex "
        f"changed, or the constant-value index lookup broke."
    )
    checkers = {s.checker for s in smells}
    assert checkers == {"no-inline-production-constants"}, checkers
    messages = " ".join(s.message for s in smells)
    assert "PLANTED_PROD_CONSTANT" in messages, (
        f"BE.2 smoke: expected the public planted constant name "
        f"in some smell message; got {messages!r}"
    )
    assert "_PLANTED_PRIVATE_PROD_CONSTANT" in messages, (
        f"BE.2 smoke: expected the private planted constant name "
        f"in some smell message; got {messages!r}"
    )
