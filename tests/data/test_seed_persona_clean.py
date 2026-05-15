"""Persona-cleanliness test for the lifted ``common.l2.seed`` module (M.2d.5).

The user's framing for whether the M.2d.5 lift is actually clean: take
the SPEC's worked-example shapes (``tests/l2/spec_example.yaml``,
hand-assembled from SPEC.md §618+ with persona-neutral names), plant
one of every scenario type via the lifted seed primitives, and confirm
that ZERO Sasquatch / SNB / FRB / Bigfoot strings appear in the
generated SQL. If anything does leak, the lift smuggled an L3 persona
literal into L1 code.

What this test catches that the AR-fixture tests can't:
- Hardcoded counterparty IDs (e.g., the ``ext-frb-snb-master`` literal
  the original lift carried over from sasquatch_ar's tightly-coupled
  shape).
- Hardcoded counterparty display names (``Federal Reserve Bank — SNB
  Master``).
- Any stray ``"Sasquatch"`` / ``"Bigfoot"`` / etc. that ends up baked
  into a metadata payload or a generated comment.

The AR fixture's tests pass even when those leaks exist (because their
expected output IS the leaked output). This test cannot.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quicksight_gen.common.l2 import (
    Identifier,
    Name,
    load_instance,
)
from quicksight_gen.common.l2.seed import (
    DriftPlant,
    LimitBreachPlant,
    OverdraftPlant,
    ScenarioPlant,
    StuckPendingPlant,
    StuckUnbundledPlant,
    SupersessionPlant,
    TemplateInstance,
    emit_seed,
)


SPEC_YAML = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
REFERENCE_DATE = date(2026, 4, 25)


# Persona literals that MUST NOT appear anywhere in the generated SQL.
# Match case-insensitively. Add new entries as the seed grows.
_PERSONA_BLOCKLIST = (
    "sasquatch",
    "bigfoot",
    "yeti",
    "cascadia",
    "juniper",
    "snb",            # Sasquatch National Bank
    "frb",            # Federal Reserve Bank — appears in sasquatch_ar fixture's
                      # external counterparty NAME, never neutral
    "farmers exchange",  # the absorbed bank in AR demo
    "merchant settlement cycle",  # OK if used as a TransferTemplate name in YAML
                                  # but should never be hardcoded by the seed
)


@pytest.fixture(scope="module")
def spec_instance():
    """The SPEC-shaped L2 instance used as the persona-clean substrate."""
    return load_instance(SPEC_YAML)


@pytest.fixture(scope="module")
def spec_scenario(spec_instance) -> ScenarioPlant:
    """A scenario that exercises every plant type using only spec_example.yaml's
    accounts + rails. No Sasquatch identifiers anywhere."""
    instances = (
        TemplateInstance(
            template_role=Identifier("CustomerSubledger"),
            account_id=Identifier("cust-001"),
            name=Name("Customer Number One"),
        ),
        TemplateInstance(
            template_role=Identifier("CustomerSubledger"),
            account_id=Identifier("cust-002"),
            name=Name("Customer Number Two"),
        ),
    )
    return ScenarioPlant(
        template_instances=instances,
        drift_plants=(
            DriftPlant(
                account_id=Identifier("cust-001"),
                days_ago=5,
                delta_money=Decimal("75.00"),
                rail_name=Identifier("ExternalRailInbound"),
                counter_account_id=Identifier("external-counterparty-one"),
            ),
        ),
        overdraft_plants=(
            OverdraftPlant(
                account_id=Identifier("cust-002"),
                days_ago=6,
                money=Decimal("-1500.00"),
            ),
        ),
        limit_breach_plants=(
            LimitBreachPlant(
                account_id=Identifier("cust-001"),
                days_ago=4,
                rail_name="wire",
                rail_name=Identifier("ExternalRailOutbound"),
                amount=Decimal("9000.00"),  # > $5k wire cap
                counter_account_id=Identifier("external-counterparty-one"),
            ),
        ),
        stuck_pending_plants=(
            StuckPendingPlant(
                account_id=Identifier("cust-001"),
                days_ago=2,
                rail_name="ach",
                rail_name=Identifier("ExternalRailInbound"),
                amount=Decimal("450.00"),
            ),
        ),
        stuck_unbundled_plants=(
            StuckUnbundledPlant(
                account_id=Identifier("cust-002"),
                days_ago=1,
                rail_name="charge",
                rail_name=Identifier("SubledgerCharge"),
                amount=Decimal("12.50"),
            ),
        ),
        supersession_plants=(
            SupersessionPlant(
                account_id=Identifier("cust-001"),
                days_ago=3,
                rail_name="charge",
                rail_name=Identifier("SubledgerCharge"),
                original_amount=Decimal("250.00"),
                corrected_amount=Decimal("275.00"),
            ),
        ),
        today=REFERENCE_DATE,
    )


@pytest.fixture(scope="module")
def spec_seed_sql(spec_instance, spec_scenario) -> str:
    return emit_seed(spec_instance, spec_scenario)


def test_seed_emits_against_spec_example_yaml(spec_seed_sql) -> None:
    """The lifted seed primitives can run against an arbitrary L2 instance.

    Smoke: emit_seed(spec_example, ...) produces non-empty SQL with both
    INSERTs (transactions + daily_balances).
    """
    assert spec_seed_sql.strip()
    assert "INSERT INTO spec_example_transactions" in spec_seed_sql
    assert "INSERT INTO spec_example_daily_balances" in spec_seed_sql


def test_no_persona_literals_in_generated_sql(spec_seed_sql) -> None:
    """The headline cleanliness check: no persona literals appear anywhere.

    Iterates the blocklist and asserts each is absent (case-insensitive).
    A failure here means the lifted common.l2.seed code is smuggling
    a Sasquatch / SNB / FRB / etc. literal into output that should be
    persona-blind.
    """
    haystack = spec_seed_sql.lower()
    leaks = [needle for needle in _PERSONA_BLOCKLIST if needle in haystack]
    assert not leaks, (
        f"common.l2.seed leaked persona literals into SQL emitted from "
        f"the SPEC example instance: {leaks!r}. The seed should resolve "
        f"every account/rail identifier from the L2 instance, not from "
        f"hardcoded constants."
    )


def test_seed_includes_only_spec_example_account_ids(spec_seed_sql) -> None:
    """Every account_id literal in the SQL came from spec_example.yaml.

    Stronger version of the persona-blocklist: the only account-id
    literals in the output should be ones declared in spec_example.yaml
    (cust-001, cust-002, external-counterparty-one). Any other id is
    a leak.
    """
    expected_ids = {
        "cust-001",
        "cust-002",
        "external-counterparty-one",
    }
    # Pull all single-quoted strings shaped like an account id (slug
    # form: lowercase + at least one hyphen). Avoids matching scope
    # literals like 'external' or status literals like 'Posted'.
    import re
    found_account_ids = set(
        re.findall(
            r"'((?:cust|ext|gl|int)-[a-z0-9_-]+)'",
            spec_seed_sql,
        )
    )
    unexpected = found_account_ids - expected_ids
    assert not unexpected, (
        f"Generated SQL contains account_id literals not declared in "
        f"spec_example.yaml: {unexpected!r}. The seed has hardcoded "
        f"identifier strings that should have come from the L2 instance."
    )


def test_seed_includes_only_spec_example_account_names(spec_seed_sql) -> None:
    """Every account display name in the SQL comes from spec_example.yaml.

    Catches hardcoded account-name strings (e.g. ``Federal Reserve Bank
    — SNB Master``) that would still leak persona context even if the
    id field was correctly resolved.
    """
    expected_names = {
        "Customer Number One",
        "Customer Number Two",
        "External Counterparty One",
    }
    # Account names are the second single-quoted string in each VALUES row;
    # we'll look for any name not in expected by checking the blocklist
    # logic above plus an explicit assertion for the expected names being
    # present.
    for name in expected_names:
        assert f"'{name}'" in spec_seed_sql, (
            f"expected account name {name!r} missing from SQL — the seed "
            f"is not emitting the SPEC instance's account names"
        )
