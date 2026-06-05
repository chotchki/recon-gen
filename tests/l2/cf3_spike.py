"""CF.3 diagram-density spike harness — render + measure topology layers.

Loads an L2 yaml, renders L1/L2/L3 via ``build_topology_graph_per_rail``,
runs the resulting DOT through the system ``dot`` binary, and captures
legibility metrics for cold-read comparison.

Reuses the same ``dot 14.1.5`` toolchain that produced the
``docs/audits/v13_1_1_diagram.md`` baseline numbers (181 crossings,
2937pt height on the demo L2 at layer 3), so post-spike measurements
compare apples-to-apples with that audit.

Usage:

    .venv/bin/python -m tests.l2.cf3_spike render \\
        --yaml tests/l2/heavy_density_v1.yaml \\
        --label heavy_density_v1 \\
        --out docs/audits/cf_3_diagram_spike/heavy_density_v1

The output directory gets ``l{1,2,3}.svg`` (the rendered diagrams),
``l{1,2,3}.dot`` (the DOT source, for reproducibility), and a
``metrics.json`` aggregating per-layer numbers. ``gen`` subcommand
materializes a YAML from an inline FuzzPlan for quick iteration.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Same-directory import works because tests/l2/ is a package.
from tests.l2.fuzz import FuzzPlan, random_l2_yaml_from_plan

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.topology import build_topology_graph_per_rail


# dot -v emits this on stderr at the end of mincross. The number is the
# best (= lowest) crossing count graphviz found across mincross iterations.
_CROSSINGS_RE = re.compile(r"mincross.*?best_cross\s+(\d+)", re.DOTALL)

# SVG header line: <svg ... width="Xpt" height="Ypt" viewBox="...">
_WIDTH_RE = re.compile(r'\bwidth="([\d.]+)pt"')
_HEIGHT_RE = re.compile(r'\bheight="([\d.]+)pt"')


@dataclass(frozen=True, slots=True)
class LayerMetrics:
    layer: int
    n_nodes: int
    n_edges: int
    crossings: int | None
    width_pt: float | None
    height_pt: float | None
    layout_ms: float
    dot_bytes: int
    svg_bytes: int


def _count_nodes_edges(svg_text: str) -> tuple[int, int]:
    """Exact node + edge count from rendered SVG.

    Dot stamps every node with ``<g class="node">`` and every edge with
    ``<g class="edge">`` regardless of styling, so substring counts are
    exact (cheaper than parsing the SVG, and the markup is stable across
    graphviz versions). Counting from the DOT source is harder because
    node IDs can be bare or quoted and clusters/subgraph headers look
    similar.
    """
    return svg_text.count('class="node"'), svg_text.count('class="edge"')


def _render_with_dot(
    dot_source: str, *, fmt: str
) -> tuple[bytes, bytes, float]:
    """Pipe DOT through `dot -T<fmt> -v` and return (stdout, stderr, ms).

    Verbose mode (`-v`) is on so crossings appear on stderr — this is
    the only way graphviz exposes the mincross metric externally.
    """
    t0 = time.perf_counter()
    proc = subprocess.run(  # noqa: S603,S607
        ["dot", f"-T{fmt}", "-v"],
        input=dot_source.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    layout_ms = (time.perf_counter() - t0) * 1000
    return proc.stdout, proc.stderr, layout_ms


def measure_layer(
    instance: Any, *, db_table_prefix: str, layer: int
) -> tuple[LayerMetrics, str, bytes]:
    """Render one layer and capture metrics + outputs.

    Returns ``(metrics, dot_source, svg_bytes)``.
    """
    g: Any = build_topology_graph_per_rail(
        instance, db_table_prefix=db_table_prefix, layer=layer,
    )
    dot_source: str = g.source

    svg_bytes, stderr_bytes, layout_ms = _render_with_dot(dot_source, fmt="svg")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    svg_text = svg_bytes.decode("utf-8", errors="replace")
    n_nodes, n_edges = _count_nodes_edges(svg_text)

    crossings_m = _CROSSINGS_RE.search(stderr_text)
    crossings = int(crossings_m.group(1)) if crossings_m else None

    width_m = _WIDTH_RE.search(svg_text)
    height_m = _HEIGHT_RE.search(svg_text)
    width_pt = float(width_m.group(1)) if width_m else None
    height_pt = float(height_m.group(1)) if height_m else None

    return (
        LayerMetrics(
            layer=layer,
            n_nodes=n_nodes,
            n_edges=n_edges,
            crossings=crossings,
            width_pt=width_pt,
            height_pt=height_pt,
            layout_ms=round(layout_ms, 1),
            dot_bytes=len(dot_source.encode("utf-8")),
            svg_bytes=len(svg_bytes),
        ),
        dot_source,
        svg_bytes,
    )


def cmd_render(args: argparse.Namespace) -> int:
    yaml_path = Path(args.yaml)
    out_dir = Path(args.out)
    label: str = args.label
    prefix: str = args.prefix

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[load] {yaml_path}")
    t0 = time.perf_counter()
    instance = load_instance(yaml_path)
    load_ms = (time.perf_counter() - t0) * 1000
    print(
        f"[load]   accounts={len(instance.accounts)} "
        f"account_templates={len(instance.account_templates)} "
        f"rails={len(instance.rails)} "
        f"transfer_templates={len(instance.transfer_templates)} "
        f"chains={len(instance.chains)} "
        f"limit_schedules={len(instance.limit_schedules)} "
        f"({load_ms:.0f}ms)",
    )

    layers: list[LayerMetrics] = []
    for layer in (1, 2, 3):
        print(f"[L{layer}] rendering...", flush=True)
        m, dot_source, svg_bytes = measure_layer(
            instance, db_table_prefix=prefix, layer=layer,
        )
        (out_dir / f"l{layer}.svg").write_bytes(svg_bytes)
        (out_dir / f"l{layer}.dot").write_text(dot_source)
        layers.append(m)
        print(
            f"[L{layer}]   nodes={m.n_nodes:>4d} edges={m.n_edges:>4d} "
            f"crossings={m.crossings!s:>5s} "
            f"size={m.width_pt}x{m.height_pt}pt "
            f"layout={m.layout_ms:.0f}ms",
        )

    metrics_path = out_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "label": label,
                "yaml": str(yaml_path),
                "yaml_bytes": yaml_path.stat().st_size,
                "instance_counts": {
                    "accounts": len(instance.accounts),
                    "account_templates": len(instance.account_templates),
                    "rails": len(instance.rails),
                    "transfer_templates": len(instance.transfer_templates),
                    "chains": len(instance.chains),
                    "limit_schedules": len(instance.limit_schedules),
                },
                "layers": [asdict(m) for m in layers],
            },
            indent=2,
        )
        + "\n",
    )
    print(f"[done] {metrics_path}")
    return 0


def cmd_gen(args: argparse.Namespace) -> int:
    plan = FuzzPlan(
        seed=args.seed,
        n_singleton_internal=args.singleton_internal,
        n_singleton_external=args.singleton_external,
        n_templates=args.account_templates,
        n_rails=args.rails,
        n_transfer_templates=args.transfer_templates,
        n_chains=args.chains,
        n_limit_schedules=args.limit_schedules,
        two_leg_ratio=args.two_leg_ratio,
        aggregating_count=args.aggregating,
        pending_age_probability=args.pending_age_p,
        description_probability=args.description_p,
    )
    yaml_text = random_l2_yaml_from_plan(plan)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml_text)
    print(f"[gen] seed={args.seed} bytes={len(yaml_text)} -> {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cf3_spike", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="render + measure L1/L2/L3 from a yaml")
    p_render.add_argument("--yaml", required=True, help="path to L2 yaml")
    p_render.add_argument("--label", required=True, help="identifier for this run")
    p_render.add_argument("--out", required=True, help="output directory")
    p_render.add_argument(
        "--prefix",
        default="spike",
        help="db_table_prefix passed to topology builder (default: spike)",
    )
    p_render.set_defaults(func=cmd_render)

    p_gen = sub.add_parser("gen", help="generate L2 yaml from FuzzPlan knobs")
    p_gen.add_argument("--seed", type=int, required=True)
    p_gen.add_argument("--out", required=True)
    p_gen.add_argument("--rails", type=int, default=100)
    p_gen.add_argument("--transfer-templates", type=int, default=30, dest="transfer_templates")
    p_gen.add_argument("--chains", type=int, default=12)
    p_gen.add_argument("--account-templates", type=int, default=4, dest="account_templates")
    p_gen.add_argument("--singleton-internal", type=int, default=8, dest="singleton_internal")
    p_gen.add_argument("--singleton-external", type=int, default=20, dest="singleton_external")
    p_gen.add_argument("--limit-schedules", type=int, default=6, dest="limit_schedules")
    p_gen.add_argument("--two-leg-ratio", type=float, default=0.6, dest="two_leg_ratio")
    p_gen.add_argument("--aggregating", type=int, default=2)
    p_gen.add_argument("--pending-age-p", type=float, default=0.3, dest="pending_age_p")
    p_gen.add_argument("--description-p", type=float, default=0.7, dest="description_p")
    p_gen.set_defaults(func=cmd_gen)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
