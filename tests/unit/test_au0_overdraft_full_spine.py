"""AU.0 spike — overdraft end-to-end through the AS-promoted spine.

The first AU-phase deliverable. Proves the spine SHAPE (now landed in
`src/recon_gen/common/spine/` by AS.1-AS.7) generalizes from drift — a
multi-row arithmetic invariant with a many-to-many edge to ledger_drift —
to **overdraft**: a single-row witness, no parent dependency, no
multi-leg arithmetic, no edge to any other detector. Different shape,
SAME spine.

Why overdraft as AU's pilot (per the AU phase preamble + the audit's
"AS.0 result" subsection): overdraft is the next-simplest L1 after drift —
the matview is a one-line `WHERE stored < 0` filter on
`current_daily_balances`, not an arithmetic comparison across legs +
stored. If the spine can absorb a structurally distinct invariant
without a Protocol change, the type shape AS pinned is durable. If it
can't, AU.0 catches the gap BEFORE AU.1 promotes.

Spike scope (what this proves):

- The promoted spine vocabulary (`Violation` / `Invariant` /
  `ViolationGenerator`) is shape-agnostic — `OverdraftInvariant` +
  `OverdraftGenerator` satisfy both Protocols without specialization
  hooks the AS impls didn't already need.
- Overdraft's `scenario_for(role)` resolves cleanly against the spec
  example shape — ANY internal account can overdraft (no leaf/parent
  filter the way drift requires), so the smart-constructor's role
  filter is strictly weaker than drift's.
- Non-violating IS the same generator with `magnitude=0.0` — same
  AP.2-shaped knob the drift spike pinned. Overdraft inherits the
  convention.
- Overdraft requires ONLY a daily_balances row to manifest (no
  transactions). That's a real DIFFERENCE from drift, and the spike
  pins it via the `_TX_COLS` row-count check — both as documentation
  AND as a regression guard if AU.1's promotion grows accidental
  transaction emission.
- **(SPIKE FINDING — corrected mid-write)** Overdraft on a LEAF
  internal account ALSO carries a many-to-many edge: it trips
  `OverdraftInvariant` AND `DriftInvariant`. Mechanism: drift's
  matview filter is `parent_role IS NOT NULL` AND `stored ≠ Σ posted
  legs`. The overdraft plant satisfies BOTH (the leaf has a parent
  role; the plant emits stored=−magnitude with zero transactions, so
  Σ legs = 0 ≠ −magnitude). The edge is not drift-specific structural
  exotica — it falls out of overlapping base-table predicates between
  independent matviews. **AU.1 must register
  `OverdraftGenerator → (OverdraftInvariant, DriftInvariant)`**, same
  shape as `DriftGenerator → (DriftInvariant, LedgerDriftInvariant)`.
  The spike pins this by asserting overdraft fires AND drift fires
  AND ledger_drift does NOT (the planted leaf isn't a parent role).
  That's the empirical edge — AU.1's promotion records the bookkeeping.
- Drift's substitution-path checklist (AR.5 lesson) extends — overdraft
  reads the matview directly via a static SQL, same as drift, zero
  `<<$param>>` substitution surface, zero AR.5 risk.

Spike scope (what this does NOT prove):

- Promotion to `src/` (AU.1 work — the spike vocab stays LOCAL by
  spike discipline, matching AS.0's pattern).
- Composition with `DriftGenerator` in one `LedgerSimulation` (AU.2 —
  the spine-scales-past-one-invariant gate the user explicitly asked
  for; AU.0 sets up the type shape that AU.2 composes).
- Live-deploy agreement (AU MANDATORY GATE — added in AU's analogue of
  AS.6 once OverdraftInvariant lands in src/).
- The remaining L1 invariants (`expected_eod_balance_breach` /
  `stuck_pending` / `stuck_unbundled` / `limit_breach`). AU.0's
  promotion-order conclusion guides AU.3-4.

The AU.0 audit subsection (`docs/audits/date_range_model_audit.md` §5
"AU.0 result") captures the lessons that carry past drift.
"""

from __future__ import annotations

from decimal import Decimal

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame
from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    Invariant,
    Violation,
    ViolationGenerator,
)
from recon_gen.common.sql import Dialect
from recon_gen.common.tree import DateView

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


