// X.4.b.2 — Studio diagram (spike arm A: D3 + d3-force).
//
// Renders the topology projection as a force-directed graph with
// per-kind Y-banding so the user's mental model reads visually:
//
//     Layer 3 (top):    TransferTemplates
//     Layer 2 (middle): Rails (every rail = one node, not edge labels)
//     Layer 1 (bottom): Roles (the institutional perimeter, "core")
//
// Edges:
//   - rail → role(s)      "rail_endpoint"   — connectivity
//   - template → rail     "template_member" — composition (one rail = one line)
//   - rail|template ↔     "chain"           — sequencing
//     rail|template
//   - role → role         "control_parent"  — structural roll-up
//
// Same chrome contract as arm B (CSS-class-driven hide / focus / mode);
// reads URL params (?focus=neighbors|subgraph) the same way.

/* global d3 */

const TOGGLE_KINDS = [
  "role-internal", "role-external", "rail", "template", "chain",
  "control_parent", "template_member",
];
const EDGE_LABEL_KINDS = [
  "rail_endpoint", "chain", "control_parent", "template_member",
];

// Y-banding — templates top, rails middle, roles bottom (foundation).
const Y_BAND = {
  template: 100,
  rail: 320,
  role: 540,
};
const Y_BAND_STRENGTH = 0.4;

// Per-kind sizing (drives forceCollide radius + visual sizing).
const NODE_RADIUS = {
  role: 38,
  rail: 22,
  template: 32,
};

function _readData() {
  const el = document.getElementById("topology-d3-data");
  if (!el) {
    console.error("studio/diagram_d3: missing #topology-d3-data");
    return { nodes: [], links: [] };
  }
  try {
    return JSON.parse(el.textContent || "{}");
  } catch (err) {
    console.error("studio/diagram_d3: bad JSON", err);
    return { nodes: [], links: [] };
  }
}

