"""DS.7 — the semantic-lock --check, wired onto the chain.

The semantic lock (the violation-set snapshot at the canonical anchor) is
the successor to the byte-locked seeds: it gates on WHAT the detectors
find, not the SQL bytes that built it. The byte-compare test died with
CB.8 and nothing on the chain re-checked it — so a violation-set drift
(a law change, a universe fix, an off-by-a-cent identity shift) could
land unnoticed until someone happened to re-lock by hand. This wires the
check back into the unit tier, per instance.

It runs the SAME code path ``recon-gen data semantic-lock --check`` uses
(``_build_fresh_semantic_lock`` → byte-compare against the on-disk lock),
DuckDB-only, no container — so it's POLICY 1 by construction (identical
local and CI). A failure here means the emitted violation set no longer
matches the committed snapshot: investigate the diff (the CLI's
per-invariant count summary names which invariant moved), and re-lock
with ``recon-gen data semantic-lock --l2 <yaml>`` ONLY once the shift is
understood + intended.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.as_of_frame import LOCKED_ANCHOR
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.sql import Dialect
from recon_gen.cli.data import _build_fresh_semantic_lock  # pyright: ignore[reportPrivateUsage]: the check reuses the CLI's own fresh-lock builder so the wired gate and the operator's `--check` can never diverge

_REPO = Path(__file__).parent.parent.parent
_LOCKS = _REPO / "tests" / "data" / "_semantic_locks"
_INSTANCES = ("spec_example", "sasquatch_pr")


@pytest.mark.parametrize("instance_name", _INSTANCES)
def test_semantic_lock_matches_fresh_emit(instance_name: str) -> None:
    """The on-disk lock is byte-identical to a fresh emit at the
    canonical anchor. Drift = the violation set changed; re-lock only
    after understanding why."""
    yaml_path = _REPO / "tests" / "l2" / f"{instance_name}.yaml"
    instance = load_instance(yaml_path)
    fresh = _build_fresh_semantic_lock(
        instance, LOCKED_ANCHOR, prefix=instance_name, dialect=Dialect.DUCKDB,
    )
    locked_path = _LOCKS / f"{instance_name}.duckdb.json"
    assert locked_path.exists(), (
        f"semantic lock missing: {locked_path} — run "
        f"`recon-gen data semantic-lock --l2 {yaml_path}`"
    )
    on_disk = locked_path.read_text()
    if fresh == on_disk:
        return
    # Non-byte-equal: surface the per-invariant count delta (the same
    # summary the CLI prints) so the failure is readable even when the
    # raw diff is enormous.
    import json  # noqa: PLC0415 — only on the drift path
    from typing import cast  # noqa: PLC0415

    def _counts(text: str) -> dict[str, int]:
        payload: object = json.loads(text)
        assert isinstance(payload, dict)
        violations = cast("dict[str, object]", payload)["violations"]
        assert isinstance(violations, dict)
        out: dict[str, int] = {}
        for k, v in cast("dict[str, object]", violations).items():
            out[k] = len(cast("list[object]", v)) if isinstance(v, list) else 0
        return out

    old, new = _counts(on_disk), _counts(fresh)
    moved = [
        f"{name}: {old.get(name, 0)} -> {new.get(name, 0)}"
        for name in sorted(set(old) | set(new))
        if old.get(name, 0) != new.get(name, 0)
    ]
    pytest.fail(
        f"{instance_name} semantic lock drifted from the committed "
        f"snapshot. Per-invariant count delta:\n  "
        + ("\n  ".join(moved) if moved else "(no count change — identity / "
           "value drift; run the CLI --check for the raw diff)")
        + f"\nRe-lock with `recon-gen data semantic-lock --l2 "
        f"tests/l2/{instance_name}.yaml` ONLY once the shift is intended.",
    )
