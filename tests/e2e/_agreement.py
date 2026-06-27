"""CB.5 addendum — cross-tier agreement helper.

Each renderer-specific test writes its rendered output to
`runs/<run-id>/<layer>/<name>.json`; the high-watermark validator
(at QS_BROWSER tier, the last layer in the runner's
`unit → db → app2 → deploy → api → browser` chain) reads all the
inputs and asserts agreement.

Why this shape (vs one monolithic agreement test):

- Each renderer's test is single-tier — clean tier classification
  without a multi-tier marker.
- A single broken renderer fails ITS OWN tier's test, not the
  agreement comparison — direct attribution instead of "agreement
  broke, which renderer?"
- The validator's `@inputs(...)` marker
  (`tests/_marks.py::inputs`) names the producer tests via pytest
  nodeids. Collection-time validation catches renamed / moved /
  deleted inputs before the runtime artifact-read silently misses.

Artifact contract:

- `runs/<run-id>/<layer>/<name>.json` is a JSON file containing the
  rendered rows. The `name` is a logical identifier the producer +
  consumer both agree on (e.g., "drift_sheet", "inv_money_trail").
- `runs/<run-id>` resolves from `RECON_GEN_RUN_DIR` which the
  runner sets per cell (cell-isolated by construction).
- The producer test is responsible for writing on success. If the
  producer test FAILS, the artifact is absent — the validator
  fails with "missing artifact: <layer>/<name>.json — did the
  producer test in tier <layer> fail or skip?"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any  # typing-smell: ignore[explicit-any]: artifact payload is arbitrary JSON; consumers know their own shape

import pytest

from recon_gen.common.env_keys import RECON_GEN_RUN_DIR


def _runs_dir() -> Path:
    """Resolve the per-cell runs root from `RECON_GEN_RUN_DIR` (env
    var the runner sets in every subprocess). Raises a clear error if
    unset — agreement artifacts only make sense when the runner is
    in charge of orchestration.

    Standalone `pytest tests/...` invocations (without the runner)
    have no run dir; agreement validators will fail to write/read.
    That's correct — the validator's contract is "the runner chain
    has already run my inputs."
    """
    raw = RECON_GEN_RUN_DIR.get_or_none()
    if raw is None:
        raise RuntimeError(
            "RECON_GEN_RUN_DIR is unset — the agreement-artifact "
            "helper only works under `./run_tests.sh up_to=<layer>` "
            "where the runner sets the per-cell run dir. Standalone "
            "`pytest tests/...` invocations skip this contract."
        )
    return Path(raw)


def _artifact_path(layer: str, name: str) -> Path:
    """Resolve the path to a tier-layer artifact, parents created
    on demand. Layer is the tier name (unit / db / app2 / qs_api /
    qs_browser); name is the consumer-agreed identifier.
    """
    out = _runs_dir() / layer / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def write_rendered_rows(layer: str, name: str, rows: list[Any]) -> None:  # typing-smell: ignore[explicit-any]: artifact payload — see module docstring
    """Producer side: write rendered rows from this renderer's test
    into the cell's run-dir artifact tree.

    The consumer's validator picks this up via `read_rendered_rows`
    AFTER its `@inputs(...)` dependency chain has run.

    Convention: rows is a list of dicts (one per rendered row); the
    consumer + producer agree on the dict shape. Keep it simple —
    just the columns the validator compares.
    """
    out = _artifact_path(layer, name)
    out.write_text(json.dumps(rows, indent=2, sort_keys=True))


def artifact_exists(layer: str, name: str) -> bool:
    """True iff the producer wrote this artifact.

    DW.3 — lets an agreement validator distinguish "producer legitimately
    skipped" from "producer ran". Used by the L2-OPTIONAL invariant
    validators (anomaly / money_trail): those producers `pytest.skip`
    when the L2 doesn't declare the roles the invariant needs, so their
    artifact is simply absent. A validator that hard-failed on absence
    would turn a legitimate "this invariant doesn't apply to this L2"
    into a red gate.

    Safe to skip on absence here (vs hiding a real producer failure): a
    producer FAILURE goes red in its OWN layer (db / app2), which halts
    the runner chain BEFORE the agreement layer ever runs. So by the time
    a validator reads artifacts, a missing one can only mean the producer
    skipped — never that it failed. The universal L1 invariants (drift /
    overdraft / …) keep hard-failing on absence; only the L2-optional
    inv invariants opt into skip-on-absence.
    """
    return _artifact_path(layer, name).exists()


def read_rendered_rows(layer: str, name: str) -> list[Any]:  # typing-smell: ignore[explicit-any]: artifact payload — see module docstring
    """Consumer side: read the artifact a producer test wrote.

    Raises a clear error if the artifact is absent — usually means
    the producer test failed or was skipped, OR the runner chain
    didn't include the producer's tier. Either way, the validator
    can't proceed.

    The error message gives the operator an actionable next step
    (re-run with higher up_to= layer; check producer test status).
    """
    out = _artifact_path(layer, name)
    if not out.exists():
        raise pytest.fail.Exception(
            f"agreement-validator missing artifact: "
            f"{out.relative_to(_runs_dir())}\n"
            f"  This usually means:\n"
            f"  - the producer test in tier {layer!r} failed or was "
            f"skipped (check the {layer} layer's stderr.log under "
            f"{_runs_dir()})\n"
            f"  - the runner chain didn't include the {layer} tier "
            f"(re-run with `./run_tests.sh up_to=<higher>` to fire "
            f"the full chain)\n"
            f"  - the producer test's `name` argument doesn't match "
            f"this consumer's (the convention: producer + consumer "
            f"agree on the artifact name)"
        )
    return list(json.loads(out.read_text()))
