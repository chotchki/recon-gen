// X.4.b.3 — Studio diagram (spike arm B: enhanced graphviz).
//
// Renders the L2 topology DOT (inlined as a <template id="topology-dot">)
// to SVG via @hpcc-js/wasm-graphviz, then post-processes the SVG: every
// node + edge gets data-kind / data-id / data-source / data-target attrs
// derived from the graphviz <title> text (which carries our id-prefixed
// node names: role__X / rail__Y / tmpl__Z; edges: A->B). The chrome
// checkboxes + click-to-focus drive these data-attrs via CSS classes
// applied to the SVG root — no DOM mutation per interaction, just
// data-* state flags so behavior is dirt-cheap.
//
// Knobs (URL query params, hot-reload friendly — refresh to apply):
//   - ?engine=dot|neato|sfdp|fdp|circo|twopi   (default: dot)
//   - ?focus=neighbors|subgraph                (default: neighbors)
//   - ?show-edge-labels=true|false             (default: true)
//
// The renderer choice is the X.4.b spike question; ?engine= lets you
// flip layouts without restarting Studio.

const PREFIX_TO_KIND = {
  "role__": "role",
  "rail__": "rail",
  "tmpl__": "template",
};

// Strip the typed prefix off a graphviz title to recover the L2 id.
function _idFromTitle(title) {
  for (const [prefix, _kind] of Object.entries(PREFIX_TO_KIND)) {
    if (title.startsWith(prefix)) return title.slice(prefix.length);
  }
  return title;
}

function _kindFromTitle(title) {
  for (const [prefix, kind] of Object.entries(PREFIX_TO_KIND)) {
    if (title.startsWith(prefix)) return kind;
  }
  return "unknown";
}

// Parse "role__A->role__B" into { source, target }.
function _parseEdgeTitle(title) {
  const m = title.match(/^(.+?)->(.+)$/);
  if (!m) return null;
  return { source: m[1].trim(), target: m[2].trim() };
}

// Edge kind heuristic: the typed graph's edge kinds aren't carried in
// the graphviz title (graphviz only knows source->target). We classify
// from the title-extracted kinds + self-loop test:
//   - source.kind === target.kind === "role" + same id → self_loop
//   - source.kind === target.kind === "role" → rail_bundle
//   - source.kind === "template" + target.kind === "rail" → template_member
//   - source.kind === "rail" + target.kind === "rail|template" → chain
//   - source.kind === "template" + target.kind === "template" → chain
function _edgeKind(srcId, dstId) {
  const srcKind = _kindFromTitle(srcId);
  const dstKind = _kindFromTitle(dstId);
  if (srcId === dstId) return "self_loop";
  if (srcKind === "role" && dstKind === "role") return "rail_bundle";
  if (srcKind === "template" && dstKind === "rail") return "template_member";
  return "chain";
}

let _rendererPromise = null;
function _getRenderer() {
  if (_rendererPromise === null) {
    _rendererPromise = (async () => {
      // Loaded from the docs-shared vendored bundle (mounted at
      // /studio/wasm-graphviz/index.js by the Studio routes — no
      // duplicate copy under assets/vendor/ for the spike phase;
      // production vendoring decision lives at X.4.c.1).
      const mod = await import("/studio/wasm-graphviz/index.js");
      return await mod.Graphviz.load();
    })();
  }
  return _rendererPromise;
}

async function renderDiagram() {
  const dotTemplate = document.getElementById("topology-dot");
  const target = document.getElementById("diagram-target");
  if (!dotTemplate || !target) {
    console.error("studio/diagram: missing #topology-dot or #diagram-target");
    return;
  }
  const dot = dotTemplate.content.textContent.trim();

  // Pull engine from query string; fall back to dot.
  const params = new URLSearchParams(window.location.search);
  const engine = params.get("engine") || "dot";

  const status = document.getElementById("diagram-status");
  if (status) status.textContent = `rendering (engine: ${engine})…`;

  let renderer;
  try {
    renderer = await _getRenderer();
  } catch (err) {
    console.error("studio/diagram: wasm-graphviz load failed", err);
    if (status) status.textContent = "renderer load failed; see console";
    return;
  }

  let svgText;
  try {
    svgText = renderer.layout(dot, "svg", engine);
  } catch (err) {
    console.error("studio/diagram: layout failed", err);
    if (status) {
      status.textContent =
        `layout failed (engine: ${engine}; try ?engine=neato or sfdp); see console`;
    }
    return;
  }

  target.innerHTML = svgText;
  const svg = target.querySelector("svg");
  if (!svg) {
    if (status) status.textContent = "no <svg> in render output";
    return;
  }

  // Strip the wasm-graphviz default sizing so the SVG fills the viewport.
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  svg.setAttribute("class", "topology-svg");

  // Annotate every node with data-kind / data-id derived from the
  // graphviz <title> text (our prefix-discriminated id IS the kind).
  let counts = { role: 0, rail: 0, template: 0 };
  for (const g of svg.querySelectorAll('g.node')) {
    const titleEl = g.querySelector('title');
    if (!titleEl) continue;
    const title = titleEl.textContent.trim();
    const kind = _kindFromTitle(title);
    const id = _idFromTitle(title);
    g.setAttribute('data-kind', kind);
    g.setAttribute('data-id', title);  // full id (with prefix) for edge lookups
    g.setAttribute('data-display-id', id);  // unprefixed (for tooltips)
    if (kind in counts) counts[kind] += 1;
  }

  // Annotate every edge with data-source / data-target / data-kind +
  // build a node→incident-edges map for fast focus-mode dimming.
  let edgeCounts = {
    rail_bundle: 0, self_loop: 0, template_member: 0, chain: 0,
  };
  const incidentEdges = {};  // nodeFullId → Set<edgeElement>
  const adjacency = {};      // nodeFullId → Set<nodeFullId>
  for (const g of svg.querySelectorAll('g.edge')) {
    const titleEl = g.querySelector('title');
    if (!titleEl) continue;
    const parsed = _parseEdgeTitle(titleEl.textContent.trim());
    if (!parsed) continue;
    const kind = _edgeKind(parsed.source, parsed.target);
    g.setAttribute('data-source', parsed.source);
    g.setAttribute('data-target', parsed.target);
    g.setAttribute('data-kind', kind);
    if (kind in edgeCounts) edgeCounts[kind] += 1;
    for (const id of [parsed.source, parsed.target]) {
      if (!(id in incidentEdges)) incidentEdges[id] = new Set();
      incidentEdges[id].add(g);
      if (!(id in adjacency)) adjacency[id] = new Set();
    }
    adjacency[parsed.source].add(parsed.target);
    adjacency[parsed.target].add(parsed.source);
  }

  // Update the count badges in the chrome.
  const updateCount = (id, n) => {
    const el = document.getElementById(id);
    if (el) el.textContent = `(${n})`;
  };
  updateCount("count-role", counts.role);
  updateCount("count-rail", counts.rail);
  updateCount("count-template", counts.template);
  const totalEdges = Object.values(edgeCounts).reduce((s, n) => s + n, 0);
  updateCount("count-chain", edgeCounts.chain);
  if (status) {
    status.textContent =
      `engine: ${engine} · ${counts.role + counts.rail + counts.template} ` +
      `nodes · ${totalEdges} edges`;
  }

  // Wire chrome interactivity now that the SVG is annotated.
  _wireToggles(svg);
  _wireFocus(svg, incidentEdges, adjacency);
  _wireEdgeLabels(svg, params);
}