# ---------------------------------------------------------------------------
# Overdraft — local concrete spine impls (AU.1 promotes verbatim). The shape
# proposal for `src/recon_gen/common/spine/overdraft.py`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverdraftInvariant:
    """Non-negative-stored-balance detector. Persona-blind — the matview
    SQL is `WHERE money < 0` on every internal account, no role join.
    `scenario_for(role)` filters the L2 by role only; ANY scope=internal
    account qualifies (no parent-role requirement that drift carries).

    Production-shape note for AU.1: the prefix carries onto every
    promoted Invariant. Spike defaults to `spec_example` so the in-process
    harness round-trips against the bundled L2 yaml.
    """

    # ClassVar so the frozen-dataclass field set stays empty for the
    # Protocol's read-only `name` contract. Matches DriftInvariant's
    # shape from `common/spine/drift.py`.
    name: str = "overdraft"  # spike-local; AU.1 promotes as ClassVar
    prefix: str = _PREFIX

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, business_day_start, stored_balance "
            f"FROM {self.prefix}_overdraft",
        ).fetchall()
        # AO.1: stored_balance is BIGINT cents — project to dollars at
        # the detect boundary (mirror of OverdraftInvariant.detect).
        from recon_gen.common.money import Cents
        return {
            Violation.of(
                "overdraft",
                account_id=aid,
                business_day=_to_date(bds),
                stored_balance=round(
                    float(Cents.from_db(int(sb)).to_dollars()), 2,
                ),
            )
            for aid, bds, sb in rows
        }

    def scenario_for(
        self,
        role: str,
        *,
        magnitude: float = 5.0,
        instance: L2Instance | None = None,
    ) -> "OverdraftGenerator":
        """Resolve a role against the shape and return a generator that
        manufactures a stored-balance overdraft on the first internal
        account with that role.

        Magnitude is "how far below zero the planted stored is" —
        magnitude=5.0 ⇒ stored=-5.0 ⇒ overdraft fires; magnitude=0.0 ⇒
        stored=0.0 ⇒ clean (non-violating, matching AP.2's convention).
        Caller-friendly sign so the spine API reads positively.

        Raises `ValueError` if the L2 has no internal account with the
        requested role — same smart-constructor discipline drift uses
        (the invariant owns shape resolution, fails loud at the request
        site, never silently emits inert rows).
        """
        inst = instance if instance is not None else _spec_example()
        candidates = [
            a for a in inst.accounts
            if getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
        ]
        if not candidates:
            raise ValueError(
                f"shape has no overdraft-eligible internal account with role "
                f"{role!r}; cannot manufacture an overdraft scenario"
            )
        acct = candidates[0]
        return OverdraftGenerator(
            account_id=f"acct-overdraft-{role}",
            account_role=role,
            account_parent_role=(
                str(getattr(acct, "parent_role"))
                if getattr(acct, "parent_role", None) is not None
                else None
            ),
            anchor_day=LOCKED_ANCHOR,
            magnitude=magnitude,
        )


@dataclass
class OverdraftGenerator:
    """Emit a daily_balances row whose `money` is below zero by
    `magnitude`. NO transactions — overdraft's matview reads
    `current_daily_balances` directly; only the balance row is needed.

    Per the AP.2 knob convention: `magnitude=0.0` means the
    perturbation is OFF; the emitted row has money=0, which is NOT < 0,
    so overdraft does NOT fire. That's the non-violating shape.
    """

    account_id: str
    account_role: str
    account_parent_role: str | None
    anchor_day: date
    magnitude: float

    @property
    def intended(self) -> Violation:
        # stored_balance is the planted negative balance; magnitude is
        # caller-facing positive ("how far below zero"). The Violation's
        # identity uses the actual matview value (negative).
        return Violation.of(
            "overdraft",
            account_id=self.account_id,
            business_day=self.anchor_day,
            stored_balance=round(-self.magnitude, 2),
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        start, end = _day_bounds(self.anchor_day)
        _insert_balance(
            conn,
            account_id=self.account_id,
            account_name=f"Overdraft Acct ({self.account_role})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=self.account_parent_role,
            business_day_start=start,
            business_day_end=end,
            money=-self.magnitude,
        )


# ---------------------------------------------------------------------------
# In-process harness — mirrors AS.0; AS.2's drift.py uses the same shape.
# ---------------------------------------------------------------------------


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


def _spec_example() -> L2Instance:
    return load_instance(_SPEC_EXAMPLE)


_DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money",
)


