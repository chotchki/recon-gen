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

// Per-kind sizing (drives forceCollide radius + visual sizing).
const NODE_RADIUS = {
  role: 38,
  rail: 22,
  template: 32,
};

// Tunable knobs — each is a (default, min, max, step) range plus a
// URL-param name. _readKnobs() reads URL overrides; _wireKnobs() binds
// sliders + logs every change to /log so the user can copy good configs
// out of the studio process stderr (dev-log forwarder).
//
// Per-kind knobs (X.4.b.2 iteration): Y / repulsion / collide are now
// per node-kind; link distance per edge-kind; X-center stays global.
// Defaults are the same across kinds (= prior global default); user
// twiddles per-kind to fine-tune (e.g. templates need more repulsion
// since their labels are longer).
const KNOBS = {
  // Per-node-kind Y-pull. Positive = toward Y-band home; negative =
  // pushed AWAY from it. Roles split by role_subkind ("parent" =
  // control accounts, "child" = subledger accounts that point at a
  // parent_role, "standalone" = neither — uses the bare ``role`` knob).
  y_strength_role:          { def: 0.15, min: -1.0, max: 1.0, step: 0.05 },
  y_strength_role_parent:   { def: 0.15, min: -1.0, max: 1.0, step: 0.05 },
  y_strength_role_child:    { def: 0.15, min: -1.0, max: 1.0, step: 0.05 },
  y_strength_rail:          { def: 0.15, min: -1.0, max: 1.0, step: 0.05 },
  y_strength_template:      { def: 0.15, min: -1.0, max: 1.0, step: 0.05 },
  // Per-node-kind X-pull. Positive = toward canvas center; negative =
  // pushed away from center (toward the edges).
  x_strength_role:          { def: 0.04, min: -1.0, max: 1.0, step: 0.02 },
  x_strength_role_parent:   { def: 0.04, min: -1.0, max: 1.0, step: 0.02 },
  x_strength_role_child:    { def: 0.04, min: -1.0, max: 1.0, step: 0.02 },
  x_strength_rail:          { def: 0.04, min: -1.0, max: 1.0, step: 0.02 },
  x_strength_template:      { def: 0.04, min: -1.0, max: 1.0, step: 0.02 },
  // Per-node-kind repulsion (negative; more negative = harder push).
  charge_role:          { def: -450, min: -1500, max: -50, step: 10 },
  charge_role_parent:   { def: -450, min: -1500, max: -50, step: 10 },
  charge_role_child:    { def: -450, min: -1500, max: -50, step: 10 },
  charge_rail:          { def: -450, min: -1500, max: -50, step: 10 },
  charge_template:      { def: -450, min: -1500, max: -50, step: 10 },
  // Per-node-kind collide padding (extra px around the base radius).
  collide_role:          { def: 14, min: 2, max: 60, step: 1 },
  collide_role_parent:   { def: 14, min: 2, max: 60, step: 1 },
  collide_role_child:    { def: 14, min: 2, max: 60, step: 1 },
  collide_rail:          { def: 14, min: 2, max: 60, step: 1 },
  collide_template:      { def: 14, min: 2, max: 60, step: 1 },
  // Per-edge-kind link length BOUNDS (custom force enforces both).
  // The d3.forceLink.distance is set to (min+max)/2 with weak (0.1)
  // strength as a soft attractor toward the midpoint; the custom
  // boundedLinkForce runs per tick to push apart edges that fell
  // below ``min`` and pull together edges that grew past ``max``.
  link_min_rail_endpoint:   { def: 60,  min: 10, max: 400, step: 5 },
  link_max_rail_endpoint:   { def: 200, min: 10, max: 400, step: 5 },
  link_min_template_member: { def: 60,  min: 10, max: 400, step: 5 },
  link_max_template_member: { def: 200, min: 10, max: 400, step: 5 },
  link_min_chain:           { def: 40,  min: 10, max: 400, step: 5 },
  link_max_chain:           { def: 140, min: 10, max: 400, step: 5 },
  link_min_control_parent:  { def: 30,  min: 10, max: 400, step: 5 },
  link_max_control_parent:  { def: 130, min: 10, max: 400, step: 5 },
};