async function renderDiagram() {
  if (typeof d3 === "undefined") {
    console.error("studio/diagram_d3: d3 not loaded");
    const status = document.getElementById("diagram-status");
    if (status) status.textContent = "d3 missing — check <script> src";
    return;
  }

  const data = _readData();
  const nodes = data.nodes || [];
  const links = data.links || [];
  const target = document.getElementById("diagram-target");
  if (!target) {
    console.error("studio/diagram_d3: missing #diagram-target");
    return;
  }

  const status = document.getElementById("diagram-status");
  if (status) {
    status.textContent =
      `d3-force · ${nodes.length} nodes · ${links.length} edges`;
  }

  // Counts in the chrome (mirrors arm B).
  const counts = { role: 0, rail: 0, template: 0,
                   role_internal: 0, role_external: 0 };
  const edgeCounts = {
    rail_endpoint: 0, template_member: 0, chain: 0, control_parent: 0,
  };
  for (const n of nodes) {
    if (n.kind in counts) counts[n.kind] += 1;
    if (n.kind === "role" && n.scope === "internal") counts.role_internal += 1;
    if (n.kind === "role" && n.scope === "external") counts.role_external += 1;
  }
  for (const l of links) {
    if (l.kind in edgeCounts) edgeCounts[l.kind] += 1;
  }
  _setCount("count-role-internal", counts.role_internal);
  _setCount("count-role-external", counts.role_external);
  _setCount("count-rail", counts.rail);
  _setCount("count-template", counts.template);
  _setCount("count-chain", edgeCounts.chain);
  _setCount("count-control_parent", edgeCounts.control_parent);
  _setCount("count-template_member", edgeCounts.template_member);
  _setCount("count-rail_endpoint", edgeCounts.rail_endpoint);

  // Build SVG. We use d3.zoom so pan / wheel-zoom come "for free".
  const rect = target.getBoundingClientRect();
  const width = rect.width || 1400;
  const height = rect.height || 800;

  const svg = d3.select(target)
    .append("svg")
    .attr("class", "topology-d3-svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  // Marker defs for directional arrowheads (per edge kind for color).
  const defs = svg.append("defs");
  for (const kind of EDGE_LABEL_KINDS) {
    defs.append("marker")
      .attr("id", `arrow-${kind}`)
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 14)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .attr("class", `arrow-${kind}`)
      .append("path")
      .attr("d", "M0,-4L10,0L0,4");
  }

  // Zoomable group — all nodes/edges live inside this so pan + zoom apply.
  const zoomG = svg.append("g").attr("class", "zoom-root");
  const zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on("zoom", (event) => zoomG.attr("transform", event.transform));
  svg.call(zoom);

  // Edges first so they render under nodes.
  const link = zoomG.append("g")
    .attr("class", "links")
    .selectAll("line")
    .data(links)
    .join("line")
    .attr("class", (d) => `link link-${d.kind}`)
    .attr("data-kind", (d) => d.kind)
    .attr("data-source", (d) => (typeof d.source === "object" ? d.source.id : d.source))
    .attr("data-target", (d) => (typeof d.target === "object" ? d.target.id : d.target))
    .attr("marker-end", (d) => `url(#arrow-${d.kind})`);

  // Edge labels (small text, optional per kind via CSS).
  const linkLabels = zoomG.append("g")
    .attr("class", "link-labels")
    .selectAll("text")
    .data(links)
    .join("text")
    .attr("class", (d) => `link-label link-label-${d.kind}`)
    .attr("data-kind", (d) => d.kind)
    .attr("text-anchor", "middle")
    .text((d) => _linkLabelText(d));

  // Nodes — group per node so we can tag with data-attrs for CSS toggles.
  const node = zoomG.append("g")
    .attr("class", "nodes")
    .selectAll("g")
    .data(nodes)
    .join("g")
    .attr("class", (d) => `node node-${d.kind}`)
    .attr("data-kind", (d) => d.kind)
    .attr("data-id", (d) => d.id)
    .attr("data-scope", (d) => d.scope || null)
    .style("cursor", "pointer");

  // Per-kind shape + label.
  node.each(function (d) {
    const sel = d3.select(this);
    if (d.kind === "role") {
      sel.append("rect")
        .attr("class", "shape role-rect")
        .attr("rx", 6).attr("ry", 6)
        .attr("width", 110).attr("height", 36)
        .attr("x", -55).attr("y", -18);
      _labelLines(sel, d.label, 0, 0, 11);
    } else if (d.kind === "rail") {
      sel.append("rect")
        .attr("class", "shape rail-pill")
        .attr("rx", 12).attr("ry", 12)
        .attr("width", 100).attr("height", 24)
        .attr("x", -50).attr("y", -12);
      _labelLines(sel, d.label, 0, -1, 9);
    } else if (d.kind === "template") {
      sel.append("rect")
        .attr("class", "shape template-rect")
        .attr("rx", 4).attr("ry", 4)
        .attr("width", 130).attr("height", 36)
        .attr("x", -65).attr("y", -18);
      _labelLines(sel, d.label, 0, 0, 9);
    }
  });

  // Click-to-focus.
  const adjacency = _buildAdjacency(nodes, links);
  const focusMode = (new URLSearchParams(window.location.search))
    .get("focus") || "neighbors";
  const resetFocus = () => {
    svg.classed("focused", false);
    node.classed("dim", false).classed("focus", false);
    link.classed("dim", false).classed("focus", false);
    linkLabels.classed("dim", false).classed("focus", false);
  };
  node.on("click", function (event, d) {
    event.stopPropagation();
    const focused = new Set([d.id]);
    if (focusMode === "subgraph") {
      const queue = [d.id];
      while (queue.length > 0) {
        const cur = queue.shift();
        for (const nbr of (adjacency[cur] || [])) {
          if (!focused.has(nbr)) { focused.add(nbr); queue.push(nbr); }
        }
      }
    } else {
      for (const nbr of (adjacency[d.id] || [])) focused.add(nbr);
    }
    svg.classed("focused", true);
    node.each(function (n) {
      const inFocus = focused.has(n.id);
      d3.select(this)
        .classed("focus", inFocus)
        .classed("dim", !inFocus);
    });
    link.each(function (l) {
      const sId = typeof l.source === "object" ? l.source.id : l.source;
      const tId = typeof l.target === "object" ? l.target.id : l.target;
      const both = focused.has(sId) && focused.has(tId);
      d3.select(this).classed("focus", both).classed("dim", !both);
    });
    linkLabels.each(function (l) {
      const sId = typeof l.source === "object" ? l.source.id : l.source;
      const tId = typeof l.target === "object" ? l.target.id : l.target;
      const both = focused.has(sId) && focused.has(tId);
      d3.select(this).classed("focus", both).classed("dim", !both);
    });
  });
  svg.on("click", (e) => { if (e.target === svg.node()) resetFocus(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") resetFocus();
  });

  // Force simulation.
  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id)
      .distance((d) => {
        // Cross-band edges want longer links; intra-band shorter.
        if (d.kind === "rail_endpoint" || d.kind === "template_member") return 110;
        return 80;
      })
      .strength(0.35))
    .force("charge", d3.forceManyBody().strength(-260))
    .force("collide", d3.forceCollide()
      .radius((d) => (NODE_RADIUS[d.kind] || 30) + 4)
      .strength(0.9))
    .force("y", d3.forceY((d) => Y_BAND[d.kind] || height / 2)
      .strength(Y_BAND_STRENGTH))
    .force("x", d3.forceX(width / 2).strength(0.04))
    .on("tick", () => {});

  // Wire drag now that sim exists in closure.
  node.call(d3.drag()
    .on("start", (event, d) => {
      if (!event.active) sim.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on("end", (event, d) => {
      if (!event.active) sim.alphaTarget(0);
      d.fx = null; d.fy = null;
    }));

  // Re-attach the tick handler so render updates fire.
  sim.on("tick", () => {
      link
        .attr("x1", (d) => d.source.x)
        .attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x)
        .attr("y2", (d) => d.target.y);
      linkLabels
        .attr("x", (d) => (d.source.x + d.target.x) / 2)
        .attr("y", (d) => (d.source.y + d.target.y) / 2);
      node.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

  _wireToggles(svg);
  _wireEdgeLabelToggles(svg);
  _wireMode(svg);
  _wireLayer(svg);
  _wireReset(svg, resetFocus);
}

function _setCount(id, n) {
  const el = document.getElementById(id);
  if (el) el.textContent = `(${n})`;
}

function _linkLabelText(d) {
  if (d.kind === "chain") {
    let s = "chain";
    const parts = [];
    if (d.required) parts.push("required");
    if (d.xor_group) parts.push(`xor: ${d.xor_group}`);
    if (parts.length) s = `chain (${parts.join(", ")})`;
    return s;
  }
  if (d.kind === "control_parent") {
    return d.has_limits ? "controls ($ caps)" : "controls";
  }
  if (d.kind === "template_member") return "leg-rail";
  if (d.kind === "rail_endpoint") return d.endpoint || "";
  return "";
}

function _labelLines(sel, text, x, y, fontSize) {
  const lines = String(text).split("\n");
  const lineHeight = fontSize + 2;
  const startDy = -((lines.length - 1) * lineHeight) / 2;
  lines.forEach((line, i) => {
    sel.append("text")
      .attr("class", "node-label")
      .attr("text-anchor", "middle")
      .attr("x", x)
      .attr("y", y + startDy + i * lineHeight + fontSize / 3)
      .style("font-size", `${fontSize}px`)
      .text(line);
  });
}

function _buildAdjacency(nodes, links) {
  const adj = {};
  for (const n of nodes) adj[n.id] = new Set();
  for (const l of links) {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    if (adj[s]) adj[s].add(t);
    if (adj[t]) adj[t].add(s);
  }
  return adj;
}

function _wireToggles(svg) {
  for (const kind of TOGGLE_KINDS) {
    const cb = document.getElementById(`toggle-${kind}`);
    if (!cb) continue;
    const apply = () => svg.node().classList.toggle(`hide-${kind}`, !cb.checked);
    cb.addEventListener("change", apply);
    apply();
  }
}

function _wireEdgeLabelToggles(svg) {
  for (const kind of EDGE_LABEL_KINDS) {
    const cb = document.getElementById(`toggle-edge-label-${kind}`);
    if (!cb) continue;
    const apply = () => svg.node().classList.toggle(`hide-edge-label-${kind}`, !cb.checked);
    cb.addEventListener("change", apply);
    apply();
  }
}

function _wireMode(svg) {
  const sel = document.getElementById("mode-select");
  if (!sel) return;
  const apply = () => {
    for (const m of ["default", "coverage", "trainer"]) {
      svg.node().classList.remove(`mode-${m}`);
    }
    svg.node().classList.add(`mode-${sel.value}`);
  };
  sel.addEventListener("change", apply);
  apply();
}

function _wireLayer(svg) {
  const buttons = document.querySelectorAll(".layer-btn");
  if (buttons.length === 0) return;
  const setLayer = (n) => {
    for (const k of [1, 2, 3]) svg.node().classList.remove(`layer-${k}`);
    svg.node().classList.add(`layer-${n}`);
    for (const btn of buttons) {
      const b = parseInt(btn.getAttribute("data-layer"), 10);
      btn.classList.toggle("active", b === n);
    }
  };
  for (const btn of buttons) {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const layer = parseInt(btn.getAttribute("data-layer"), 10);
      if (layer >= 1 && layer <= 3) setLayer(layer);
    });
  }
  setLayer(3);
}

