"""CB.5 stage 2 — shared helpers for the decomposed agreement tests.

The agreement tests (`test_audit_*_agreement.py`, `test_inv_*_agreement.py`)
were monolithic in CB.4: ONE test function drove every renderer and
asserted agreement inline. CB.5 stage 2 split them into renderer-tier
producers (db / app2 / qs_browser) that write JSON artifacts via
`tests/e2e/_agreement.py::write_rendered_rows`, plus high-watermark
validators at `qs_browser` tier that read the artifacts via
`read_rendered_rows` and assert agreement.

This module holds the shared setup logic each tier's producer
modules call — per-dialect cfg loading + skip rules, the L2 yaml
resolver, the audit window anchor — so the producer modules stay
short and the same-shape logic doesn't drift across files.

The dialect_cfg / seeded_audit fixture *wrappers* live in each
producer module's own conftest.py (one per tier dir) so pytest
fixture scoping stays clean — this module is just plain functions.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from recon_gen.common.intervals import DateInterval
from recon_gen.common.l2.primitives import SCOPE_INTERNAL
from recon_gen.common.sql import Dialect

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2 import L2Instance


def anomaly_pair_for_l2(instance: "L2Instance") -> tuple[str, str]:
    """CS.12 — pick a (sender_role, recipient_role) pair that exists in
    the L2 instance's accounts catalog. Returns the role names that the
    anomaly + money_trail tests pass into ``scenario_for``.

    Pre-CS.12 the tests hardcoded ``("CustomerSubledger",
    "CustomerSubledger")`` — fine on spec_example (which declares that
    role) but broke on sasquatch_pr (whose closest leaf-money role is
    ``CustomerDDA``). CS.12 walked the instance for the first of a
    NAME preference order (``CustomerSubledger`` → ``CustomerDDA``).

    DY.5 (backlog #239) broadens that to STRUCTURAL discovery with the
    name-preference kept as a determinism head:

    1. If the L2 declares an internal-leaf account under
       ``CustomerSubledger`` or (failing that) ``CustomerDDA``, return
       it — the default L2s stay pinned to their known role so nothing
       about their scenario shifts.
    2. Otherwise fall back to the FIRST internal-leaf role the L2
       declares (scope ``internal`` with a non-null ``parent_role``) —
       mirroring the db tier's structural ``_pick_internal_leaf_role``
       so a custom L2 with a differently-NAMED leaf-money role no longer
       skips just because it doesn't use our vocabulary.

    A usable role needs an internal-LEAF account: the recipient / hop
    leg calls ``find_internal_with_role(..., must_be_leaf=True)`` and the
    returned ``(role, role)`` self-pair reuses it for the sender leg.

    Only when the L2 declares NO internal-leaf account at all does this
    skip — a genuine data-conditional (the anomaly / money-trail
    invariants don't apply to a shape with no leaf-money account), not a
    coverage gap.
    """
    preferred = ("CustomerSubledger", "CustomerDDA")
    leaf_roles = [
        a.role
        for a in instance.accounts
        if a.scope == SCOPE_INTERNAL and a.parent_role is not None
    ]
    # DY.7.2 — template-driven L2s (sasquatch_pr) declare their internal-LEAF
    # accounts as account_templates (materialized to cust-NNNN only at seed
    # time), so they're absent from instance.accounts and the scan above finds
    # nothing → the pair used to SKIP on sasquatch. Include the template roles
    # so anomaly / money-trail RUN. `find_internal_with_role` has the matching
    # template fallback to resolve a representative account for the role.
    leaf_roles += [
        t.role
        for t in instance.account_templates
        if t.scope == SCOPE_INTERNAL and t.parent_role is not None
    ]
    for role in preferred:
        if role in leaf_roles:
            return role, role
    if leaf_roles:
        return leaf_roles[0], leaf_roles[0]
    pytest.skip(
        "L2 instance declares no internal-leaf account OR account_template "
        "(scope='internal' with a non-null parent_role) to anchor an anomaly "
        "/ money-trail scenario — the invariant genuinely doesn't apply to an "
        f"L2 with no leaf-money account (preferred roles checked: {preferred}). "
        "This is a data-conditional skip, not a coverage gap (DY.5 / DY.7.2 / "
        "#239)."
    )


# Bundled persona-neutral L2 — the same yaml every other e2e test
# defaults to when no per-cell override is set. Resolved at
# tests/l2/spec_example.yaml relative to the tests/ root.
_FIXTURES_DIR = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE_BUNDLED = _FIXTURES_DIR / "spec_example.yaml"


def l2_yaml_for_test() -> Path:
    """The L2 yaml every producer / validator in this decomposition
    targets. m.4.f honors the runner's per-cell synthesized yaml
    when `RECON_GEN_TEST_L2_INSTANCE` is set; otherwise falls back
    to the bundled `spec_example.yaml`.

    Z.C — the Y.2.gate.m runner used to set
    ``RECON_GEN_TEST_L2_INSTANCE`` to a per-cell synthesized yaml whose
    dropped ``instance`` field encoded the cell code (e.g.,
    ``sp_pg_aw``). With the field gone, the DB-table prefix lives on
    cfg.db.table_prefix and the QS-resource prefix lives on
    cfg.aws.deployment_name; this resolver only resolves the L2 yaml
    itself.
    """
    from recon_gen.common.env_keys import RECON_GEN_TEST_L2_INSTANCE
    env_val = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if env_val:
        return Path(env_val)
    return _SPEC_EXAMPLE_BUNDLED


# U.8.b.5 — Per-dialect config files. The matrix runs one cell per
# (invariant, dialect); each dialect's cell loads its own config.
_DIALECT_CONFIG_PATHS: dict[str, Path] = {
    "postgres": Path("run/config.postgres.yaml"),
    "oracle": Path("run/config.oracle.yaml"),
    # DY.2 — DuckDB joins the agreement matrix. Without this entry the
    # `_DIALECT_CONFIG_PATHS[dialect_name]` fallback KeyErrors on the
    # `duckdb` param whenever no env cfg matches.
    "duckdb": Path("run/config.duckdb.yaml"),
}


def env_demo_url_dialect() -> str | None:
    """m.4.c — sniff the dialect from `RECON_GEN_DEMO_DATABASE_URL`
    so a per-cell `lo`-target variant can skip mismatched
    parametrizations. Returns None when the env var is unset (e.g.,
    `aw` cell)."""
    from recon_gen.common.env_keys import RECON_GEN_DEMO_DATABASE_URL
    env_url = RECON_GEN_DEMO_DATABASE_URL.get_or_none()
    if env_url is None:
        return None
    if env_url.startswith(("postgres", "postgresql")):
        return "postgres"
    if env_url.startswith(("oracle", "oracle+oracledb")):
        return "oracle"
    # CB.14 followup: post-CA the runner spins du_lo cells with
    # `duckdb:///<tmpfile>` URLs. Tests that parametrize over
    # ("postgres", "oracle", ...) need to skip on those cells; the
    # missing arm here let them fall through and try to `psycopg.connect`
    # against a duckdb URL, surfacing as obscure parse errors in CI.
    if env_url.startswith("duckdb"):
        return "duckdb"
    return None


def load_dialect_cfg(dialect_name: str) -> "tuple[Config, Path, Dialect]":
    """Resolve `(cfg, cfg_path, dialect_enum)` for the named dialect,
    or call `pytest.skip` on any of the well-defined skip conditions:

    - per-cell env-URL or env-cfg dialect mismatch (the sibling cell
      handles the other dialect)
    - cfg yaml not present on disk (operator only has the other
      dialect set up locally)
    - cfg yaml has no `demo_database_url` (can't seed the DB)
    - cfg yaml's `dialect:` field disagrees with the requested name

    Same shape as the pre-CB.5 module's `dialect_cfg` fixture; lifted
    into a plain function so every tier's producer module can call it
    from its own fixture without re-implementing the skip logic.
    """
    from recon_gen.common.config import load_config
    from recon_gen.common.env_keys import RECON_GEN_CONFIG as _RGC

    env_url_dialect = env_demo_url_dialect()
    if env_url_dialect is not None and env_url_dialect != dialect_name:
        pytest.skip(
            f"runner cell's RECON_GEN_DEMO_DATABASE_URL implies "
            f"dialect={env_url_dialect!r}; this {dialect_name!r} "
            f"parametrization would route to the wrong DB. The sibling "
            f"sp_{env_url_dialect[:2]}_lo cell handles {dialect_name!r}."
        )
    # Y.2.browser.triage — `aw`-target analogue of the skip above. An
    # `aw` cell doesn't set RECON_GEN_DEMO_DATABASE_URL (uses the
    # operator's external Aurora/Oracle) but the runner DOES inject
    # RECON_GEN_CONFIG = the cell's *dialect* cfg.
    from recon_gen.common.env_keys import RECON_GEN_CONFIG
    qs_gen_cfg = RECON_GEN_CONFIG.get_or_none()
    if qs_gen_cfg is not None:
        low = str(qs_gen_cfg).lower()
        cfg_dialect = (
            "postgres" if "postgres" in low
            else "oracle" if "oracle" in low
            else "duckdb" if "duckdb" in low
            else None
        )
        if cfg_dialect is not None and cfg_dialect != dialect_name:
            pytest.skip(
                f"runner cell's RECON_GEN_CONFIG={qs_gen_cfg!r} implies "
                f"dialect={cfg_dialect!r}; this {dialect_name!r} cell would "
                f"walk tables it never seeded. The sibling sp_{cfg_dialect[:2]}_<tgt> "
                f"cell handles {dialect_name!r}."
            )
    # AB.7.1a — prefer the runtime `RECON_GEN_CONFIG` cfg when its
    # `dialect:` field matches the parametrize dialect. release.yml +
    # `aw`-target runner cells generate a synthesized cfg outside the
    # gitignored `run/` dir.
    cfg_path: Path | None = None
    env_cfg_path = _RGC.get_or_none()
    if env_cfg_path is not None:
        env_cfg_path_p = Path(env_cfg_path)
        if env_cfg_path_p.exists():
            try:
                env_loaded = load_config(str(env_cfg_path_p))
            except Exception:  # noqa: BLE001 — defensive; fall through
                env_loaded = None
            if (
                env_loaded is not None
                and env_loaded.db.dialect.value == dialect_name
            ):
                cfg_path = env_cfg_path_p
    if cfg_path is None:
        cfg_path = _DIALECT_CONFIG_PATHS[dialect_name]
    if not cfg_path.exists():
        pytest.skip(
            f"{cfg_path} not present — {dialect_name} dialect cell "
            f"skipped. The other dialect still runs; CI runs each "
            f"dialect cell with its own `-c run/config.{dialect_name}.yaml`."
        )
    loaded = load_config(str(cfg_path))
    if loaded.db.url is None:
        pytest.skip(
            f"{cfg_path} has no demo_database_url — {dialect_name} "
            f"dialect cell skipped. Agreement tests need a seedable DB."
        )
    # DY.2 — THE key bug was `Dialect.ORACLE if ... else Dialect.POSTGRES`,
    # which silently coerced the `duckdb` param to POSTGRES so every duck
    # cell walked Postgres tables it never seeded. `Dialect(<name>)` maps
    # the param string to its enum member for all three dialects (the enum
    # values ARE "postgres"/"oracle"/"duckdb").
    dialect_enum = Dialect(dialect_name)
    if loaded.db.dialect is not dialect_enum:
        pytest.skip(
            f"{cfg_path} declares dialect={loaded.db.dialect.value} but "
            f"the matrix expects dialect={dialect_name}. Fix the YAML "
            f"or rename the file so the matrix loads it under the "
            f"right cell."
        )
    return (loaded, cfg_path, dialect_enum)


# Anchor on real today so the stuck_* matviews' CURRENT_TIMESTAMP
# filter sees plants in the past. days_ago offsets stay deterministic;
# only the absolute calendar date varies. The audit period [_TODAY - 7,
# _TODAY - 1] then contains the plant effective dates by construction.
def today_anchor() -> date:
    """Real-today anchor for the L1-invariant seed window. The
    stuck_* matviews use CURRENT_TIMESTAMP-based age filters, so
    plants must land in the past relative to NOW (not the
    2030-anchored hash-lock date)."""
    return date.today()  # typing-smell: ignore[test-module-nondeterminism]: stuck_* matviews compute age via CURRENT_TIMESTAMP


def audit_window(today: date) -> DateInterval:
    """BC.4d — typed audit window. `trailing_days_ending_yesterday(today, 7)`
    yields `[today - 7, today - 1]`, threaded into
    `apply_db_seed(plant_window=...)` so the L1-invariant spine
    generators land plants on `plant_window.end` (the most-recently-
    closed auditable day) by construction.
    """
    return DateInterval.trailing_days_ending_yesterday(today, 7)


# All 6 L1 invariants. Producers parametrize over this list to keep
# the "one producer per invariant" file shape clean.
ALL_L1_INVARIANTS: tuple[str, ...] = (
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
)


# Flat-shape invariants — one matview row per (account, day[, transfer_type]),
# and the dashboard + audit PDF show that same flat row set. For these
# the agreement validator tightens to row-identity (natural-key set).
# The rest (stuck_* / supersession) are divergent-shape — the PDF
# aggregates into roll-up tables while the QS + App2 detail tables show
# raw matview rows — so those stay count-level.
FLAT_SHAPE_INVARIANTS: frozenset[str] = frozenset(
    {"drift", "overdraft", "limit_breach"},
)


# Both L2 invariants — Investigation 3-way agreement.
ALL_L2_INVARIANTS: tuple[str, ...] = ("anomaly", "money_trail")
