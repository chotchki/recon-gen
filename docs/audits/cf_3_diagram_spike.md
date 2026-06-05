# CF.3 — Diagram density spike (heavy fuzz seed + visual results)

**Status:** v0 measurements landed; awaiting operator cold-read on `heavy_density_v1`.

**Goal:** Build a heavy-density fuzz fixture + a measurement harness that lets CF.3.a→.f land against a stable scale-stress baseline. Per operator lock 2026-06-04: "iterate on the custom shapes" requires "a large seed... heavy rails+templates+chains to really see the benefits". This audit is the iteration loop's persistent state — what we've tried, what we measured, what the operator validated.

## Toolchain (matches the v13.1.1 audit baseline)

- `dot - graphviz version 14.1.5 (20260411.2331)` — same release the [`docs/audits/v13_1_1_diagram.md`](v13_1_1_diagram.md) "+58 template_member edges, constraint=false → 181→63 crossings" numbers were measured on. Apples-to-apples.
- Harness: `tests/l2/cf3_spike.py` — CLI with `gen` (FuzzPlan → yaml) and `render` (yaml → L1/L2/L3 SVGs + metrics).
- Fixture lock: `tests/l2/heavy_density_v1.yaml` — checked in; regen via `python -m tests.l2.cf3_spike gen --seed 42 --rails 100 --transfer-templates 30 --chains 12 --account-templates 4 --singleton-internal 8 --singleton-external 20 --limit-schedules 6 --two-leg-ratio 0.6 --aggregating 2 --out tests/l2/heavy_density_v1.yaml`.

## Fuzzer enhancement (post-spike side-benefit per operator lock)

`tests/l2/fuzz.py::_FuzzPlan` → public `FuzzPlan`; added public `random_l2_yaml_from_plan(plan: FuzzPlan) -> str`. The default-ranged `random_l2_yaml(seed)` dogfood path is unchanged (still byte-stable). The new entry lets callers drive the generator outside the default ranges — heavy-density stress, surgical reproductions, deliberate edge-density configurations. `_build_instance` survives heavy density unmodified (no caps tripped at 100 rails / 30 templates / 12 chains). Reusable for scale-perf benchmarks, future audit fixtures, etc.

## v0 measurements

| Layer | Metric             | sasquatch_pr (baseline) | heavy_density_v1     | Δ vs baseline      |
|-------|--------------------|--------------------------|------------------------|----------------------|
| L1    | nodes              | 14                       | 31                     | +17 (2.2×)          |
| L1    | edges              | 3                        | 4                      | +1                   |
| L1    | crossings          | 0                        | 0                      | —                    |
| L1    | size (pt)          | 305 × 514                | 242 × 1324             | 1.0× wide, 2.6× tall |
| L1    | layout (ms)        | 76                       | 78                     | +2                   |
| L2    | nodes              | 41                       | 127                    | +86 (3.1×)          |
| L2    | edges              | 45                       | 149                    | +104 (3.3×)         |
| L2    | crossings          | 0                        | 242                    | +242                 |
| L2    | size (pt)          | 1682 × 502               | 2933 × 1110            | 1.7× wide, 2.2× tall |
| L2    | layout (ms)        | 78                       | 98                     | +20                  |
| **L3**| **nodes**          | **44**                   | **158**                | **+114 (3.6×)**     |
| **L3**| **edges**          | **65**                   | **223**                | **+158 (3.4×)**     |
| **L3**| **crossings**      | **1 086**                | **139 990**            | **+138 904 (129×)** |
| **L3**| **size (pt)**      | **2517 × 827**           | **2071 × 2283**        | **2.3× area**       |
| **L3**| **layout (ms)**    | **87**                   | **116**                | **+29 (+33 %)**     |

**Read on L3:** 129× crossing explosion at heavy density confirms the legibility apocalypse the v13.1.1 audit warned about ("at scale, confirm: the template port/record nodes don't overflow"). Layout time stays sub-second per [operator lock 2026-06-04](../../PLAN.md) ("render time is not a constraint") — caching (CF.3.k) addresses any post-port-node growth, not the unfixed baseline.