function _readKnobs() {
  const params = new URLSearchParams(window.location.search);
  const out = {};
  for (const [name, spec] of Object.entries(KNOBS)) {
    const raw = params.get(name);
    if (raw !== null) {
      const parsed = parseFloat(raw);
      out[name] = Number.isFinite(parsed) ? parsed : spec.def;
    } else {
      out[name] = spec.def;
    }
  }
  return out;
}

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
    .attr("data-rail-subtype", (d) => d.rail_subtype || null)
    .style("cursor", "pointer");

  // Per-kind shape + label, sized to fit content (X.4.b.2 iteration).
  // Render order: text first, measure with getBBox, then insert rect
  // BEFORE the text (insert puts it earlier in DOM order = renders
  // underneath) sized to wrap the measured text + per-kind padding.
  // The measured radius is stashed on `d` so forceCollide picks up the
  // real footprint instead of guessing from NODE_RADIUS constants.
  node.each(function (d) {
    const sel = d3.select(this);
    let fontSize, padX, padY, shapeClass, rx;
    if (d.kind === "role") {
      fontSize = 11; padX = 12; padY = 8;
      shapeClass = "role-rect"; rx = 6;
    } else if (d.kind === "rail") {
      fontSize = 9; padX = 10; padY = 6;
      shapeClass = "rail-pill"; rx = 12;
    } else if (d.kind === "template") {
      fontSize = 9; padX = 12; padY = 8;
      shapeClass = "template-rect"; rx = 4;
    } else {
      fontSize = 10; padX = 10; padY = 6;
      shapeClass = "rail-pill"; rx = 6;
    }
    _labelLines(sel, d.label, 0, 0, fontSize);
    // Measure the text we just laid down.
    let bbox;
    try {
      bbox = sel.node().getBBox();
    } catch {
      bbox = { width: 80, height: 24 };
    }
    const w = Math.max(bbox.width + 2 * padX, 40);
    const h = Math.max(bbox.height + 2 * padY, 20);
    // Stash for forceCollide. Use diagonal half-length as a tight
    // bounding circle (better than max(w,h)/2 for wide-and-short or
    // tall-and-narrow shapes).
    d.measuredRadius = 0.5 * Math.sqrt(w * w + h * h);
    sel.insert("rect", "text")
      .attr("class", `shape ${shapeClass}`)
      .attr("rx", rx).attr("ry", rx)
      .attr("width", w).attr("height", h)
      .attr("x", -w / 2).attr("y", -h / 2);
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

  // Force simulation — current knob values from URL or defaults.
  // Per-kind force accessors close over ``knobs`` so re-binding picks
  // up the latest slider values when the user iterates.
  // ``forceLink`` is kept at low (0.1) strength as a soft midpoint
  // attractor + the canonical mechanism for resolving link.source /
  // link.target from string IDs to node refs; the custom
  // ``boundedLinkForce`` (added below) enforces the per-kind
  // ``[min, max]`` bounds each tick.
  const knobs = _readKnobs();
  // Boolean state for the viewport-clamp checkbox; closed over by the
  // viewport force below so toggling fires immediately on next tick
  // without re-binding.
  const viewportState = { clamp: true };
  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id).strength(0.1))
    .force("link_bounds", _boundedLinkForce(links, knobs))
    .force("charge", d3.forceManyBody())
    .force("collide", d3.forceCollide().strength(0.95))
    .force("y", d3.forceY((d) => Y_BAND[d.kind] || height / 2))
    .force("x", d3.forceX(width / 2))
    .force("viewport", _viewportClampForce(nodes, width, height, viewportState))
    .on("tick", () => {});
  _bindForces(sim, knobs);
  _wireViewportClamp(viewportState, sim);

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
  _wireKnobs(sim, knobs);
  _wireBandHints(target);
  _wireBundleToggle();
}

