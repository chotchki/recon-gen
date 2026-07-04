"""EA.3 — timings-as-signal for the unit-tier wall-clock budgets.

A hard ``assert elapsed < BUDGET`` is calibrated to ONE machine's speed and
red-herrings the moment the suite runs somewhere slower — the EA.2 cloud
spike proved it: the ~9 budgets tuned to the 5800X3D/16-core self-hosted box
all blow on a 4-core GitHub-hosted runner, failing the chain on machine speed
rather than a code regression.

The operator's stance ([[feedback_timings_as_smell]]) is that a timing is a
SIGNAL, not a gate: capture it, watch it drift run-over-run, ``⚠`` on a
regression — never hard-fail on an absolute. The runner already does this at
the LAYER grain (``timings.json`` diff, Y.2.gate.c.3); ``timing_signal``
extends it to the per-test batteries.

So the wall-clock CEILING becomes a recorded, greppable ``[timing]`` line
(captured in the layer's ``stdout.log`` artifact) with an ``over=`` flag the
operator + the runner summary can surface — but it does NOT fail the test.

A genuine LOWER-bound (``floor_s``) is different: "this operation must take at
least N seconds" is a BEHAVIORAL invariant (a debounce actually delayed, an
SLA sleep fired), independent of machine speed — that stays a hard assert.
"""
from __future__ import annotations

from recon_gen.common.env_keys import RECON_GEN_RUN_DIR


def timing_signal(
    name: str,
    elapsed_s: float,
    *,
    budget_s: float,
    floor_s: float | None = None,
) -> None:
    """Record a wall-clock measurement as a SIGNAL (never hard-fails on the
    ``budget_s`` ceiling — see module docstring). Prints a parseable
    ``[timing] name=… elapsed=… budget=… over=…`` line and, when the runner
    set ``RECON_GEN_RUN_DIR``, appends it to ``<run_dir>/unit-timings.jsonl``
    for run-over-run drift. ``floor_s`` (optional) keeps a genuine behavioral
    lower-bound as a HARD assert (machine-speed-independent)."""
    over = elapsed_s > budget_s
    print(
        f"[timing] name={name} elapsed={elapsed_s:.3f}s "
        f"budget={budget_s:.3f}s over={over}"
    )
    run_dir = RECON_GEN_RUN_DIR.get_or_none()
    if run_dir:
        import json
        from pathlib import Path

        line = {
            "name": name, "elapsed_s": round(elapsed_s, 4),
            "budget_s": budget_s, "over": over,
        }
        art = Path(run_dir) / "unit-timings.jsonl"
        art.parent.mkdir(parents=True, exist_ok=True)
        with art.open("a") as fh:
            fh.write(json.dumps(line) + "\n")
    if floor_s is not None:
        assert elapsed_s >= floor_s, (
            f"{name} took {elapsed_s:.3f}s — UNDER the {floor_s}s floor. "
            f"This is a BEHAVIORAL minimum (env-independent), not a perf "
            f"budget: the operation is expected to actually take time."
        )
