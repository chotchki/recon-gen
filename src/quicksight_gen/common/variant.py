"""Variant matrix primitives — Y.2.gate.m.

The runner expresses test variants as a 3-axis matrix:
``scenario × dialect × target``. Each cell is a `VariantSpec`, which
carries enough information for the chain to set up the right database,
seed the right L2 instance, and tag the right AWS resources without
collisions across parallel cells.

**Naming convention** (run-internal only — operators don't type these
except for single-cell triage via ``--variants=<code>``):

- ``<sc>_<di>_<ta>`` — three short components joined by ``_``.
- Scenario codes: ``sp`` (spec_example), ``sq`` (sasquatch_pr),
  ``f<n>`` (fuzz seed N), ``us`` (user-supplied yaml).
- Dialect codes: ``pg`` (postgres), ``or`` (oracle), ``sl`` (sqlite).
- Target codes: ``lo`` (local container), ``aw`` (AWS / operator's
  external Aurora).

Examples: ``sp_pg_lo``, ``f42_or_lo``, ``us_sl_lo``, ``sq_pg_aw``.

**Invalid cells** (handled by `VariantSpec.is_valid`):

- ``<any>_sl_aw`` — SQLite is file-based; QuickSight has no remote
  DataSource for it.

(``us_*_*`` is matrix-level excluded from ``expand_full()``; not a
cell-level invalid — operators can opt in via
``--scenarios=us:path/foo.yaml`` and the spec is well-formed.)

**Spike source:** ``docs/audits/y_2_gate_m_0_variant_matrix_spike.md``
(LOCKED 2026-05-08).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NewType

# Closed sets — Literal lets pyright reject unknown values at the
# constructor call site. Scenario is open (`f<n>` for any n) so it
# stays NewType + runtime regex validation.
DialectCode = Literal["pg", "or", "sl"]
TargetCode = Literal["lo", "aw"]
ScenarioCode = NewType("ScenarioCode", str)

# Scenario regex: sp | sq | us | f<digits>. Validated in `__post_init__`.
_SCENARIO_RE = re.compile(r"^(sp|sq|us|f\d+)$")

# Frozensets for quick membership checks at validation time.
NAMED_SCENARIOS: frozenset[str] = frozenset({"sp", "sq", "us"})
DIALECTS: frozenset[DialectCode] = frozenset({"pg", "or", "sl"})
TARGETS: frozenset[TargetCode] = frozenset({"lo", "aw"})


@dataclass(frozen=True)
class VariantSpec:
    """One cell of the variant matrix.

    Construction is validated: the scenario code must parse + the
    fuzz_seed / user_yaml fields must agree with the scenario kind.
    Attempting to construct an invalid spec raises ``ValueError`` at
    the call site (the runner's m.0 "knows an invalid case" contract
    for spec-level malformations; cell-level invalids — e.g.,
    ``sl × aw`` — surface via `is_valid` instead).
    """

    scenario: ScenarioCode
    dialect: DialectCode
    target: TargetCode
    # Set when scenario is ``f<n>``; the integer n. None otherwise.
    fuzz_seed: int | None = None
    # Set when scenario is ``us``; the operator-supplied L2 yaml path.
    # None otherwise.
    user_yaml: Path | None = None

    def __post_init__(self) -> None:
        if not _SCENARIO_RE.match(self.scenario):
            raise ValueError(
                f"invalid scenario code {self.scenario!r}; "
                f"expected sp, sq, us, or f<digits>"
            )
        if self.scenario.startswith("f"):
            if self.fuzz_seed is None:
                raise ValueError(
                    f"scenario {self.scenario!r} requires fuzz_seed to be set"
                )
            if self.user_yaml is not None:
                raise ValueError(
                    f"scenario {self.scenario!r} (fuzz) is mutex with user_yaml"
                )
            # Sanity: scenario suffix matches fuzz_seed value.
            if self.scenario != f"f{self.fuzz_seed}":
                raise ValueError(
                    f"scenario {self.scenario!r} doesn't match fuzz_seed={self.fuzz_seed} "
                    f"(expected scenario=f{self.fuzz_seed})"
                )
        elif self.scenario == "us":
            if self.user_yaml is None:
                raise ValueError(
                    "scenario 'us' requires user_yaml to be set"
                )
            if self.fuzz_seed is not None:
                raise ValueError(
                    "scenario 'us' is mutex with fuzz_seed"
                )
        else:
            # Named scenarios (sp, sq) — both extras must be None.
            if self.fuzz_seed is not None or self.user_yaml is not None:
                raise ValueError(
                    f"scenario {self.scenario!r} doesn't take "
                    f"fuzz_seed or user_yaml"
                )

    @property
    def name(self) -> str:
        """Stable variant code: ``<sc>_<di>_<ta>``. Used as artifact dir
        name (``runs/<id>/<variant>/``), DB schema prefix discriminator,
        and AWS resource ``L2Instance:<variant>`` tag value."""
        return f"{self.scenario}_{self.dialect}_{self.target}"

    def is_valid(self) -> bool:
        """Cell-level validity. Returns False for known-impossible
        combinations the matrix expander should skip:

        - ``sl × aw``: SQLite is file-based; QuickSight can't reach it
          via a remote DataSource.

        Spec-level malformations (e.g., scenario doesn't match
        fuzz_seed) raise in ``__post_init__`` — those never construct.
        """
        if self.dialect == "sl" and self.target == "aw":
            return False
        return True


# --- matrix expanders -----------------------------------------------------


def derive_default_fuzz_seed() -> int:
    """Random fuzz seed for the default ``full`` matrix (and any other
    fuzz cell that doesn't have a pinned seed).

    Per audit §7.11 LOCKED + m.0 spike + m.3 PLAN direction: random
    by default, different per chain invocation. Seed is captured in
    each cell's manifest (m.3 wires manifest writes); operator pins
    via ``--variants=f<seed>_<di>_<ta>`` for one-line reproduction.

    `secrets.randbelow(2**32)` rather than ``random.X``: cryptographic
    RNG, no module-level state, won't trip the
    ``b.15.lint.determinism`` check on accidental seed-module use.
    """
    return secrets.randbelow(2**32)


def expand_full() -> list[VariantSpec]:
    """The 13-cell ``full`` default matrix (spike §"`full` matrix
    definition", LOCKED 2026-05-08).

    Cells:

    - 6 named-scenario × all-dialects × local: ``{sp, sq} × {pg, or, sl} × {lo}``
    - 4 named-scenario × non-sqlite × aws:    ``{sp, sq} × {pg, or} × {aw}``
    - 3 fuzz-seed × all-dialects × local:    ``{f<seed>} × {pg, or, sl} × {lo}``

    The fuzz cells share **one** random seed across the 3 dialect cells
    so the same synthesized L2 topology gets exercised on PG / Oracle /
    SQLite — cross-dialect coverage on identical input. ``--scenarios=fuzz:N``
    (m.3 sub-flag composer territory) ramps this to N seeds × |dialect-axis|.

    Excluded from default ``full`` per spike §"Invalid cells":

    - ``us_*_*``: requires operator yaml; opt-in via ``--scenarios=us:<path>``.
    - Fuzz on ``aw``: cost-control default; reachable via explicit
      ``--scenarios=fuzz:N --targets=aw``.
    - ``<any>_sl_aw``: invalid cell (caught by `is_valid`); never
      constructed here.

    Caller-side invariant: every returned spec satisfies ``is_valid()``.
    """
    cells: list[VariantSpec] = []

    # Named scenarios × all dialects × local — 2 × 3 = 6 cells.
    for sc_named in ("sp", "sq"):
        for di_local in ("pg", "or", "sl"):
            cells.append(VariantSpec(ScenarioCode(sc_named), di_local, "lo"))

    # Named scenarios × non-sqlite dialects × aws — 2 × 2 = 4 cells.
    # (sl × aw excluded by `is_valid`; not constructed.)
    for sc_named in ("sp", "sq"):
        for di_aws in ("pg", "or"):
            cells.append(VariantSpec(ScenarioCode(sc_named), di_aws, "aw"))

    # 1 random fuzz seed × all dialects × local — 3 cells.
    # Same seed across dialects: cross-dialect coverage on identical L2.
    seed = derive_default_fuzz_seed()
    fuzz_code = ScenarioCode(f"f{seed}")
    for di_local in ("pg", "or", "sl"):
        cells.append(VariantSpec(fuzz_code, di_local, "lo", fuzz_seed=seed))

    return cells