function _bindForces(sim, knobs) {
  // (Re-)bind every force accessor with closures that read the LATEST
  // knob values. d3 caches per-node force results until you call the
  // accessor again, so we re-call on every knob change.
  sim.force("y").strength((d) => {
    if (d.kind === "role") return _roleKnob(knobs, "y_strength", d);
    if (d.kind === "rail") return knobs.y_strength_rail;
    if (d.kind === "template") return knobs.y_strength_template;
    return 0.15;
  });
  sim.force("charge").strength((d) => {
    if (d.kind === "role") return _roleKnob(knobs, "charge", d);
    if (d.kind === "rail") return knobs.charge_rail;
    if (d.kind === "template") return knobs.charge_template;
    return -260;
  });
  sim.force("collide").radius((d) => {
    // Prefer the actual rendered footprint (set by node.each above)
    // over the NODE_RADIUS fallback so size-to-fit nodes don't overlap.
    const base = d.measuredRadius || NODE_RADIUS[d.kind] || 30;
    if (d.kind === "role") return base + _roleKnob(knobs, "collide", d);
    if (d.kind === "rail") return base + knobs.collide_rail;
    if (d.kind === "template") return base + knobs.collide_template;
    return base + 14;
  });
  // forceLink target = (min+max)/2 — the soft pull's "preferred" length.
  // Bounds enforcement happens in boundedLinkForce per tick.
  sim.force("link").distance((d) => {
    const [lo, hi] = _linkBounds(d.kind, knobs);
    return (lo + hi) / 2;
  });
  sim.force("x").strength((d) => {
    if (d.kind === "role") return _roleKnob(knobs, "x_strength", d);
    if (d.kind === "rail") return knobs.x_strength_rail;
    if (d.kind === "template") return knobs.x_strength_template;
    return 0.04;
  });
}

// Resolve a per-role-subkind knob: parent / child / standalone fall
// back to the base "role" knob if the sub-suffix isn't defined (defensive).
function _roleKnob(knobs, prefix, d) {
  const sub = d.role_subkind;
  if (sub === "parent") {
    const v = knobs[`${prefix}_role_parent`];
    if (v !== undefined) return v;
  } else if (sub === "child") {
    const v = knobs[`${prefix}_role_child`];
    if (v !== undefined) return v;
  }
  return knobs[`${prefix}_role`];
}

function _linkBounds(kind, knobs) {
  if (kind === "rail_endpoint") {
    return [knobs.link_min_rail_endpoint, knobs.link_max_rail_endpoint];
  }
  if (kind === "template_member") {
    return [knobs.link_min_template_member, knobs.link_max_template_member];
  }
  if (kind === "chain") {
    return [knobs.link_min_chain, knobs.link_max_chain];
  }
  if (kind === "control_parent") {
    return [knobs.link_min_control_parent, knobs.link_max_control_parent];
  }
  return [50, 150];
}

// Custom d3-force: enforce per-link [min, max] length bounds. Inside
// the bounds = no force; outside = velocity nudge toward / away from the
// other endpoint. Uses ``vx``/``vy`` (Verlet velocity) per d3-force
// convention, so it composes cleanly with charge / collide / x / y.
function _boundedLinkForce(linksArg, knobsRef) {
  function force(alpha) {
    const k = 0.7;  // bound-enforcement strength scalar
    for (const link of linksArg) {
      // d3.forceLink resolves source/target from id-strings to nodes
      // during its own initialize phase; if it hasn't run yet (first
      // tick race), skip — next tick will catch up.
      if (typeof link.source !== "object" || typeof link.target !== "object") {
        continue;
      }
      const [lo, hi] = _linkBounds(link.kind, knobsRef);
      const dx = link.target.x - link.source.x;
      const dy = link.target.y - link.source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1e-6;
      let direction = 0;
      let amount = 0;
      if (dist > hi) {
        // Pull together: source += (target-source) * frac, target -= same.
        amount = (dist - hi) / dist;
        direction = +1;
      } else if (dist < lo) {
        // Push apart: source -= (target-source) * frac, target += same.
        amount = (lo - dist) / dist;
        direction = -1;
      } else {
        continue;
      }
      const dvx = dx * amount * k * alpha * direction;
      const dvy = dy * amount * k * alpha * direction;
      // Half-half on the velocity nudge; pinned endpoints (fx/fy set
      // during drag) don't accept velocity changes.
      if (link.source.fx == null) {
        link.source.vx = (link.source.vx || 0) + dvx / 2;
        link.source.vy = (link.source.vy || 0) + dvy / 2;
      }
      if (link.target.fx == null) {
        link.target.vx = (link.target.vx || 0) - dvx / 2;
        link.target.vy = (link.target.vy || 0) - dvy / 2;
      }
    }
  }
  // d3.forceSimulation calls .initialize on each force with the node
  // array; we don't need it (linksArg is closed over) but the API
  // expects the property to be defined.
  force.initialize = function () {};
  return force;
}