def _insert_balance(conn: sqlite3.Connection, **vals: object) -> None:
    # AO.1: money + expected_eod_balance are BIGINT cents. Author-side
    # floats convert at the write boundary so this local helper mirrors
    # `insert_balance` in `common/spine/_emit_helpers.py`.
    from recon_gen.common.money import Cents
    for col in ("money", "expected_eod_balance"):
        raw = vals.get(col)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            vals[col] = int(Cents.from_dollars(raw if isinstance(raw, int) else Decimal(str(raw))))
    placeholders = ", ".join("?" for _ in _DB_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_daily_balances ({', '.join(_DB_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _DB_COLS],
    )


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _to_date(bds: object) -> date:
    return datetime.strptime(str(bds)[:10], "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# The end-to-end slice. ALL spine types in one test.
# ---------------------------------------------------------------------------


def test_overdraft_threads_the_full_spine() -> None:
    """The AU.0 proving ground: overdraft threaded through every spine
    type, real emitted overdraft matview SQL, in-process. If this
    round-trips, the spine generalizes past drift; AU.1 can promote
    OverdraftInvariant + OverdraftGenerator with confidence."""
    # ---- Invariant: owns detect + scenario_for ----------------------------
    inv = OverdraftInvariant()
    assert inv.name == "overdraft"
    # Promoted Protocol satisfaction is structural — pin it explicitly so
    # the spike acts as the AU.1 promotion contract.
    assert isinstance(inv, Invariant)

    # ---- scenario_for(role) → ViolationGenerator --------------------------
    gen = inv.scenario_for("CustomerSubledger", magnitude=5.0)
    assert gen.account_role == "CustomerSubledger"
    assert isinstance(gen, ViolationGenerator)
    intended = gen.intended

    # ---- Stateful-fold-equivalent (single-day for overdraft) --------------
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)

        # ---- Invariant.detect: the spine link --------------------------
        detected = inv.detect(conn)
        assert intended in detected, (
            f"overdraft detector did not confirm the intended violation.\n"
            f"  intended: {intended}\n"
            f"  detected: {detected}"
        )

        # ---- View presents the violation -------------------------------
        view = DateView(frame=AsOfFrame.locked())
        resolved = view.resolve_day([LOCKED_ANCHOR])
        assert resolved == LOCKED_ANCHOR
        assert view.is_satisfied_by([LOCKED_ANCHOR])
    finally:
        conn.close()


def test_overdraft_non_violating_is_same_generator_with_magnitude_zero() -> None:
    """AP.2 convention promoted to overdraft: magnitude=0 is the
    perturbation-off knob; stored=0 is NOT < 0; overdraft does NOT fire.
    The non-violating fold IS the same generator with the knob off,
    matching drift's AS.0 contract."""
    inv = OverdraftInvariant()
    clean = inv.scenario_for("CustomerSubledger", magnitude=0.0)
    dirty = inv.scenario_for("CustomerSubledger", magnitude=5.0)

    conn = _fresh_db()
    try:
        clean.emit(conn)
        conn.commit()
        _refresh(conn)
        assert dirty.intended not in inv.detect(conn)
    finally:
        conn.close()


def test_scenario_for_unknown_role_fails_loud() -> None:
    """Smart-constructor invariant promoted to overdraft: the invariant
    owns role resolution, fails loud at request time on a role the L2
    shape doesn't host. Matches drift's contract."""
    with pytest.raises(ValueError, match="no overdraft-eligible"):
        OverdraftInvariant().scenario_for("NoSuchRole", magnitude=5.0)


def test_view_anchored_at_frame_carries_one_anchor_through_the_spine() -> None:
    """The AR.1 promise still holds for overdraft: the generator's
    anchor IS the frame's `as_of` by construction; the view's
    `required_coverage` always contains the planted day. Plant ⟷
    query-window contract is structural, not per-invariant."""
    frame = AsOfFrame.locked(window_days=7)
    view = DateView(frame=frame)
    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)

    assert gen.anchor_day == frame.as_of
    lo, hi = view.required_coverage
    assert lo <= gen.anchor_day <= hi


def test_overdraft_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    """AR.5 lesson — substitution-path checklist — extends to overdraft.
    Its `detect()` reads `<prefix>_overdraft` via a static SQL with no
    `<<$param>>` substitution, so zero divergence risk between QS-bridge
    (typed value) and api/smoke (string literal). AU.1's promotion
    inherits this property; the per-promoted-invariant property test
    (AR.5 lesson codified) is satisfied for overdraft."""
    inv = OverdraftInvariant()
    conn = _fresh_db()
    try:
        captured: list[str] = []
        conn.set_trace_callback(captured.append)
        inv.detect(conn)
        conn.set_trace_callback(None)
    finally:
        conn.close()

    assert captured, "expected OverdraftInvariant.detect() to run ≥1 SQL"
    for sql in captured:
        assert "<<$" not in sql, (
            f"overdraft detector unexpectedly crossed a SQL-pushdown "
            f"surface; AR.5-style substitution-path test required.\n"
            f"  sql: {sql!r}"
        )


# ---------------------------------------------------------------------------
# Structural-DIFFERENCE pins — what overdraft DOES NOT share with drift.
# These are the AU.0-specific contracts that ground AU.2's composition test.
# ---------------------------------------------------------------------------


def test_overdraft_emission_requires_only_a_balance_row() -> None:
    """Overdraft is a balance-only invariant — the matview reads
    `current_daily_balances` directly, no leg arithmetic. The generator
    emits ZERO transaction rows. Pinning this explicitly so AU.1's
    promotion can't silently grow `_insert_tx` calls a future shape
    change would import."""
    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
        balance_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances",
        ).fetchone()[0]
    finally:
        conn.close()
    assert tx_count == 0, (
        f"OverdraftGenerator emitted {tx_count} transactions; expected 0 — "
        "overdraft is a balance-only invariant per the AU.0 spike contract"
    )
    assert balance_count == 1, (
        f"OverdraftGenerator emitted {balance_count} balance rows; expected 1"
    )


