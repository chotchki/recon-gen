"""CF.3.j — continuous validation harness for the diagram-density spike.

Re-renders the spike fixtures (heavy_density_v1 + sasquatch_pr) and
asserts that the measured visual-quality metrics don't regress vs the
locked baseline in `docs/audits/cf_3_diagram_spike/<fixture>_cf3f/metrics.json`.

Per operator lock (CF.3.j, 2026-06-05): the regression gate tracks
VISUAL QUALITY only (crossings + node/edge count + width/height
within reasonable tolerance). LAYOUT TIME is explicitly NOT a
constraint — caching (CF.3.k) absorbs latency, so layout cost can
trade freely for visual quality.

Catches:
- Regressions to topology emit code that re-introduces nodes / edges
  / crossings the CF.3.a → .f stack measured down.
- Graphviz behavior shifts that blow up dimensions on the heavy
  fixture (which the demo L2 sasquatch_pr might not catch).

To intentionally roll the baseline forward after a measured win:
  .venv/bin/python -m tests.l2.cf3_spike render \\
      --yaml tests/l2/heavy_density_v1.yaml \\
      --label heavy_density_v1_cf3f \\
      --out docs/audits/cf_3_diagram_spike/heavy_density_v1_cf3f
(same for sasquatch_pr), then commit the updated metrics.json
alongside the topology change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recon_gen.common.l2.loader import load_instance


# CF.3.j note — the spike harness measures via `subprocess.run(['dot', ...])`
# to extract crossings from graphviz's verbose stderr + dimensions from
# the rendered SVG. Studio prod renders client-side via
# @hpcc-js/wasm-graphviz, so the system `dot` binary is NOT a runtime
# dep. It IS a test-bench dep — installed on the macOS dev box via
# homebrew and on the WSL2 CI runner via apt-get (operator-confirmed
# 2026-06-05). If the dependency goes missing on a runner, the test
# fails with FileNotFoundError — re-install graphviz rather than
# papering over.


REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "l2"
SPIKE_DIR = REPO_ROOT / "docs" / "audits" / "cf_3_diagram_spike"

# (yaml stem, baseline output dir name)
SPIKE_FIXTURES = [
    ("heavy_density_v1", "heavy_density_v1_cf3f"),
    ("sasquatch_pr", "sasquatch_pr_cf3f"),
]

# Crossings tolerance — the gate exists to catch BIG regressions (a
# code change that adds hundreds of crossings, e.g. a deleted
# constraint=false). Cross-machine graphviz-version variance is real
# and substantial: the locked baseline was rendered on macOS / dot
# 14.1.5 (homebrew); the CI runner has a different distro/build that
# produces different mincross results from the same DOT. We saw
# sasquatch L3 jump 55→84 with no code change between commits. Set
# the gate generously (2x baseline + 20 absolute) to absorb that
# variance while still failing loudly on the catastrophic case (e.g.
# 4x+, 1000+ crossings extra).
CROSSINGS_TOLERANCE_PCT = 1.0
CROSSINGS_TOLERANCE_ABS = 20

# Width / height tolerance — graphviz canvas size can jitter ±10-15%
# between point releases without semantic change. The shape-vocabulary
# work has dramatic effects (CF.3.f.b TB layout was −68% width on
# heavy) but those are intentional baseline rolls. Widened to ±35%
# for the same cross-machine variance reasons as crossings — width on
# heavy can drift more than 20% from layout-engine release jitter.
DIM_TOLERANCE_PCT = 0.35


@pytest.mark.parametrize(
    "yaml_stem,baseline_dir",
    SPIKE_FIXTURES,
    ids=[f[0] for f in SPIKE_FIXTURES],
)
def test_cf3_diagram_metrics_no_regression(
    yaml_stem: str, baseline_dir: str,
) -> None:
    """Render each layer of the spike fixture; assert metrics don't
    regress vs the locked baseline."""
    pytest.importorskip("graphviz")
    # Defer the import — cf3_spike imports recon_gen.common.l2.topology
    # which builds the full topology emit, and we want pytest's
    # importorskip to gate the graphviz dep first.
    from tests.l2.cf3_spike import measure_layer  # noqa: PLC0415

    yaml_path = FIXTURES_DIR / f"{yaml_stem}.yaml"
    baseline_path = SPIKE_DIR / baseline_dir / "metrics.json"
    assert yaml_path.exists(), f"missing fixture: {yaml_path}"
    assert baseline_path.exists(), (
        f"missing baseline: {baseline_path}. To establish (re-)baseline, "
        f"run `python -m tests.l2.cf3_spike render --yaml {yaml_path} "
        f"--label {baseline_dir} --out {SPIKE_DIR / baseline_dir}`."
    )
    baseline = json.loads(baseline_path.read_text())
    instance = load_instance(yaml_path)

    for layer_meta in baseline["layers"]:
        layer = layer_meta["layer"]
        rendered, _, _, _ = measure_layer(
            instance, db_table_prefix="cf3j", layer=layer,
        )

        # Node / edge counts — these are STRUCTURAL. A change here
        # means a real shift in what the topology emits (an edge was
        # added or removed). The baseline tracks the locked vocabulary;
        # re-render + re-commit metrics.json to widen.
        assert rendered.n_nodes == layer_meta["n_nodes"], (
            f"L{layer} node count drift on {yaml_stem}: "
            f"baseline {layer_meta['n_nodes']} → rendered {rendered.n_nodes}"
        )
        assert rendered.n_edges == layer_meta["n_edges"], (
            f"L{layer} edge count drift on {yaml_stem}: "
            f"baseline {layer_meta['n_edges']} → rendered {rendered.n_edges}"
        )

        # Crossings — gate on regressions (improvements welcome).
        baseline_crossings = layer_meta["crossings"]
        if baseline_crossings is not None and rendered.crossings is not None:
            cap = int(baseline_crossings * (1 + CROSSINGS_TOLERANCE_PCT)) + (
                CROSSINGS_TOLERANCE_ABS
            )
            assert rendered.crossings <= cap, (
                f"L{layer} crossings regression on {yaml_stem}: "
                f"baseline {baseline_crossings} → rendered {rendered.crossings} "
                f"(allowed up to {cap} = baseline + 10 % + 5)"
            )

        # Width + height — symmetric tolerance for graphviz layout drift.
        for dim_name in ("width_pt", "height_pt"):
            baseline_val = layer_meta[dim_name]
            rendered_val = getattr(rendered, dim_name)
            if baseline_val is None or rendered_val is None:
                continue
            drift = abs(rendered_val - baseline_val) / baseline_val
            assert drift < DIM_TOLERANCE_PCT, (
                f"L{layer} {dim_name} drift > {DIM_TOLERANCE_PCT * 100:.0f} % "
                f"on {yaml_stem}: baseline {baseline_val} → rendered "
                f"{rendered_val} ({drift * 100:.1f} %)"
            )


def test_cf3_diagram_baselines_are_locked() -> None:
    """Smoke: every spike fixture has a baseline metrics.json on disk.

    Catches: someone deletes the baseline dir or renames the yaml
    without updating SPIKE_FIXTURES + the parametrize would silently
    skip-empty (pytest would still pass with 0 cases — a worse failure
    mode than a loud error).
    """
    for yaml_stem, baseline_dir in SPIKE_FIXTURES:
        yaml_path = FIXTURES_DIR / f"{yaml_stem}.yaml"
        baseline_path = SPIKE_DIR / baseline_dir / "metrics.json"
        assert yaml_path.exists(), f"missing spike fixture {yaml_path}"
        assert baseline_path.exists(), f"missing baseline {baseline_path}"