function _wireReset(svg, resetFocus) {
  const reset = document.getElementById("toggle-reset");
  if (!reset) return;
  reset.addEventListener("click", (e) => {
    e.preventDefault();
    for (const kind of TOGGLE_KINDS) {
      const cb = document.getElementById(`toggle-${kind}`);
      if (cb) {
        cb.checked = true;
        svg.node().classList.remove(`hide-${kind}`);
      }
    }
    for (const kind of EDGE_LABEL_KINDS) {
      const cb = document.getElementById(`toggle-edge-label-${kind}`);
      if (cb) {
        cb.checked = true;
        svg.node().classList.remove(`hide-edge-label-${kind}`);
      }
    }
    const modeSel = document.getElementById("mode-select");
    if (modeSel) {
      modeSel.value = "default";
      for (const m of ["default", "coverage", "trainer"]) {
        svg.node().classList.remove(`mode-${m}`);
      }
      svg.node().classList.add("mode-default");
    }
    for (const k of [1, 2, 3]) svg.node().classList.remove(`layer-${k}`);
    svg.node().classList.add("layer-3");
    for (const btn of document.querySelectorAll(".layer-btn")) {
      const b = parseInt(btn.getAttribute("data-layer"), 10);
      btn.classList.toggle("active", b === 3);
    }
    resetFocus();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderDiagram);
} else {
  renderDiagram();
}
