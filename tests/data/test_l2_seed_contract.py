"""L2 seed contract — parameterized over multiple L2 instances (M.2d.8).

The headline shape: every assertion in this file runs against EVERY L2
instance in ``L2_INSTANCES``. Today that's ``spec_example.yaml`` (the
SPEC's worked-example shapes assembled into a complete generic
fixture) and ``sasquatch_ar.yaml`` (the curated AR fixture). Adding
``sasquatch_pr.yaml`` in M.3.1 is a one-line addition to the list —
every contract assertion below immediately covers PR with no test
edits.

What this file replaces (per M.2d.7): the v3-era schema-contract
tests in ``test_etl_examples.py`` + ``test_demo_etl_examples.py`` that
parsed Schema_v3 markdown structures the v6 rewrite removed. The
intent there was "the seed's emitted values trace back to a declared
spec"; the spec is now the L2 YAML + Schema_v6's column tables, not a
single hand-curated registry.

Contracts asserted here:
- ``account_role`` literals in seed SQL all resolve to a declared L2
  ``Account.role`` or ``AccountTemplate.role`` (or to a synthetic
  template-instance role the auto-scenario materializes).
- ``account_id`` literals all resolve to a declared singleton OR a
  synthetic ``cust-NNN`` from the auto-materialized template
  instances.
- ``rail_name`` literals all resolve to a declared ``Rail.name``.
- ``transfer_type`` literals all resolve to a declared
  ``Rail.transfer_type``.
- Metadata JSON keys all appear in some ``Rail.metadata_keys`` OR in
  a small set of seed-infra keys (``customer_id``,
  ``external_reference``).
- Every column in ``INSERT INTO <prefix>_transactions (...)`` and
  ``INSERT INTO <prefix>_daily_balances (...)`` matches Schema_v6's
  documented column lists for those tables.
- Persona literals (Sasquatch / SNB / FRB / Bigfoot / etc.) NEVER
  appear in the seed when the instance is the persona-neutral
  ``spec_example`` — and DO appear (because that's the persona's name)
  in the sasquatch instance, but only as values from the YAML's own
  declarations.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import date
from pathlib import Path

import pytest

from quicksight_gen.common.env_keys import QS_GEN_FUZZ_SEED

from quicksight_gen.common.l2 import L2Instance, load_instance
from quicksight_gen.common.l2.auto_scenario import default_scenario_for
from quicksight_gen.common.l2.seed import emit_seed

from tests.l2.fuzz import random_l2_yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
L2_DIR = REPO_ROOT / "tests" / "l2"
SCHEMA_DOC = REPO_ROOT / "src" / "quicksight_gen" / "docs" / "Schema_v6.md"
CANONICAL_TODAY = date(2030, 1, 1)


# The fuzz seed: random per dev session by default, env-var-overridable
# for CI determinism (M.2d.9.3). Resolved once at module import so every
# test in this file sees the same fuzz instance within a run.
def _resolve_fuzz_seed() -> int:
    override = QS_GEN_FUZZ_SEED.get_or_none()
    if override is not None:
        return override
    # secrets.randbits is cryptographically random — different across
    # dev runs, surfacing new shapes each test invocation.
    return secrets.randbits(32)


FUZZ_SEED: int = _resolve_fuzz_seed()


# Fuzz YAML materialized once per pytest session into a known location
# under the repo's tmp area so a failure can be re-loaded for triage.
# (Path is in the gitignored tests/l2/fuzz_failures/ dir.)
_FUZZ_DUMP_DIR = REPO_ROOT / "tests" / "l2" / "fuzz_failures"


def _fuzz_yaml_path() -> Path:
    """Materialize fuzz YAML to a stable path keyed by seed.

    xdist-safe: per-PID temp filenames + atomic ``os.replace`` for the
    final file, so multiple worker processes that race on collection
    don't corrupt the canonical path. If two workers race to the
    final ``os.replace``, the loser's identical content overwrites
    the winner's — harmless because both bytes are byte-identical
    (same seed → same text → same hash).
    """
    _FUZZ_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    p = _FUZZ_DUMP_DIR / f"fuzz_seed_{FUZZ_SEED}.yaml"
    if p.exists():
        return p
    text = random_l2_yaml(FUZZ_SEED)
    final_tmp = _FUZZ_DUMP_DIR / f"_final_pid_{os.getpid()}.yaml"
    final_tmp.write_text(text)
    os.replace(final_tmp, p)
    return p


def _write_temp(text: str) -> Path:
    """Write to a sibling tmp file just for the pre-lock load."""
    tmp = _FUZZ_DUMP_DIR / f"_tmp_seed_{FUZZ_SEED}_pid_{os.getpid()}.yaml"
    tmp.write_text(text)
    return tmp


# The L2 fixtures that every contract test runs against. Adding a new
# YAML here parameterizes the entire file at once — that's the M.2d.8
# headline ergonomics. M.3.1 will add `sasquatch_pr.yaml` and inherit
# all assertions. The fuzz entry is resolved at module import so the
# id reflects the actual seed in the test name (helps when triaging
# failures).
L2_INSTANCES = [
    pytest.param(L2_DIR / "spec_example.yaml", id="spec_example"),
    pytest.param(L2_DIR / "sasquatch_pr.yaml", id="sasquatch_pr"),
    pytest.param(
        _fuzz_yaml_path(),
        id=f"fuzz-seed-{FUZZ_SEED}",
    ),
]


# -- Fixtures: the matrix subject ------------------------------------------


@pytest.fixture(params=L2_INSTANCES)
def l2_yaml(request) -> Path:
    return request.param


@pytest.fixture
def instance(l2_yaml: Path) -> L2Instance:
    return load_instance(l2_yaml)


@pytest.fixture
def auto_seed_sql(instance: L2Instance) -> str:
    """Auto-derived seed SQL for the parameterized instance.

    Uses the canonical reference date so the SQL is stable across days
    (matches the YAML's seed_hash semantics).
    """
    report = default_scenario_for(instance, today=CANONICAL_TODAY)
    return emit_seed(instance, report.scenario)


@pytest.fixture(autouse=True, scope="session")
def _announce_fuzz_seed(request) -> None:
    """Print the fuzz seed at session start so passing runs surface it,
    and the matrix's `fuzz-seed-N` test id matches the printed seed."""
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        override = "QS_GEN_FUZZ_SEED" in os.environ
        suffix = " (from QS_GEN_FUZZ_SEED)" if override else " (random; pin via QS_GEN_FUZZ_SEED=N)"
        reporter.write_sep(
            "-", f"fuzz seed for L2 contract matrix: {FUZZ_SEED}{suffix}",
        )


def _fuzz_repro_hint() -> str:
    """A trailing hint included in every assertion message on the fuzz
    entry — gives the developer the exact command to reproduce."""
    return (
        f"\n\n[reproduce] QS_GEN_FUZZ_SEED={FUZZ_SEED} pytest "
        f"tests/test_l2_seed_contract.py\n"
        f"[fuzz YAML] {_fuzz_yaml_path()}"
    )


def _is_fuzz_instance(instance: L2Instance) -> bool:
    return str(instance.instance).startswith("fuzz_seed_")


def _hint_if_fuzz(instance: L2Instance) -> str:
    return _fuzz_repro_hint() if _is_fuzz_instance(instance) else ""


# -- SQL parsing helpers ----------------------------------------------------


_INSERT_COLS_RE = re.compile(
    r"INSERT INTO \w+_(?P<table>transactions|daily_balances) "
    r"\((?P<cols>[^)]+)\)",
)
_VALUES_RE = re.compile(
    r"^  \((?P<row>.*)\)[,;]?\s*$",
    re.MULTILINE,
)


def _columns_in_insert(sql: str, table: str) -> list[str]:
    """Return the column-list (in declaration order) for the named table."""
    for m in _INSERT_COLS_RE.finditer(sql):
        if m.group("table") == table:
            return [c.strip() for c in m.group("cols").split(",")]
    return []


def _value_rows(sql: str) -> list[list[str]]:
    """Pull each VALUES row as a list of textual literals.

    Lightweight — splits on ``,`` outside quotes. Good enough for
    contract-shape assertions; not a full SQL parser.
    """
    rows: list[list[str]] = []
    for m in _VALUES_RE.finditer(sql):
        rows.append(_split_csv_quoted(m.group("row")))
    return rows


def _split_csv_quoted(line: str) -> list[str]:
    """Split a comma-separated SQL VALUES row, respecting single-quoted strings."""
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "'" and (i + 1 < len(line)) and line[i + 1] == "'" and in_quote:
            # Escaped single quote inside a string literal.
            buf.append("''")
            i += 2
            continue
        if c == "'":
            in_quote = not in_quote
            buf.append(c)
            i += 1
            continue
        if c == "," and not in_quote:
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _quoted_strings(text: str) -> set[str]:
    """All single-quoted string literals in the input."""
    return set(re.findall(r"'((?:[^']|'')*)'", text))


# -- Schema_v6 column-list parsing -----------------------------------------


def _columns_documented_for(table_suffix: str) -> set[str]:
    """Read Schema_v6.md's `## Table N — {{ l2_instance_name }}_<table>`
    heading and pull the backticked column names from the immediately-
    following `### Columns` table.

    ``table_suffix`` is ``"transactions"`` or ``"daily_balances"``.
    """
    doc = SCHEMA_DOC.read_text()
    # The doc uses mkdocs-macros placeholder `{{ l2_instance_name }}`
    # for the per-instance prefix (committed at ff40c8e). Match the raw
    # template form on disk — we don't render macros for this check.
    prefix_token = r"\{\{ l2_instance_name \}\}"
    section_re = re.compile(
        rf"^## Table \d+ — `{prefix_token}_{re.escape(table_suffix)}`",
        re.MULTILINE,
    )
    m = section_re.search(doc)
    if m is None:
        raise AssertionError(
            f"Schema_v6.md missing section heading for "
            f"`{{{{ l2_instance_name }}}}_{table_suffix}`"
        )
    # Read until the next `## ` heading.
    rest = doc[m.end():]
    next_section = re.search(r"^## ", rest, re.MULTILINE)
    section_body = rest[: next_section.start()] if next_section else rest

    # Find the `### Columns` block and collect backticked column names
    # from the first column of the table that follows.
    cols_idx = section_body.find("### Columns")
    if cols_idx == -1:
        raise AssertionError(
            f"`### Columns` subheading missing under "
            f"`{{{{ l2_instance_name }}}}_{table_suffix}`"
        )
    cols_body = section_body[cols_idx:]
    # Stop at the next `### ` subheading.
    next_sub = re.search(r"^### ", cols_body[3:], re.MULTILINE)
    if next_sub:
        cols_body = cols_body[: next_sub.start() + 3]

    # Pull the first cell of each table row: `| \`column_name\` | ... |`.
    column_names = set(re.findall(
        r"^\|\s*`([a-z_][a-z0-9_]*)`\s*\|", cols_body, re.MULTILINE,
    ))
    if not column_names:
        raise AssertionError(
            f"No backticked column names found under "
            f"`<prefix>_{table_suffix}` ### Columns table"
        )
    return column_names


# -- Determinism ------------------------------------------------------------


def test_auto_seed_is_byte_deterministic(instance, auto_seed_sql) -> None:
    """Two emit_seed runs with the same instance + canonical today =
    byte-identical output. The YAML's seed_hash assumes this."""
    report_a = default_scenario_for(instance, today=CANONICAL_TODAY)
    report_b = default_scenario_for(instance, today=CANONICAL_TODAY)
    assert emit_seed(instance, report_a.scenario) == emit_seed(
        instance, report_b.scenario,
    )
    assert emit_seed(instance, report_a.scenario) == auto_seed_sql


# -- Account contract -------------------------------------------------------


def test_seed_account_roles_resolve_to_instance(
    instance: L2Instance, auto_seed_sql: str,
) -> None:
    """Every account_role literal in the seed comes from a declared
    Account.role or AccountTemplate.role."""
    declared_roles = {
        str(a.role) for a in instance.accounts if a.role is not None
    } | {
        str(t.role) for t in instance.account_templates
    }
    txn_cols = _columns_in_insert(auto_seed_sql, "transactions")
    db_cols = _columns_in_insert(auto_seed_sql, "daily_balances")
    role_idx_txn = txn_cols.index("account_role") if "account_role" in txn_cols else None
    role_idx_db = db_cols.index("account_role") if "account_role" in db_cols else None

    seen_roles: set[str] = set()
    in_txn = "INSERT INTO " in auto_seed_sql.split("VALUES")[0]
    # Walk the rows: parse out the `account_role` column from each.
    # We find INSERT chunks and process them in order.
    chunks = re.split(r"INSERT INTO \w+_(\w+) \([^)]+\) VALUES\n", auto_seed_sql)
    # chunks alternates [pre, table_name, body, table_name, body, ...]
    for table_idx in range(1, len(chunks), 2):
        table = chunks[table_idx]
        body = chunks[table_idx + 1]
        if table == "transactions" and role_idx_txn is not None:
            for parts in [_split_csv_quoted(m.group("row"))
                          for m in _VALUES_RE.finditer(body)]:
                if len(parts) > role_idx_txn:
                    seen_roles.add(parts[role_idx_txn].strip("'"))
        elif table == "daily_balances" and role_idx_db is not None:
            for parts in [_split_csv_quoted(m.group("row"))
                          for m in _VALUES_RE.finditer(body)]:
                if len(parts) > role_idx_db:
                    seen_roles.add(parts[role_idx_db].strip("'"))

    undeclared = seen_roles - declared_roles
    assert not undeclared, (
        f"Auto-seed for {instance.instance!r} emitted account_role values "
        f"not declared in instance.accounts/account_templates: {sorted(undeclared)!r}"
        + _hint_if_fuzz(instance)
    )


def test_seed_account_ids_resolve_to_instance_or_synthetic_template(
    instance: L2Instance, auto_seed_sql: str,
) -> None:
    """Every account_id literal in the seed is either a declared singleton
    Account.id OR a synthetic template-instance id the auto-scenario
    materialized.

    Synthetic ids are derived by calling ``_materialize_instances`` on
    every declared AccountTemplate — so M.4.2b's per-template
    ``instance_id_template`` opt-in (e.g. sasquatch_pr's
    ``cust-{n:04d}-snb``) is naturally accepted alongside the legacy
    ``cust-{n:03d}`` default. Hardcoding the legacy pattern would
    falsely reject any persona that opts in.
    """
    from quicksight_gen.common.l2.auto_scenario import _materialize_instances
    declared_ids = {str(a.id) for a in instance.accounts}
    synthetic_ids: set[str] = set()
    for tmpl in instance.account_templates:
        for cust in _materialize_instances(tmpl):
            synthetic_ids.add(str(cust.account_id))
    allowed = declared_ids | synthetic_ids

    seen_ids: set[str] = set()
    txn_cols = _columns_in_insert(auto_seed_sql, "transactions")
    db_cols = _columns_in_insert(auto_seed_sql, "daily_balances")
    chunks = re.split(r"INSERT INTO \w+_(\w+) \([^)]+\) VALUES\n", auto_seed_sql)
    for table_idx in range(1, len(chunks), 2):
        table = chunks[table_idx]
        body = chunks[table_idx + 1]
        cols = txn_cols if table == "transactions" else db_cols
        if "account_id" not in cols:
            continue
        idx = cols.index("account_id")
        for parts in [_split_csv_quoted(m.group("row"))
                      for m in _VALUES_RE.finditer(body)]:
            if len(parts) > idx:
                seen_ids.add(parts[idx].strip("'"))

    undeclared = seen_ids - allowed
    assert not undeclared, (
        f"Auto-seed for {instance.instance!r} emitted account_id values "
        f"that are neither declared singletons nor synthetic "
        f"template-instances: {sorted(undeclared)!r}"
        + _hint_if_fuzz(instance)
    )


# -- Rail + transfer_type contract -----------------------------------------


def test_seed_rail_names_resolve_to_instance(
    instance: L2Instance, auto_seed_sql: str,
) -> None:
    """Every rail_name literal in the seed = a declared Rail.name."""
    declared = {str(r.name) for r in instance.rails}
    txn_cols = _columns_in_insert(auto_seed_sql, "transactions")
    if "rail_name" not in txn_cols:
        pytest.skip("transactions INSERT doesn't include rail_name column")
    idx = txn_cols.index("rail_name")
    seen: set[str] = set()
    chunks = re.split(r"INSERT INTO \w+_(\w+) \([^)]+\) VALUES\n", auto_seed_sql)
    for table_idx in range(1, len(chunks), 2):
        if chunks[table_idx] != "transactions":
            continue
        body = chunks[table_idx + 1]
        for parts in [_split_csv_quoted(m.group("row"))
                      for m in _VALUES_RE.finditer(body)]:
            if len(parts) > idx:
                lit = parts[idx]
                if lit != "NULL":
                    seen.add(lit.strip("'"))
    undeclared = seen - declared
    assert not undeclared, (
        f"Auto-seed for {instance.instance!r} emitted rail_name values "
        f"not declared in instance.rails: {sorted(undeclared)!r}"
        + _hint_if_fuzz(instance)
    )


def test_seed_transfer_types_resolve_to_instance(
    instance: L2Instance, auto_seed_sql: str,
) -> None:
    """Every transfer_type literal in the seed = a declared
    Rail.transfer_type OR TransferTemplate.transfer_type (M.3.10g —
    TT plants emit the template's own transfer_type, which is a
    separate declaration from any leg_rail's transfer_type per SPEC).
    Catches drift where the auto-scenario or seed code emits a
    transfer_type the L2 doesn't actually declare."""
    declared = {r.name for r in instance.rails}
    declared.update(t.name for t in instance.transfer_templates)
    txn_cols = _columns_in_insert(auto_seed_sql, "transactions")
    if "transfer_type" not in txn_cols:
        pytest.skip("transactions INSERT doesn't include transfer_type column")
    idx = txn_cols.index("transfer_type")
    seen: set[str] = set()
    chunks = re.split(r"INSERT INTO \w+_(\w+) \([^)]+\) VALUES\n", auto_seed_sql)
    for table_idx in range(1, len(chunks), 2):
        if chunks[table_idx] != "transactions":
            continue
        body = chunks[table_idx + 1]
        for parts in [_split_csv_quoted(m.group("row"))
                      for m in _VALUES_RE.finditer(body)]:
            if len(parts) > idx:
                seen.add(parts[idx].strip("'"))
    undeclared = seen - declared
    assert not undeclared, (
        f"Auto-seed for {instance.instance!r} emitted transfer_type values "
        f"not declared on any Rail.transfer_type: {sorted(undeclared)!r}"
        + _hint_if_fuzz(instance)
    )


# -- Metadata key contract --------------------------------------------------


# Seed-infrastructure keys not declared on any Rail's metadata_keys but
# always emitted by the seed's helper rows. customer_id is added to every
# row for cross-walk traceability; external_reference is added on the
# drift background's external counter-leg; sender_id / recipient_id tag
# the InvFanoutPlant legs so the Investigation matviews can cross-walk
# the planted fanout edges back to their (sender, recipient) pair.
_INFRA_METADATA_KEYS = {
    "customer_id", "external_reference", "sender_id", "recipient_id",
}


def test_seed_metadata_keys_subset_of_rail_declarations(
    instance: L2Instance, auto_seed_sql: str,
) -> None:
    """Every JSON metadata key in the seed comes from one of:
    a Rail.metadata_keys entry, a TransferTemplate.transfer_key entry
    (M.3.10g — TT plants emit transfer_key fields per SPEC's "same
    transfer_key joins one shared Transfer" rule), OR the small set
    of seed-infra keys."""
    declared: set[str] = set(_INFRA_METADATA_KEYS)
    for r in instance.rails:
        declared.update(str(k) for k in r.metadata_keys)
    for t in instance.transfer_templates:
        declared.update(str(k) for k in t.transfer_key)

    # Pull keys from every metadata literal — `'key': 'value'`.
    metadata_lits = re.findall(r"'(\{[^']*\})'", auto_seed_sql)
    seen_keys: set[str] = set()
    for lit in metadata_lits:
        for k in re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', lit):
            seen_keys.add(k)

    undeclared = seen_keys - declared
    assert not undeclared, (
        f"Auto-seed for {instance.instance!r} emitted metadata keys "
        f"not declared on any Rail.metadata_keys / TransferTemplate."
        f"transfer_key (and not infra-keys): "
        f"{sorted(undeclared)!r}. Either add them to the relevant "
        f"rail's metadata_keys, or add them to _INFRA_METADATA_KEYS "
        f"in this test."
        + _hint_if_fuzz(instance)
    )


# -- Schema_v6 column-list contract ----------------------------------------


def test_seed_columns_match_schema_v6_documented_set(
    instance: L2Instance, auto_seed_sql: str,
) -> None:
    """Every column referenced in the seed's INSERT lists is documented
    in Schema_v6.md's `<prefix>_<table>` ### Columns table."""
    for table in ("transactions", "daily_balances"):
        emitted = set(_columns_in_insert(auto_seed_sql, table))
        if not emitted:
            continue  # auto-scenario may produce no rows for this table
        documented = _columns_documented_for(table)
        undocumented = emitted - documented
        assert not undocumented, (
            f"Auto-seed for {instance.instance!r} INSERT INTO "
            f"<prefix>_{table} references columns missing from "
            f"Schema_v6.md's `<prefix>_{table}` documentation: "
            f"{sorted(undocumented)!r}. Update the doc OR drop the "
            f"columns from the seed."
            + _hint_if_fuzz(instance)
        )


# X.1.k — broad / l1_plus_broad hash-locks deleted. The locked SQL
# files at tests/data/_locked_seeds/<instance>.<dialect>.sql cover the
# l1_plus_broad mode (which is what `data apply` actually emits via
# `cli/_helpers.py::build_full_seed_sql`); the broad-only intermediate
# scenario is no longer separately verified here. Broad mode is still
# functionally exercised by the e2e harness — if it drifts the
# dashboards regress, just not via a hash mismatch surfaced here.
