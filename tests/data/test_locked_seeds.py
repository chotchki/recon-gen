"""X.1.k — locked-SQL byte check for the demo seed pipeline.

Each ``tests/data/_locked_seeds/<instance>.<dialect>.sql`` file is the
SHA256-stamped output that ``data apply`` would emit for the named
``(L2 instance, dialect)`` pair at the canonical anchor
``date(2030, 1, 1)``. This test re-emits and asserts byte-equality
against the locked file.

Auto-discovers files in the directory — adding a new (instance,
dialect) pair to the lock surface is "drop the file"; no Python
constant to maintain.

Refresh after a reviewed seed-shape change with
``quicksight-gen data lock -c <postgres-or-oracle config> --l2 <yaml>``
(once per dialect). The CLI keys off ``demo_database_url`` in the
config to pick which dialect's lock file to write.
"""

from __future__ import annotations

import difflib
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from quicksight_gen.cli._helpers import build_full_seed_sql
from quicksight_gen.common.l2 import load_instance
from quicksight_gen.common.sql.dialect import Dialect

from tests._test_helpers import make_test_config


_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCKED_DIR = _REPO_ROOT / "tests" / "data" / "_locked_seeds"
_L2_DIR = _REPO_ROOT / "tests" / "l2"

# Same anchor as the CLI (X.1.k _CANONICAL_LOCK_ANCHOR). Drift this and
# every locked file becomes wrong; pin it here as a test invariant.
_CANONICAL_ANCHOR = date(2030, 1, 1)


def _discover_locked_files() -> list[pytest.ParameterSet]:
    if not _LOCKED_DIR.exists():
        return []
    out: list[pytest.ParameterSet] = []
    for p in sorted(_LOCKED_DIR.glob("*.sql")):
        # filename: <instance>.<dialect>.sql
        stem = p.stem  # "<instance>.<dialect>"
        if stem.count(".") != 1:
            raise RuntimeError(
                f"Locked seed file has unexpected name: {p.name!r}. "
                f"Expected `<instance>.<dialect>.sql`."
            )
        instance_name, dialect_name = stem.rsplit(".", 1)
        out.append(pytest.param(p, instance_name, dialect_name, id=stem))
    return out


_LOCKED_FILES = _discover_locked_files()


@pytest.mark.skipif(
    not _LOCKED_FILES,
    reason="no locked seed files found under tests/data/_locked_seeds/",
)
@pytest.mark.parametrize(
    "locked_path,instance_name,dialect_name", _LOCKED_FILES,
)
def test_locked_seed_matches_fresh_emit(
    locked_path: Path, instance_name: str, dialect_name: str,
) -> None:
    """Re-emit the seed for ``(instance, dialect)`` at the canonical
    anchor and assert it matches the locked file byte-for-byte.

    On drift, fail with a unified diff of the first ~50 changed lines
    so the reviewer sees the actual SQL shift, not just a hash flip.
    Re-lock with ``quicksight-gen data lock -c <config> --l2 <yaml>``.
    """
    yaml_path = _L2_DIR / f"{instance_name}.yaml"
    assert yaml_path.exists(), (
        f"Lock file {locked_path.name} references L2 instance "
        f"{instance_name!r} but {yaml_path} doesn't exist. "
        f"Either rename the lock file or restore the YAML."
    )
    instance = load_instance(yaml_path)
    # Z.C — db_table_prefix is now a required cfg field; pin to the
    # locked-seed instance name (was previously stamped via
    # `with_l2_instance_prefix(instance_name)`).
    cfg = make_test_config(
        dialect=Dialect(dialect_name),
        db_table_prefix=instance_name,
    )
    fresh = build_full_seed_sql(cfg, instance, anchor=_CANONICAL_ANCHOR)

    on_disk = locked_path.read_text()
    if fresh == on_disk:
        return
    diff = list(difflib.unified_diff(
        on_disk.splitlines(keepends=True),
        fresh.splitlines(keepends=True),
        fromfile=f"locked/{locked_path.name}",
        tofile=f"fresh/{locked_path.name}",
        n=2,
    ))
    snippet = "".join(diff[:50])
    truncated = (
        f"\n... ({len(diff) - 50} more diff lines truncated)"
        if len(diff) > 50 else ""
    )
    pytest.fail(
        f"Locked seed drifted from fresh emit for "
        f"({instance_name!r}, {dialect_name!r}).\n"
        f"Re-lock with: quicksight-gen data lock -c <config.yaml> "
        f"--l2 tests/l2/{instance_name}.yaml\n\n"
        f"First 50 diff lines:\n{snippet}{truncated}"
    )


