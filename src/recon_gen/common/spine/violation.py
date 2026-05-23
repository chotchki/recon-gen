"""`Violation` — the currency type of the invariant spine.

A detected breach. Identity is the analyst-facing columns naming the
violation: account + day + magnitude for drift; pair + sigma band for
anomaly; root_transfer + depth for a money trail. Two `Violation`s
compare equal iff they name the same breach, REGARDLESS of which
detector emitted them or which generator was the cause.

Why a frozenset of `(column, value)` pairs and not a fixed schema:
different invariants have different identity columns. A drift row IS
`(account_id, business_day, drift_amount)`; an anomaly row is
`(sender_id, recipient_id, sigma_band)`. A fixed schema would force a
common denominator that loses information. The frozenset shape makes
equality column-aware while letting per-invariant identity vary.

Smart constructor `Violation.of(invariant, **identity)` is the only
public way to build one — it normalizes the identity to the frozenset
shape so two callers building "the same" violation with the same kwargs
produce equal instances. Direct construction works but is discouraged;
prefer `.of()`.

Promoted from `tests/unit/test_as0_drift_full_spine.py` by AS.1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    """A detected invariant breach.

    `invariant`: the invariant's `name` attribute (e.g. ``"drift"``,
        ``"ledger_drift"``, ``"inv_pair_rolling_anomalies"``). This is
        the matview suffix on the production side — the spine's link
        between a `Violation` and the SQL that produces it.

    `identity`: a frozenset of `(column, value)` pairs naming the
        breach. Always the analyst-facing columns (account_id,
        business_day, drift_amount), never auto-derived row internals
        (matview row PK, internal UUIDs).
    """

    invariant: str
    identity: frozenset[tuple[str, object]]

    @classmethod
    def of(cls, invariant: str, **identity: object) -> "Violation":
        """The blessed way to build a `Violation`.

        Keyword args become the identity columns. Order doesn't matter;
        two callers passing the same kwargs in any order produce equal
        instances (the frozenset takes care of canonicalization).

        Example::

            Violation.of("drift",
                         account_id="acct-1",
                         business_day=date(2030, 1, 1),
                         drift=5.00)
        """
        return cls(invariant=invariant, identity=frozenset(identity.items()))