def test_overdraft_on_a_leaf_account_also_trips_drift() -> None:
    """AU.0 finding: the many-to-many edge structure is universal, not
    drift-specific. An overdraft planted on a LEAF internal account trips
    BOTH `OverdraftInvariant` AND `DriftInvariant`, because drift's
    matview filter (`parent_role IS NOT NULL` AND `stored ≠ Σ posted
    legs`) is naturally satisfied by the overdraft plant — leaf has a
    parent role; the plant emits stored=−magnitude with ZERO transactions,
    so Σ legs = 0 ≠ −magnitude. The edge falls out of overlapping base-
    table predicates between two independent matview SELECTs, not from
    any special drift-side construction.

    What this means for AU.1:

    - `INVARIANT_GENERATOR_EDGES` must register
      `OverdraftGenerator: (OverdraftInvariant, DriftInvariant)` —
      mirroring DriftGenerator's two-edge registration in `registry.py`.
    - The promotion-order lesson generalizes: every new
      `ViolationGenerator` lands with an empirical multi-matview
      detect-sweep check, NOT a structural prediction of "only my own
      invariant fires." That check IS this test, parametrized by the
      generator + the full known-invariant set.

    What about ledger_drift? It does NOT fire here, because the planted
    leaf isn't a parent role — `_computed_ledger_balance` only emits for
    accounts with children, so the ledger_drift matview's JOIN produces
    no row for the leaf. AU.2's composition test will plant overdraft on
    a parent role separately to confirm the parent-side edge.
    """
    from recon_gen.common.spine import DriftInvariant, LedgerDriftInvariant

    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        overdraft_detected = OverdraftInvariant().detect(conn)
        drift_detected = DriftInvariant().detect(conn)
        ledger_drift_detected = LedgerDriftInvariant().detect(conn)
    finally:
        conn.close()
    # The PRIMARY claim — overdraft fires for the planted Violation.
    assert gen.intended in overdraft_detected
    # The SECONDARY edge — drift fires too, on the same account/day.
    expected_drift = Violation.of(
        "drift",
        account_id=gen.account_id,
        business_day=gen.anchor_day,
        # Drift magnitude = stored − Σ legs = −magnitude − 0 = −magnitude.
        drift=round(-gen.magnitude, 2),
    )
    assert expected_drift in drift_detected, (
        f"OverdraftGenerator should trip DriftInvariant on a leaf account; "
        f"expected {expected_drift} in {drift_detected}"
    )
    # Ledger-drift does NOT fire — the leaf isn't a parent role, so the
    # parent-side matview has no row for it. AU.2 covers parent-role plant.
    assert ledger_drift_detected == set(), (
        f"OverdraftGenerator unexpectedly tripped LedgerDriftInvariant on a "
        f"leaf-account plant: {ledger_drift_detected}"
    )