def test_lock_dir_only_holds_known_dialects() -> None:
    """Every file under ``_locked_seeds/`` must end ``.<dialect>.sql``
    for a real Dialect enum value. Catches typos like ``...postgress.sql``."""
    if not _LOCKED_DIR.exists():
        pytest.skip("lock dir not yet created")
    valid = {d.value for d in Dialect}
    for p in _LOCKED_DIR.glob("*.sql"):
        stem = p.stem
        assert stem.count(".") == 1, (
            f"Lock filename {p.name!r} should have exactly one dot "
            f"between instance and dialect."
        )
        _, dialect_name = stem.rsplit(".", 1)
        assert dialect_name in valid, (
            f"Lock filename {p.name!r} cites unknown dialect "
            f"{dialect_name!r}; valid: {sorted(valid)}"
        )


# Y.2.gate.c.13.1 — `--seed-density=N` tunable parameter tests.


def _emit_at(density: float, *, dialect_name: str = "postgres") -> str:
    """Helper: emit the spec_example seed at a given density."""
    instance = load_instance(_L2_DIR / "spec_example.yaml")
    cfg = make_test_config(
        dialect=Dialect(dialect_name),
        db_table_prefix="spec_example",
    )
    return build_full_seed_sql(
        cfg, instance, anchor=_CANONICAL_ANCHOR, density=density,
    )


def test_density_default_is_one_byte_identical_to_no_arg() -> None:
    """Y.2.gate.c.13.1 contract: density=1.0 (default) == no-density-arg call.
    Without this, the locked SQL files would shift on every density
    integration. The 4 locked-seeds tests above ALSO enforce this implicitly,
    but a direct assertion documents the contract."""
    instance = load_instance(_L2_DIR / "spec_example.yaml")
    cfg = make_test_config(
        dialect=Dialect.POSTGRES,
        db_table_prefix="spec_example",
    )

    no_arg = build_full_seed_sql(cfg, instance, anchor=_CANONICAL_ANCHOR)
    explicit_one = build_full_seed_sql(
        cfg, instance, anchor=_CANONICAL_ANCHOR, density=1.0,
    )
    assert no_arg == explicit_one, (
        "density=1.0 (the documented default) must produce the same bytes "
        "as omitting the argument entirely. If this drifts, the locked SQL "
        "files would also drift on every density integration."
    )


def test_density_two_x_produces_more_rows_than_one_x() -> None:
    """density>1.0 produces more rows (more INSERT lines). Smoke proof
    that the parameter actually scales output, not just plumbed."""
    one_x = _emit_at(1.0)
    two_x = _emit_at(2.0)
    one_x_inserts = one_x.count("INSERT INTO")
    two_x_inserts = two_x.count("INSERT INTO")
    assert two_x_inserts > one_x_inserts, (
        f"density=2.0 should produce more INSERTs than density=1.0; "
        f"got {two_x_inserts} vs {one_x_inserts}."
    )


def test_density_half_x_produces_fewer_rows_than_one_x() -> None:
    """density<1.0 produces fewer rows. Useful for fast-iteration runs
    where the operator wants a thinner seed."""
    one_x = _emit_at(1.0)
    half_x = _emit_at(0.5)
    one_x_inserts = one_x.count("INSERT INTO")
    half_x_inserts = half_x.count("INSERT INTO")
    assert half_x_inserts < one_x_inserts, (
        f"density=0.5 should produce fewer INSERTs than density=1.0; "
        f"got {half_x_inserts} vs {one_x_inserts}."
    )


def test_density_emits_different_sha256_header_at_different_densities() -> None:
    """Different densities produce different content → different SHA256
    in the `-- SHA256: <hex>` header. Verifies the hash-stamp reflects
    density (so per-run drift detection in c.3 will surface density changes
    cleanly)."""
    one_x = _emit_at(1.0)
    two_x = _emit_at(2.0)
    one_x_hash = one_x.split("\n", 1)[0]  # first line: -- SHA256: <hex>
    two_x_hash = two_x.split("\n", 1)[0]
    assert one_x_hash.startswith("-- SHA256:")
    assert two_x_hash.startswith("-- SHA256:")
    assert one_x_hash != two_x_hash, (
        "different densities must hash to different values"
    )
