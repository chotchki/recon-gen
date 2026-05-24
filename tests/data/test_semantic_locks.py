"""AZ.2 — semantic-lock gate (replacement for byte-locked seeds).

Per the AZ track: byte-locked seeds (`tests/data/_locked_seeds/*.sql`)
gate CI on per-line SQL text equality. They re-locked twice during
Phase AY for documented drift (per-row INSERT shift, metadata
payload additions) — each a 28 MB diff to review. The bytes encode
SQL formatting that isn't load-bearing; the violation set is what
we actually care about.

Semantic locks (`tests/data/_semantic_locks/*.json`, ~18-19 KB each)
gate on the per-invariant violation SET via `semantic_lock(conn,
ALL_INVARIANTS)`. Re-emit through `lock_to_json` is byte-stable
(sort order is deterministic); the test compares the rendered JSON
string against the on-disk file.

Per AZ.0's validation table:

  | Drift source             | Byte lock catches? | Semantic lock catches? |
  | per-row INSERT shift     | YES (false pos)    | NO ✓                   |
  | header text change       | YES (false pos)    | NO ✓                   |
  | metadata payload         | YES (false pos)    | NO ✓                   |
  | real violation change    | YES                | YES ✓                  |
  | new invariant added      | YES (huge diff)    | YES (small key add) ✓  |
  | violation drops out      | YES                | YES ✓                  |

Phase AZ.3 (dual-gate validation) keeps BOTH byte + semantic locks
running locally before AZ.5 deletes the byte-lock infra. CI flips to
semantic-only at AZ.4.

SQLite-only scope per AZ.1; PG/Oracle locks land at AZ.1.b if needed
before AZ.4's gate swap.
"""

from __future__ import annotations

import difflib
from datetime import date
from pathlib import Path

import pytest

from recon_gen.cli.data import _build_fresh_semantic_lock_sqlite
from recon_gen.common.as_of_frame import LOCKED_ANCHOR
from recon_gen.common.l2.loader import load_instance


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEMANTIC_LOCKS_DIR = _REPO_ROOT / "tests" / "data" / "_semantic_locks"
_L2_DIR = _REPO_ROOT / "tests" / "l2"
_CANONICAL_ANCHOR: date = LOCKED_ANCHOR


def _discover_locks() -> list[tuple[Path, str]]:
    """Find every `_semantic_locks/<instance>.sqlite.json` file.
    Returns `[(path, instance_name), ...]`."""
    if not _SEMANTIC_LOCKS_DIR.exists():
        return []
    out: list[tuple[Path, str]] = []
    for p in sorted(_SEMANTIC_LOCKS_DIR.glob("*.sqlite.json")):
        # Strip `.sqlite.json` suffix to get the instance name.
        instance_name = p.name[: -len(".sqlite.json")]
        out.append((p, instance_name))
    return out


_LOCKS = _discover_locks()


@pytest.mark.skipif(
    not _LOCKS,
    reason="no semantic locks discovered — run `recon-gen data semantic-lock --l2 <yaml>` first",
)
@pytest.mark.parametrize(
    "locked_path, instance_name",
    _LOCKS,
    ids=[p.name for p, _ in _LOCKS],
)
def test_semantic_lock_matches_fresh_emit(
    locked_path: Path, instance_name: str,
) -> None:
    """Re-build the semantic lock from a fresh in-memory SQLite +
    assert it matches the on-disk JSON byte-for-byte.

    On drift, fail with a unified diff of the first ~50 changed
    lines so the reviewer sees the actual violation-set shift, not
    just a hash flip. Re-lock with `recon-gen data semantic-lock
    --l2 tests/l2/<instance>.yaml`.

    Per AZ.0: JSON-string equality is the gate contract. Re-emit
    is byte-stable by construction (lock_to_json's sort order is
    deterministic; the on-disk write uses the same serializer).
    """
    yaml_path = _L2_DIR / f"{instance_name}.yaml"
    assert yaml_path.exists(), (
        f"Semantic lock {locked_path.name} references L2 instance "
        f"{instance_name!r} but {yaml_path} doesn't exist. "
        f"Either rename the lock file or restore the YAML."
    )
    instance = load_instance(yaml_path)
    fresh = _build_fresh_semantic_lock_sqlite(
        instance, _CANONICAL_ANCHOR, prefix=instance_name,
    )
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
    truncated = ""
    if len(diff) > 50:
        truncated = (
            f"\n  ... ({len(diff) - 50} more diff lines truncated)"
        )
    pytest.fail(
        f"Semantic lock drifted from fresh emit for "
        f"{instance_name!r}.\n"
        f"Re-lock with `recon-gen data semantic-lock --l2 "
        f"tests/l2/{instance_name}.yaml`.\n"
        f"Showing first 50 diff lines:\n"
        + "".join(diff[:50])
        + truncated
    )


# ---------------------------------------------------------------------------
# Fingerprint sanity — every lock declares its (instance, dialect, anchor).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _LOCKS, reason="no semantic locks discovered")
@pytest.mark.parametrize(
    "locked_path, instance_name",
    _LOCKS,
    ids=[p.name for p, _ in _LOCKS],
)
def test_semantic_lock_fingerprint_matches_filename(
    locked_path: Path, instance_name: str,
) -> None:
    """Cheap pre-check before the expensive emit: the lock file's
    `scenario_fingerprint` must match the (instance, dialect)
    encoded in the filename. Catches rename drift cleanly."""
    import json
    parsed = json.loads(locked_path.read_text())
    fp = parsed.get("scenario_fingerprint", {})
    assert fp.get("instance") == instance_name, (
        f"Lock {locked_path.name}'s fingerprint.instance={fp.get('instance')!r} "
        f"doesn't match the filename's instance {instance_name!r}. "
        f"Re-lock or rename to fix."
    )
    assert fp.get("dialect") == "sqlite", (
        f"Lock {locked_path.name}'s fingerprint.dialect={fp.get('dialect')!r} "
        f"doesn't match the filename's `.sqlite.json` suffix."
    )
    assert fp.get("canonical_anchor") == _CANONICAL_ANCHOR.isoformat(), (
        f"Lock {locked_path.name}'s anchor={fp.get('canonical_anchor')!r} "
        f"doesn't match LOCKED_ANCHOR ({_CANONICAL_ANCHOR.isoformat()!r}). "
        f"Re-lock with `recon-gen data semantic-lock`."
    )
    assert fp.get("schema_version") == 1, (
        f"Lock {locked_path.name} has schema_version={fp.get('schema_version')!r}; "
        f"this test gate is wired for schema_version=1. Bump the loader "
        f"when AZ.x lifts the version."
    )