function _wireKnobs(sim, knobs) {
  // Bind each slider; on input, update knobs, re-bind force accessors,
  // restart the alpha (gentle), and log the new value + the FULL knob
  // set to /log so the user can grep their dev-log to find a good config.
  const apply = (name, value) => {
    knobs[name] = value;
    _bindForces(sim, knobs);
    sim.alpha(0.5).restart();
    _logKnobs(knobs, name, value);
    _updateUrlForKnobs(knobs);
  };
  for (const [name, _spec] of Object.entries(KNOBS)) {
    const input = document.getElementById(`knob-${name}`);
    const display = document.getElementById(`knob-${name}-value`);
    if (!input) continue;
    input.value = String(knobs[name]);
    if (display) display.textContent = String(knobs[name]);
    input.addEventListener("input", () => {
      const v = parseFloat(input.value);
      if (display) display.textContent = String(v);
      apply(name, v);
    });
  }
}

function _logKnobs(knobs, changedName, changedValue) {
  // Use the dev-log forwarder if available; otherwise a console.log line
  // (which the dev-log shim ALSO captures via its console.* hook).
  console.log(
    `studio/diagram_d3 knob ${changedName}=${changedValue}`,
    Object.assign({}, knobs),
  );
}

function _updateUrlForKnobs(knobs) {
  // Reflect the current knob set in the URL so the user can bookmark /
  // share a tuning. ``replaceState`` so we don't pollute history.
  const params = new URLSearchParams(window.location.search);
  for (const [name, value] of Object.entries(knobs)) {
    params.set(name, String(value));
  }
  const newUrl = `${window.location.pathname}?${params.toString()}`;
  window.history.replaceState({}, "", newUrl);
}

function _wireBandHints(target) {
  const cb = document.getElementById("toggle-band-hints");
  if (!cb) return;
  const apply = () => target.classList.toggle("show-bands", cb.checked);
  cb.addEventListener("change", apply);
  apply();
}

function _wireViewportClamp(state, sim) {
  const cb = document.getElementById("toggle-viewport-clamp");
  if (!cb) return;
  state.clamp = cb.checked;
  cb.addEventListener("change", () => {
    state.clamp = cb.checked;
    // Nudge the simulation so a flip from off→on snaps overflowing
    // nodes back inside the viewport on the very next tick.
    sim.alpha(0.3).restart();
  });
}

// Hard viewport clamp — runs each tick after the other forces have
// updated velocities. Any node that would leave the [0, width] x
// [0, height] viewBox box gets its position pinned to the boundary
// (with the node's own measured radius as inset so it doesn't half-
// disappear). Outward velocity is zeroed so the simulation doesn't
// waste energy trying to push a clamped node further out.
//
// Pinned endpoints (``fx`` / ``fy`` set during drag) bypass the clamp
// so the user can drag a node to wherever they want.
function _viewportClampForce(nodes, width, height, state) {
  function force() {
    if (!state.clamp) return;
    for (const node of nodes) {
      if (node.fx != null) continue;
      const r = (node.measuredRadius || 30) + 4;
      if (node.x < r) {
        node.x = r;
        if (node.vx < 0) node.vx = 0;
      } else if (node.x > width - r) {
        node.x = width - r;
        if (node.vx > 0) node.vx = 0;
      }
      if (node.y < r) {
        node.y = r;
        if (node.vy < 0) node.vy = 0;
      } else if (node.y > height - r) {
        node.y = height - r;
        if (node.vy > 0) node.vy = 0;
      }
    }
  }
  force.initialize = function () {};
  return force;
}

function _wireBundleToggle() {
  // Bundling is server-side (different JSON shape), so a flip needs
  // a page reload with ?bundle=on|off in the URL.
  const cb = document.getElementById("toggle-bundle");
  if (!cb) return;
  cb.addEventListener("change", () => {
    const params = new URLSearchParams(window.location.search);
    params.set("bundle", cb.checked ? "on" : "off");
    window.location.search = params.toString();
  });
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
