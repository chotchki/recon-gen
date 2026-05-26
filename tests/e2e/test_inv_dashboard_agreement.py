"""AT.5.b + AT.5.c + AT.5.e — Investigation 3-way agreement gate.

The L2 sibling of ``test_audit_dashboard_agreement.py``'s 4-way gate.
For each L2 invariant the chain:

    spine.detect()  ==  direct_matview_SELECT(filtered)  ==  App2  ==  QS

every renderer agrees with the matview under the same parameter
pushdown state (σ slider for anomaly, chain-root dropdown for
money_trail; see ``apps/investigation/datasets.py``).

AT.5.d decision (2026-05-23): no PDF leg for L2. The audit PDF stops
at L1 invariants by design (regulator-facing accounting-trail); the
Investigation surface is analyst-facing fraud/AML pattern detection
with a different audience and reporting cadence. So AT.5's L2 gate is
3-way (spine + App2 + QS), not the 4-way L1 contract that
``test_audit_dashboard_agreement.py`` holds.

Per-leg degradation (mirrors L1's pattern): a missing prereq disables
ONE leg, not the whole test. ``RECON_GEN_E2E=1`` gates the module;
absent QS deploy / ``RECON_E2E_USER_ARN`` unset → the QS leg yields
``None`` and the test runs as a 2-way spine + App2 + direct check.

L2 plants land via the spine generators (``AnomalyGenerator`` /
``MoneyTrailGenerator``) on top of the ``l1_plus_broad`` seed
``apply_db_seed`` runs first — matches what ``recon-gen data apply
--execute`` produces in a real deploy. Matviews refresh after the L2
plants land so the dashboard's SQL sees them.

Read pattern (also mirrors L1's 4-way test):

  - **App2** results read module-scoped via ``App2Driver.serving``,
    torn down before the function-scoped QS driver runs. Sync
    Playwright is one-context-per-thread; two open at once → exception.
    App2's dict carries each invariant's ``(count, seen, keys)`` so
    the per-test asserts are pure dict lookups.
  - **QS** runs in the test body via a function-scoped driver (embed
    URLs are single-use). Yields ``None`` on unavailable, no skip.
  - **Spine** ``detect()`` runs in the test body via a fresh per-test
    DB connection — same connection the direct-matview SELECT uses.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping

import pytest

from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import RECON_GEN_E2E

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.spine.anomaly import AnomalyGenerator
    from recon_gen.common.spine.money_trail import MoneyTrailGenerator
    from recon_gen.common.tree import App
    from tests.e2e._drivers import QsEmbedDriver


# Module-level cfg load can't fire under the unit-only CI job — match
# the rest of the e2e suite's RECON_GEN_E2E gate at import time so the
# unit job doesn't crash on collection.
if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "Investigation dashboard agreement test requires RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports keep collection cheap on the unit job
from recon_gen.common.l2 import (  # noqa: E402
    L2Instance,
    load_instance,
    refresh_matviews_sql,
)
from recon_gen.common.spine import (  # noqa: E402
    AnomalyInvariant,
    MoneyTrailInvariant,
    Violation,
)
from tests.audit._inv_dashboard_extract import (  # noqa: E402
    anomaly_row_keys,
    count_anomaly_rows,
    count_money_trail_rows,
    key_columns_for,
    money_trail_row_keys,
    rows_seen_anomaly,
    rows_seen_money_trail,
)
from tests.audit._matview_extract import (  # noqa: E402
    anomaly_matview_row_keys,
    count_anomaly_matview_rows,
    count_money_trail_matview_rows,
    distinct_money_trail_roots,
    money_trail_matview_row_keys,
)
from tests.audit._scenario_expectations import (  # noqa: E402
    ExpectedL2AuditCounts,
    expected_l2_audit_counts,
)
from tests.e2e._drivers import App2Driver  # noqa: E402


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


# Bundled persona-neutral L2 — the same yaml every other e2e test
# defaults to when no per-cell override is set.
_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE_BUNDLED = _FIXTURES / "spec_example.yaml"

_INSTANCE: L2Instance = load_instance(_SPEC_EXAMPLE_BUNDLED)


# Anchor on real today so the broad seed's stuck_* plants land in the
# matview's past-relative-to-NOW window (matches
# test_audit_dashboard_agreement.py's rationale). The σ-threshold +
# chain-root agreement asserts here don't depend on absolute calendar
# date — only on the relative day shape the spine generator + matview
# share — so this stays deterministic across runs.
_TODAY = date.today()  # typing-smell: ignore[test-module-nondeterminism]: stuck_* matviews compute age via CURRENT_TIMESTAMP — anchor must be relative to NOW


# The σ slider's analysis-level default + dataset-parameter default. If
# the dashboard's default drifts (``apps/investigation/app.py`` +
# ``apps/investigation/datasets.py``) and this constant doesn't follow,
# the agreement test catches it loudly: drivers read at this σ and the
# direct SELECT filter is at the same σ but the dashboard would have
# rendered a different shape.
_DEFAULT_SIGMA = 2.0


# Per-test isolation suffix appended to isolated_inv_cfg.db_table_prefix +
# cfg.deployment_name. The agreement test's seeded_l2_db drops the
# schema CASCADE and re-seeds with plants-only — without isolation,
# every other browser test reading the runner's broad seed sees empty
# tables when their xdist worker happens to run after this fixture.
# The suffix carves out a dedicated table namespace + deployment ID
# so the destructive seed can't touch the runner's prefix.
_ISOLATION_SUFFIX = "iagree"


# A high-magnitude anomaly plant — 1000× the baseline amount with 100
# background pairs feeding the population stddev. AT.0's finding: a
# spike against a too-thin population shifts the mean toward itself
# enough that even huge multipliers don't clear 3σ. 100 baseline pairs
# at $100 each + a $100,000 spike gives a clear >10σ separation.
_ANOMALY_BASELINE_PAIRS = 100
_ANOMALY_BASELINE_AMOUNT = 100.0
_ANOMALY_SPIKE_MAGNITUDE = 100_000.0

# A 3-deep money-trail chain — enough to exercise the recursive walk
# (depths 0/1/2) without growing the test's data footprint.
_MONEY_TRAIL_CHAIN_LENGTH = 3
_MONEY_TRAIL_AMOUNT = 100.0

# Deterministic root of the planted chain — the MoneyTrailGenerator's
# transfer-id scheme uses ``xfer-money-trail-{index}`` (see
# ``common/spine/money_trail.py::MoneyTrailGenerator._transfer_id``).
_PLANTED_CHAIN_ROOT = "xfer-money-trail-0"


# Both L2 invariants. The 3-way agreement test parametrizes over
# these; per-invariant projections live in ``_inv_dashboard_extract`` +
# ``_matview_extract``.
_ALL_L2_INVARIANTS: tuple[str, ...] = ("anomaly", "money_trail")


def _build_anomaly_generator(
    cfg: "Config", anchor_day: date,
) -> "AnomalyGenerator":
    """Construct the AnomalyGenerator the seed + agreement test share.

    Hoisted so both ``seeded_l2_db`` (which emits) and
    ``planted_l2_bounds`` (which derives expected key tuples without
    touching the DB) can build identical generators — the lower-bound
    key tuples otherwise depend on a planter rebuild that drifts from
    what was emitted.
    """
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=_ANOMALY_BASELINE_PAIRS,
        baseline_amount=_ANOMALY_BASELINE_AMOUNT,
        spike_magnitude=_ANOMALY_SPIKE_MAGNITUDE,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db_table_prefix
    return gen


def _build_money_trail_generator(
    cfg: "Config", anchor_day: date,
) -> "MoneyTrailGenerator":
    """Construct the MoneyTrailGenerator the seed + agreement test share.
    See ``_build_anomaly_generator`` for the rationale."""
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger",
        chain_length=_MONEY_TRAIL_CHAIN_LENGTH,
        amount=_MONEY_TRAIL_AMOUNT,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db_table_prefix
    return gen


def _plant_anchor_day() -> date:
    """Anchor day for the L2 plants — two days before ``today``. Far
    enough back to land in the past matview window, close enough to
    stay deterministic relative to ``_TODAY``."""
    return _TODAY - timedelta(days=2)


@pytest.fixture(scope="module")
def isolated_inv_cfg(cfg) -> "Iterator[Config]":  # cfg is conftest's Config — cross-slice rule: don't annotate it (slice 1 owns conftest)
    """Per-test cfg with an isolated table prefix + deployment name.

    The Investigation agreement test's seeded_l2_db is destructive —
    it DROPs the schema CASCADE then re-seeds plants-only. Sharing
    ``cfg`` with the rest of the browser tier meant every other test
    reading the runner's broad seed saw empty tables when their xdist
    worker fired after this fixture. The isolated cfg carves out
    ``<prefix>_iagree`` table names + ``<deployment_name>-iagree``
    QS resource IDs so the destructive seed lands in its own
    namespace and can't touch the shared deploy.

    Module-scoped so the per-test deploy + seed cost amortizes
    across both the anomaly + money_trail parametrizations.

    Same demo_database_url + dialect + auth as the parent cfg —
    only the namespace bits change. The fixture is responsible for
    cleaning up the isolated DB tables on teardown via DROP CASCADE
    (QS-side resources stay until the next clean — they're cheap to
    leave around and the runner's `sweep` verb covers them).
    """
    from dataclasses import replace

    iso_cfg = replace(
        cfg,
        db_table_prefix=f"{cfg.db_table_prefix}_{_ISOLATION_SUFFIX}",
        deployment_name=f"{cfg.deployment_name}-{_ISOLATION_SUFFIX}",
    )
    yield iso_cfg

    # Teardown: DROP every isolated table so the next run starts
    # fresh. Best-effort — if the connect fails (RDS stopped between
    # test + teardown), log + continue rather than fail the fixture.
    try:
        teardown_conn = connect_demo_db(iso_cfg)
    except Exception as exc:  # noqa: BLE001 — never break the chain
        print(
            f"isolated_inv_cfg teardown: connect failed: {exc!r} — "
            f"isolated tables {iso_cfg.db_table_prefix}_* may persist; "
            f"run `recon-gen schema clean -c <iso_cfg>` to drop them."
        )
        return
    try:
        from recon_gen.common.l2.schema import emit_schema_drop_sql
        clean_sql = emit_schema_drop_sql(
            _INSTANCE,
            prefix=iso_cfg.db_table_prefix,
            dialect=iso_cfg.dialect,
        )
        with teardown_conn.cursor() as cur:
            execute_script(cur, clean_sql, dialect=iso_cfg.dialect)
        teardown_conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(
            f"isolated_inv_cfg teardown: schema clean failed: {exc!r}"
        )
    finally:
        teardown_conn.close()


@pytest.fixture(scope="module")
def isolated_inv_app(isolated_inv_cfg: "Config") -> "App":
    """Investigation App tree built against the ISOLATED cfg.

    The session-scoped ``inv_app`` (conftest.py) is built off the
    SHARED cfg — its dataset SQL has the shared ``<prefix>_*`` table
    names baked in. App2Driver pointed at the isolated DB would read
    via this tree, hit the shared-prefix tables, and report zero
    rows (the isolated tables hold the seed, the shared tables don't
    have the agreement test's plants).

    Module-scoped to amortize the build cost across the two
    parametrizations (anomaly + money_trail).
    """
    from recon_gen.apps.investigation.app import build_investigation_app

    app = build_investigation_app(
        isolated_inv_cfg, l2_instance=_INSTANCE,
    )
    app.emit_analysis()
    return app


@pytest.fixture(scope="module")
def inv_dashboard_id(isolated_inv_cfg: "Config") -> str:
    """Override the conftest's ``inv_dashboard_id`` so ``qs_inv_driver``
    looks for the isolated deployment's dashboard. When the isolated
    dashboard isn't deployed (default — the runner deploys against
    the shared cfg only), ``qs_inv_driver`` catches the
    ``ResourceNotFoundException`` and yields None, and the test runs
    as a 2-way (App2 + direct) agreement.

    Re-enabling the QS leg requires the fixture (or a future runner
    step) to also `recon-gen json apply` against ``isolated_inv_cfg``
    before the test runs. Tracked separately as a follow-on; the
    isolation work landed first because that's what unblocked the
    rest of the browser tier.
    """
    return f"{isolated_inv_cfg.deployment_name}-investigation-dashboard"


@pytest.fixture(scope="module")
def seeded_l2_db(isolated_inv_cfg: "Config") -> None:
    """Apply the schema + broad seed + L2 spine plants + matview refresh
    against the ISOLATED cfg's table prefix.

    Two-phase seed: ``apply_db_seed`` lays the schema + L1 plants +
    initial refresh (the shape ``recon-gen data apply --execute``
    produces); the spine generators then plant L2 violations on top,
    and a second matview refresh picks them up.

    Module-scoped — seeding is the expensive setup and the anomaly +
    money_trail asserts both read the same matview state.

    Isolation contract: the destructive DROP CASCADE in apply_db_seed
    only touches ``<iso_prefix>_*`` tables, not the runner's
    ``<isolated_inv_cfg.db_table_prefix>_*`` broad-seed tables that the rest of
    the browser tier reads. Per-prefix table namespaces on the shared
    AWS RDS cluster.
    """
    from tests.e2e._seed_helpers import apply_db_seed

    conn = connect_demo_db(isolated_inv_cfg)
    try:
        apply_db_seed(
            conn, _INSTANCE,
            prefix=isolated_inv_cfg.db_table_prefix,
            mode="l1_plus_broad",
            today=_TODAY,
            dialect=isolated_inv_cfg.dialect,
            include_baseline=False,
        )

        # AT.5.b — explicit L2 plants via the spine generators. Same
        # shape AT.4's semantic-lock tests use; driven at a live
        # deployed DB rather than an in-memory SQLite so the renderer
        # layer has something to show. ``prefix`` swap lets the
        # generator write to whatever the cfg declares (matches AS.4's
        # LedgerSimulation prefix wiring); the dialect-aware insert
        # helpers (AT.5.b refactor of ``_emit_helpers.insert_tx``)
        # make this work against PG / Oracle as well as SQLite.
        anchor = _plant_anchor_day()
        anomaly_gen = _build_anomaly_generator(isolated_inv_cfg, anchor)
        money_trail_gen = _build_money_trail_generator(
            isolated_inv_cfg, anchor,
        )
        anomaly_gen.emit(conn)
        money_trail_gen.emit(conn)
        conn.commit()

        # Refresh again so the matviews see the L2 plants (apply_db_seed
        # ran a refresh before the spine plants landed).
        refresh_sql = refresh_matviews_sql(
            _INSTANCE,
            prefix=isolated_inv_cfg.db_table_prefix,
            dialect=isolated_inv_cfg.dialect,
        )
        with conn.cursor() as cur:
            execute_script(cur, refresh_sql, dialect=isolated_inv_cfg.dialect)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def planted_l2_bounds(isolated_inv_cfg: "Config") -> ExpectedL2AuditCounts:
    """AT.5.f — the lower-bound counts + key projections the spine
    generators planted. The 3-way test asserts every renderer's count
    is ``>=`` ``X_count`` and every renderer's key set is ``>=``
    ``X_keys`` (subset/equality depends on the invariant).

    Uses ``isolated_inv_cfg`` so the generator's table-prefix matches
    what the seeded_l2_db fixture wrote against.
    """
    anchor = _plant_anchor_day()
    return expected_l2_audit_counts(
        anomaly_gen=_build_anomaly_generator(isolated_inv_cfg, anchor),
        money_trail_gen=_build_money_trail_generator(
            isolated_inv_cfg, anchor,
        ),
    )


@pytest.fixture(scope="module")
def per_l2_app2_results(
    isolated_inv_cfg: "Config",
    isolated_inv_app: "App",
    seeded_l2_db: None,
) -> "Mapping[str, Mapping[str, object]]":
    """The App2 leg's data, read once up-front (mirrors L1's
    ``per_dialect_app2_results``).

    Spins the Investigation app tree (built off ``isolated_inv_cfg``,
    so its dataset SQL references the isolated table prefix) via
    ``App2Driver.serving`` against the ISOLATED seeded DB. Walks both
    L2 invariant sheets to collect each one's ``{"count": int,
    "seen": int, "keys": set}``, then **tears the App2 server +
    browser down before returning** so the per-test ``qs_inv_driver``
    (a second Playwright sync context) doesn't collide with App2's.
    Sync Playwright is one-context-per-thread: two open at once →
    "Playwright Sync API inside the asyncio loop".

    Depends on ``seeded_l2_db`` so the seed + plants land before the
    reads. Module-scoped — the App2 walk is the expensive setup.
    """
    _ = seeded_l2_db  # ordering dep — see docstring
    from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

    assert isolated_inv_app.analysis is not None
    visual_fetcher, options_fetcher = make_live_db_fetchers_for_app(
        tree_app=isolated_inv_app, cfg=isolated_inv_cfg,
    )
    results: dict[str, dict[str, object]] = {}
    with App2Driver.serving(
        cfg=isolated_inv_cfg,
        tree_app=isolated_inv_app,
        sheet=isolated_inv_app.analysis.sheets[0],
        data_fetcher=visual_fetcher, options_fetcher=options_fetcher,
        dashboard_id="inv", dashboard_title="Investigation (live)",
    ) as driver:
        driver.open("inv")
        # Anomaly — set σ slider, read count + DOM-window + key set.
        results["anomaly"] = {
            "count": count_anomaly_rows(driver, _DEFAULT_SIGMA),
            "seen": rows_seen_anomaly(driver, _DEFAULT_SIGMA),
            "keys": anomaly_row_keys(driver, _DEFAULT_SIGMA),
        }
        # Money trail — pick planted root, read count + window + keys.
        results["money_trail"] = {
            "count": count_money_trail_rows(driver, _PLANTED_CHAIN_ROOT),
            "seen": rows_seen_money_trail(driver, _PLANTED_CHAIN_ROOT),
            "keys": money_trail_row_keys(driver, _PLANTED_CHAIN_ROOT),
        }
    # App2 server + browser torn down by the ``with`` exit — the
    # returned dict holds everything the per-test asserts need; no
    # live driver lingers to collide with ``qs_inv_driver``.
    return results


@pytest.fixture
def qs_inv_driver(
    request: pytest.FixtureRequest,
    cfg,  # conftest fixture (Config); cross-slice rule — slice 1 annotates
    region,  # conftest fixture (str); cross-slice rule
    account_id,  # conftest fixture (str); cross-slice rule
    inv_dashboard_id: str,
    inv_app,  # conftest fixture (App); cross-slice rule
) -> "Iterator[QsEmbedDriver | None]":
    """Function-scoped ``QsEmbedDriver`` aimed at the deployed
    Investigation dashboard (mirrors L1's ``per_dialect_qs_driver``).

    Yields ``None`` (does NOT skip the test) when the QS leg can't run
    — ``RECON_E2E_USER_ARN`` unset, dashboard not deployed, etc. The
    3-way test then runs the other legs (direct + App2 + spine) as a
    clean 2-way + spine.

    Pre-flight: confirm the dashboard exists before opening the embed
    URL — otherwise QS produces a URL pointing at nothing, which loads
    as an empty error page that times out the "wait for sheet tabs"
    gate with a confusing 30s timeout.

    Tall viewport (1600×3000) so the Money Trail Sankey + stacked
    detail table fit inside the initial render area; QS lazy-renders
    below the fold and ``table_row_count``'s page-size-bump path
    needs ``.grid-container`` close enough to scroll into.
    """
    del inv_app  # only ensure ordering / tree-load — driver doesn't use it
    import boto3

    qs = boto3.client("quicksight", region_name=region)
    try:
        qs.describe_dashboard(
            AwsAccountId=account_id, DashboardId=inv_dashboard_id,
        )
    except qs.exceptions.ResourceNotFoundException:
        # Not deployed for this cell. The other legs still run.
        yield None
        return
    from tests.e2e._drivers._lifecycle import qs_driver_or_none

    with qs_driver_or_none(
        request, cfg=cfg, account_id=account_id, region=region,
        viewport=(1600, 3000),
    ) as driver:
        yield driver  # may be None if get_user_arn failed


@pytest.fixture
def db_conn(isolated_inv_cfg: "Config") -> "Iterator[Any]":
    # WHY Iterator[Any]: live PG/Oracle/SQLite connection — concrete type
    # varies per dialect, no shared Protocol across the three drivers.
    """Function-scoped raw DB connection for the direct-SELECT anchor
    + the spine ``detect()`` call. Points at the ISOLATED cfg so the
    direct SELECTs read the same per-test prefix the App2 leg reads.
    """
    conn = connect_demo_db(isolated_inv_cfg)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Per-invariant projection helpers — convert spine Violation sets to the
# matview's natural-key tuple shape so the spine leg compares directly
# against the matview SELECT projection.
# ---------------------------------------------------------------------------


def _anomaly_spine_keys(
    violations: set[Violation],
) -> set[tuple[str, str, date]]:
    """Project anomaly Violations to ``(sender, recipient, window_end)``
    — matches the matview key + the dashboard's group_by + the σ View
    threshold the dashboard applies. NB: the spine's detector returns
    EVERY bucket (AT.2's contract), so we slice here by σ_threshold via
    ``AnomalyView`` semantics to mirror the dashboard's pushdown."""
    from datetime import datetime
    out: set[tuple[str, str, date]] = set()
    for v in violations:
        items = dict(v.identity)
        sender = items.get("sender_account_id")
        recipient = items.get("recipient_account_id")
        we = items.get("window_end")
        if sender is None or recipient is None or we is None:
            continue
        # window_end may be a date or a ISO-prefixed str depending on
        # how the detector pulled the cursor cell.
        if isinstance(we, date):
            we_date = we
        elif isinstance(we, datetime):
            we_date = we.date()
        else:
            we_date = date.fromisoformat(str(we)[:10])
        out.add((str(sender), str(recipient), we_date))
    return out


def _money_trail_spine_keys(
    violations: set[Violation],
) -> set[tuple[str, int]]:
    """Project money_trail Violations to ``(transfer_id, depth)`` —
    matches the matview key + the dashboard's group_by."""
    out: set[tuple[str, int]] = set()
    for v in violations:
        items = dict(v.identity)
        tid = items.get("transfer_id")
        depth = items.get("depth")
        if tid is None or depth is None:
            continue
        out.add((str(tid), int(depth)))  # type: ignore[arg-type]: depth narrowed by the early-continue above; pyright doesn't follow
    return out


def _spine_keys_for(
    invariant: str, conn: object, prefix: str,
) -> set[object]:
    """Run the spine ``Invariant.detect(conn)`` for the named
    invariant and project to the matview's natural-key tuple shape.

    For anomaly, slices by ``z_score >= _DEFAULT_SIGMA`` — the
    detector returns every bucket (AT.2 contract) but the dashboard's
    detail table is σ-filtered; the spine leg must match the same
    threshold the direct-SELECT anchor uses, otherwise spine == direct
    holds trivially false on every dashboard-shown row count.
    For money_trail, slices by ``root_transfer_id == _PLANTED_CHAIN_ROOT``
    — same rationale (chain-root dropdown pushdown).
    """
    if invariant == "anomaly":
        inv = AnomalyInvariant(prefix=prefix)
        violations = inv.detect(conn)  # type: ignore[arg-type]: live dbapi conn — Invariant.detect annotated as sqlite3 but accepts any 2.0 connection
        keys = _anomaly_spine_keys(violations)
        # Slice to mirror the dataset pushdown — the detector returns
        # every bucket; the dashboard applies the σ threshold via the
        # dataset's WHERE clause. So we filter the spine projection to
        # the same set the σ-thresholded matview SELECT returns.
        # The detector's Violation identity carries z_bucket, not
        # z_score, so we re-query the matview directly with the same
        # filter rather than approximating from buckets. (The same
        # connection is used; this stays atomic.)
        return _filter_anomaly_keys_by_sigma(
            conn, prefix, keys, sigma_threshold=_DEFAULT_SIGMA,
        )
    if invariant == "money_trail":
        inv_mt = MoneyTrailInvariant(prefix=prefix)
        mt_violations = inv_mt.detect(conn)  # type: ignore[arg-type]: live dbapi conn — see AnomalyInvariant.detect above
        mt_keys = _money_trail_spine_keys(mt_violations)
        # Filter to the planted root the dashboard's dropdown pegs.
        # The detector returns every edge across every chain; the
        # dashboard shows one chain at a time per the analyst-driven
        # root pick.
        root_edge_keys = _money_trail_root_edge_keys(
            conn, prefix, root_transfer_id=_PLANTED_CHAIN_ROOT,
        )
        return mt_keys & root_edge_keys  # type: ignore[arg-type]: tuple[str,int] & tuple[str,int]; pyright loses through the dict-of-set
    raise ValueError(f"Unknown L2 invariant: {invariant!r}")


def _filter_anomaly_keys_by_sigma(
    conn: object, prefix: str,
    spine_keys: set[tuple[str, str, date]],
    *,
    sigma_threshold: float,
) -> set[tuple[str, str, date]]:
    """Restrict ``spine_keys`` to rows the σ-filtered matview also
    holds. The matview's ``z_bucket`` is a coarse 0/1/2/3/4+ bucket;
    the dashboard's σ slider filters on the precise ``z_score``. So
    "spine keys" alone (bucketed) can't compare row-identity with
    "direct keys" (precise threshold) unless we re-anchor on the same
    SELECT. We do that by reading the σ-filtered matview keys directly
    and intersecting with the spine projection."""
    matview_keys = anomaly_matview_row_keys(
        conn, prefix, sigma_threshold=sigma_threshold,
    )
    return spine_keys & matview_keys  # type: ignore[operator]: matview_keys items are tuple[str|date,...] union; subset of spine_keys' tuple[str,str,date]


def _money_trail_root_edge_keys(
    conn: object, prefix: str, *, root_transfer_id: str,
) -> set[tuple[str, int]]:
    """Edge keys in the matview rooted at the given transfer — the
    counter-projection for intersection with the spine's full-walk."""
    matview_keys = money_trail_matview_row_keys(
        conn, prefix, root_transfer_id=root_transfer_id,
    )
    return matview_keys  # type: ignore[return-value]: matview_keys items are tuple[str|int,...]; effectively tuple[str,int] for money_trail


# ---------------------------------------------------------------------------
# The 3-way agreement test — one body per L2 invariant.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invariant", _ALL_L2_INVARIANTS)
def test_invariant_three_way_agreement(
    seeded_l2_db: None,
    per_l2_app2_results: "Mapping[str, Mapping[str, object]]",
    qs_inv_driver: "QsEmbedDriver | None",
    inv_dashboard_id: str,
    db_conn: Any,
    isolated_inv_cfg: "Config",
    planted_l2_bounds: ExpectedL2AuditCounts,
    invariant: str,
) -> None:
    """Per-invariant 3-renderer agreement (AT.5.e) — the chain

        spine.detect()  ==  direct_matview_SELECT(filtered)
                       ==  App2 dashboard  ==  QS dashboard

    Anchors (the 4 sources of truth + the producer-side lower bound):

    - **planted scenario** (the spine generator's intended shape) — a
      *lower bound*. The 2-day rolling SUM matview may classify
      neighboring days into the spike's z-population, so direct count
      can exceed plant count.
    - **direct matview SELECT** (`_matview_extract`) — the ground
      truth. Every renderer + the spine reads through this matview;
      mismatches surface here.
    - **spine ``Invariant.detect()``** (`_spine_keys_for`) — the
      in-process semantic-correctness anchor. Sliced to the same
      pushdown state the dashboard applies (σ for anomaly, root for
      money_trail) so the comparison shape matches.
    - **App2 dashboard** (`_inv_dashboard_extract` via `App2Driver`) —
      count via ``table_row_count``, row-keys via ``table_rows``.
      Read up-front by ``per_l2_app2_results``.
    - **QS dashboard** (`_inv_dashboard_extract` via `QsEmbedDriver`)
      — same verbs (both speak `DashboardDriver`). The QS leg is
      *optional* — fixture yields ``None`` on unavailable.

    AT.5.d decision: no PDF leg. The audit PDF is L1-only by design.

    Per-leg degradation: a missing QS deploy skips the QS *driver*
    fixture only — the spine + direct + App2 legs still run.
    """
    _ = seeded_l2_db
    app2 = per_l2_app2_results[invariant]
    app2_count = int(app2["count"])  # type: ignore[call-overload]: dict value is `object`; it's an int by construction
    app2_seen = int(app2["seen"])  # type: ignore[call-overload]: same as above
    app2_keys = app2["keys"]  # set by construction

    # --- direct matview SELECT — the ground truth ---
    if invariant == "anomaly":
        direct_count = count_anomaly_matview_rows(
            db_conn, isolated_inv_cfg.db_table_prefix, sigma_threshold=_DEFAULT_SIGMA,
        )
        direct_keys = anomaly_matview_row_keys(
            db_conn, isolated_inv_cfg.db_table_prefix, sigma_threshold=_DEFAULT_SIGMA,
        )
    elif invariant == "money_trail":
        roots = distinct_money_trail_roots(db_conn, isolated_inv_cfg.db_table_prefix)
        assert _PLANTED_CHAIN_ROOT in roots, (
            f"Producer-side regression (money_trail): the planted "
            f"chain's root ({_PLANTED_CHAIN_ROOT!r}) is missing from "
            f"{isolated_inv_cfg.db_table_prefix}_inv_money_trail_edges. Found roots: "
            f"{sorted(roots)[:10]} (+ {max(len(roots) - 10, 0)} more). "
            f"Plant→matview path broken, matview not refreshed after "
            f"the L2 plants, or the generator's transfer-id naming "
            f"changed."
        )
        direct_count = count_money_trail_matview_rows(
            db_conn, isolated_inv_cfg.db_table_prefix,
            root_transfer_id=_PLANTED_CHAIN_ROOT,
        )
        direct_keys = money_trail_matview_row_keys(
            db_conn, isolated_inv_cfg.db_table_prefix,
            root_transfer_id=_PLANTED_CHAIN_ROOT,
        )
    else:
        raise AssertionError(f"unknown L2 invariant: {invariant!r}")

    # --- producer-side lower bounds (AT.5.f) — derived from the
    # spine generators rather than hardcoded constants, so a plant-
    # shape change can't drift the lower bound silently.
    expected_lower_bound = (
        planted_l2_bounds.anomaly_count
        if invariant == "anomaly"
        else planted_l2_bounds.money_trail_count
    )
    expected_planted_keys = (
        set(planted_l2_bounds.anomaly_keys)
        if invariant == "anomaly"
        else set(planted_l2_bounds.money_trail_keys)
    )
    assert direct_count >= expected_lower_bound, (
        f"Producer-side regression ({invariant}): scenario planted "
        f"at least {expected_lower_bound} rows but the matview holds "
        f"{direct_count}. Plant→matview gap."
    )
    assert app2_count >= expected_lower_bound, (
        f"Producer-side regression ({invariant}): App2 shows "
        f"{app2_count} rows but the scenario plant intended at least "
        f"{expected_lower_bound}."
    )
    # The planted keys MUST appear in the matview — the spine plant
    # is the source of truth. (Extra rows are OK: rolling-window
    # neighbors for anomaly, organic chains for money_trail.)
    assert expected_planted_keys <= direct_keys, (  # type: ignore[operator]: expected_planted_keys items match direct_keys' element type by construction (cf. _scenario_expectations key projections)
        f"Planted {invariant} keys missing from the matview:\n"
        f"  planted but absent: "
        f"{sorted(expected_planted_keys - direct_keys)[:5]}\n"  # type: ignore[operator,type-var]: set difference; sorted over union tuple
        f"  planted: {sorted(expected_planted_keys)}\n"  # type: ignore[type-var]: sortable tuples by construction
        f"  direct count: {len(direct_keys)}"
    )

    # --- spine ⋈ direct matview (the AT.5.a contract) ---
    spine_keys = _spine_keys_for(invariant, db_conn, isolated_inv_cfg.db_table_prefix)
    assert spine_keys == direct_keys, (
        f"Spine.detect disagrees with the matview ({invariant}):\n"
        f"  spine-only: {sorted(spine_keys - direct_keys)[:5]}\n"  # type: ignore[type-var]: set difference produces sortable tuples
        f"  direct-only: {sorted(direct_keys - spine_keys)[:5]}\n"  # type: ignore[type-var]: same as above
        f"  spine count: {len(spine_keys)}, direct count: "
        f"{len(direct_keys)}"
    )

    # --- App2 ⋈ direct matview (the AT.5.b contract) ---
    assert app2_count == direct_count, (
        f"App2 disagrees with the matview on count ({invariant}): "
        f"app2={app2_count}, direct={direct_count}."
    )
    assert app2_seen == direct_count, (
        f"App2 table window truncated ({invariant}): {app2_seen} of "
        f"{direct_count} rows visible — the row-identity comparison "
        f"would be partial. (A denser seed needs a read-all path.)"
    )
    assert direct_keys == app2_keys, (
        f"App2 disagrees with the matview on which {invariant} rows "
        f"({key_columns_for(invariant)}):\n"  # type: ignore[arg-type]: invariant is one of the L2 literals by parametrize
        f"  matview-only: {sorted(direct_keys - app2_keys)[:5]}\n"  # type: ignore[type-var,operator]: set difference / sort over union tuple
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}\n"  # type: ignore[type-var,operator]: same as above
        f"  matview count: {len(direct_keys)}, app2 count: "
        f"{len(app2_keys)}"  # type: ignore[arg-type]: app2_keys is a set by construction
    )

    # --- QS ⋈ direct matview (the AT.5.c contract) — optional leg ---
    if qs_inv_driver is None:
        return
    qs_inv_driver.open(inv_dashboard_id)
    if invariant == "anomaly":
        qs_count = count_anomaly_rows(qs_inv_driver, _DEFAULT_SIGMA)
        qs_seen = rows_seen_anomaly(qs_inv_driver, _DEFAULT_SIGMA)
        qs_keys = anomaly_row_keys(qs_inv_driver, _DEFAULT_SIGMA)
    else:
        qs_count = count_money_trail_rows(
            qs_inv_driver, _PLANTED_CHAIN_ROOT,
        )
        qs_seen = rows_seen_money_trail(
            qs_inv_driver, _PLANTED_CHAIN_ROOT,
        )
        qs_keys = money_trail_row_keys(
            qs_inv_driver, _PLANTED_CHAIN_ROOT,
        )
    assert qs_count == direct_count, (
        f"QS disagrees with the matview on count ({invariant}): "
        f"qs={qs_count}, direct={direct_count}."
    )
    assert qs_seen == direct_count, (
        f"QS table window truncated ({invariant}): {qs_seen} of "
        f"{direct_count} rows visible."
    )
    assert direct_keys == qs_keys, (
        f"QS disagrees with the matview on which {invariant} rows "
        f"({key_columns_for(invariant)}):\n"  # type: ignore[arg-type]: invariant is one of the L2 literals by parametrize
        f"  matview-only: {sorted(direct_keys - qs_keys)[:5]}\n"  # type: ignore[type-var,operator]: set difference over union tuples
        f"  qs-only: {sorted(qs_keys - direct_keys)[:5]}\n"  # type: ignore[type-var,operator]: same as above
        f"  matview count: {len(direct_keys)}, qs count: {len(qs_keys)}"
    )