function _wireToggles(svg) {
  // Each .toggle-kind checkbox toggles a class on the SVG root —
  // CSS hides nodes/edges with matching data-kind when the class is set.
  const kinds = ["role", "rail", "template", "chain"];
  for (const kind of kinds) {
    const cb = document.getElementById(`toggle-${kind}`);
    if (!cb) continue;
    const apply = () => {
      svg.classList.toggle(`hide-${kind}`, !cb.checked);
    };
    cb.addEventListener("change", apply);
    apply();  // initial sync (default checked)
  }
  const reset = document.getElementById("toggle-reset");
  if (reset) {
    reset.addEventListener("click", (e) => {
      e.preventDefault();
      for (const kind of kinds) {
        const cb = document.getElementById(`toggle-${kind}`);
        if (cb) {
          cb.checked = true;
          svg.classList.remove(`hide-${kind}`);
        }
      }
      svg.classList.remove("focused");
      for (const el of svg.querySelectorAll('.dim, .focus')) {
        el.classList.remove('dim', 'focus');
      }
    });
  }
}

function _wireFocus(svg, incidentEdges, adjacency) {
  const params = new URLSearchParams(window.location.search);
  const focusMode = params.get("focus") || "neighbors";

  const reset = () => {
    svg.classList.remove("focused");
    for (const el of svg.querySelectorAll('.dim, .focus')) {
      el.classList.remove('dim', 'focus');
    }
  };

  // Click on a node → focus.
  for (const node of svg.querySelectorAll('g.node')) {
    node.style.cursor = "pointer";
    node.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = node.getAttribute('data-id');
      if (!id) return;

      // Compute focused id set: clicked node + its neighbors (1-hop)
      // OR full transitive subgraph, depending on ?focus= mode.
      const focused = new Set([id]);
      if (focusMode === "subgraph") {
        const queue = [id];
        while (queue.length > 0) {
          const cur = queue.shift();
          for (const nbr of (adjacency[cur] || [])) {
            if (!focused.has(nbr)) {
              focused.add(nbr);
              queue.push(nbr);
            }
          }
        }
      } else {  // neighbors (default)
        for (const nbr of (adjacency[id] || [])) focused.add(nbr);
      }

      svg.classList.add("focused");

      // Apply dim/focus classes.
      for (const n of svg.querySelectorAll('g.node')) {
        const nid = n.getAttribute('data-id');
        n.classList.remove('dim', 'focus');
        if (focused.has(nid)) {
          n.classList.add('focus');
        } else {
          n.classList.add('dim');
        }
      }
      for (const eGroup of svg.querySelectorAll('g.edge')) {
        const src = eGroup.getAttribute('data-source');
        const dst = eGroup.getAttribute('data-target');
        eGroup.classList.remove('dim', 'focus');
        if (focused.has(src) && focused.has(dst)) {
          eGroup.classList.add('focus');
        } else {
          eGroup.classList.add('dim');
        }
      }
    });
  }

  // Click on empty SVG (background) → reset.
  svg.addEventListener("click", (e) => {
    if (e.target === svg) reset();
  });

  // Esc key → reset focus.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") reset();
  });
}

function _wireEdgeLabels(svg, params) {
  // Edge labels are noisy on dense graphs; offer a toggle.
  const showLabels = (params.get("show-edge-labels") || "true") !== "false";
  if (!showLabels) {
    svg.classList.add("hide-edge-labels");
  }
}

// Bootstrap: render once page DOM is ready.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderDiagram);
} else {
  renderDiagram();
}