**Note on baseline:** v13.1.1 audit reported "181 crossings, 2937pt height" at L3 on the demo L2. Our sasquatch_pr baseline measures 1086 crossings / 827pt height — likely a different fixture (the audit may have used spec_example or an earlier sasquatch revision). The relative density Δ heavy-vs-sasquatch is what the spike loop cares about, not absolute apples-to-the-audit numbers.

## Rendered output (operator cold-read)

Files at:
- `docs/audits/cf_3_diagram_spike/heavy_density_v1/{l1,l2,l3}.{svg,png,dot}` (+ `metrics.json`)
- `docs/audits/cf_3_diagram_spike/sasquatch_pr_baseline/{l1,l2,l3}.{svg,png,dot}` (+ `metrics.json`)

SVG = vector, infinite zoom, browser viewer for detail inspection. PNG = bitmap, inline-renderable in markdown viewers + GitHub, embedded below for at-a-glance comparison. DOT = the source graphviz layout text for reproducibility (`dot -Tsvg <file.dot>` re-renders independently).

### L1 — roles + control hierarchy

| sasquatch_pr (baseline) | heavy_density_v1 |
|---|---|
| ![sasquatch L1](cf_3_diagram_spike/sasquatch_pr_baseline/l1.png) | ![heavy L1](cf_3_diagram_spike/heavy_density_v1/l1.png) |

### L2 — adds rails + connectivity

| sasquatch_pr | heavy_density_v1 |
|---|---|
| ![sasquatch L2](cf_3_diagram_spike/sasquatch_pr_baseline/l2.png) | ![heavy L2](cf_3_diagram_spike/heavy_density_v1/l2.png) |

### L3 — full diagram (chains + templates) — **the critical comparison**

| sasquatch_pr | heavy_density_v1 |
|---|---|
| ![sasquatch L3](cf_3_diagram_spike/sasquatch_pr_baseline/l3.png) | ![heavy L3](cf_3_diagram_spike/heavy_density_v1/l3.png) |

The L3 pair is where every CF.3.a→.f sub-task lands. Open both at full resolution; the SVGs (`l3.svg` in each dir) let you zoom in further when the cold-read calls for it.

## Awaiting operator cold-read

Decision points before CF.3.a (the "ship-today 2-liner: `constraint=false` + `mclimit` 10.0") can land:

1. **Does `heavy_density_v1.yaml` stress the right shapes?**
   - Does L3 show enough template clusters with multi-leg + XOR shape to validate the "template-member edges shouldn't drive rank" hypothesis (audit's headline lever)?
   - Are there enough chain edges crossing template clusters to expose the chain-routing pain that CF.3.b's "template recast" + CF.3.f's "template-as-PORT-node" target?
   - Does the external-counterparty cluster look like a cluster, or just dispersed?
   - If any of these is "no", what FuzzPlan knob would shift the shape?

2. **Are the visual results worth pursuing?**
   - Looking at the heavy L3, can the operator imagine what the audit's "constraint=false" 65 % crossing-reduction would look like here? Or is the density so high that the win wouldn't matter to a human reader?
   - Is the current dot styling readable enough at heavy density that crossings-reduction is the right lever — or is the legibility blocker actually node size / label overlap / something layout-independent?

3. **Custom shapes — first iteration ideas?**
   - The audit's CF.3.f port-node prototype was rendered against the small demo L2. With heavy density now visible, what shape directions feel promising? (record nodes with leg ports? Hierarchical labels? Different shape per role kind?)

When the operator's read lands, I'll capture the verdict here and either re-spin the fuzz seed (knob tweak + regenerate `heavy_density_v1` or roll forward to `_v2`) or progress to CF.3.a measurement against this fixture.

## Continuous validation harness (CF.3.j — post-spike)

After the fixture locks, `cf3_spike.py render` becomes a permanent CI gate:
- run on every PR touching `src/recon_gen/common/l2/topology.py` (and the topology adjacency)
- assert L3 crossings + height + node/edge count do not regress vs the post-spike baseline
- crossings can decrease freely (CF.3.a etc. should drive it down); regressions block merge

Layout time is NOT a regression criterion (operator lock). CF.3.k caching absorbs any growth at operator-perceived layer.

## Audit history

- **2026-06-04 v0** — Harness shipped, heavy_density_v1.yaml generated (seed 42, 103 rails / 31 templates / 12 chains), v0 measurements above, operator cold-read pending.
