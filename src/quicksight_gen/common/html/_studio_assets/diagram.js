// X.4.b.3 — Studio diagram (spike arm B: enhanced graphviz).
//
// Renders the L2 topology DOT (inlined as a <template id="topology-dot">)
// to SVG via @hpcc-js/wasm-graphviz, then post-processes the SVG: every
// node + edge gets data-kind / data-id / data-source / data-target attrs
// derived from the graphviz <title> text (which carries our id-prefixed
// node names: role__X / rail__Y / tmpl__Z; edges: A->B). Role nodes also
// get data-scope merged from a sidecar JSON metadata block.
//
// Chrome dials (all CSS-class-driven on the SVG root — zero DOM mutation
// per interaction):
//
//   - Toggle visibility:    `hide-role-internal` / `hide-role-external` /
//                           `hide-rail` / `hide-template` / `hide-chain`
//   - Edge-label toggles:   `hide-edge-label-rail_bundle` /
//                           `hide-edge-label-self_loop` /
//                           `hide-edge-label-chain`
//   - Mode overlay:         `mode-default` / `mode-coverage` / `mode-trainer`
//   - Emphasize hierarchy:  `emphasize-role|rail|template|chain`
//   - Click-to-focus:       `focused` + per-element `dim` / `focus`
//
// URL-param knobs (refresh-to-apply):
//   - ?engine=dot|neato|sfdp|fdp|circo|twopi   (default: dot)
//   - ?focus=neighbors|subgraph                (default: neighbors)
//
// The renderer choice is the X.4.b spike question; ?engine= lets you
// flip layouts without restarting Studio.

const PREFIX_TO_KIND = {
  "role__": "role",
  "rail__": "rail",
  "tmpl__": "template",
};

const TOGGLE_KINDS = [
  "role-internal", "role-external", "rail", "template", "chain",
  "control_parent", "template_role",
];
const EDGE_LABEL_KINDS = [
  "rail_bundle", "self_loop", "chain", "control_parent", "template_role",
];

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

function _parseEdgeTitle(title) {
  const m = title.match(/^(.+?)->(.+)$/);
  if (!m) return null;
  return { source: m[1].trim(), target: m[2].trim() };
}

// Edge kind heuristic — the typed graph's edge kinds aren't carried in
// the graphviz title (graphviz only knows source->target). We classify
// from the title-extracted node-kinds + self-loop test.
function _edgeKind(srcId, dstId) {
  const srcKind = _kindFromTitle(srcId);
  const dstKind = _kindFromTitle(dstId);
  if (srcId === dstId) return "self_loop";
  if (srcKind === "role" && dstKind === "role") {
    // Could be rail_bundle or control_parent — both go role→role. Without
    // sidecar metadata to disambiguate (control_parent edges aren't
    // distinct in the graphviz title), default to rail_bundle. The post-
    // process below cross-references the typed graph's edge list to
    // overwrite this when needed.
    return "rail_bundle";
  }
  if (srcKind === "template" && dstKind === "rail") return "template_member";
  if (srcKind === "template" && dstKind === "role") return "template_role";
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

function _readSidecar() {
  const el = document.getElementById("topology-meta");
  if (!el) return { role_meta: {} };
  try {
    return JSON.parse(el.textContent || "{}");
  } catch (err) {
    console.error("studio/diagram: bad sidecar JSON", err);
    return { role_meta: {} };
  }
}

async function renderDiagram() {
  const dotTemplate = document.getElementById("topology-dot");
  const target = document.getElementById("diagram-target");
  if (!dotTemplate || !target) {
    console.error("studio/diagram: missing #topology-dot or #diagram-target");
    return;
  }
  const dot = dotTemplate.content.textContent.trim();
  const sidecar = _readSidecar();
  const roleMeta = sidecar.role_meta || {};

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
  svg.setAttribute("class", "topology-svg mode-default");

  // Annotate every node — kind from id prefix, scope from sidecar.
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
    if (kind === "role" && roleMeta[title]) {
      const meta = roleMeta[title];
      if (meta.scope) g.setAttribute('data-scope', meta.scope);
      if (meta.templated) g.setAttribute('data-templated', 'true');
    }
    if (kind in counts) counts[kind] += 1;
  }

  // Annotate every edge + build incidence + adjacency for focus mode.
  let edgeCounts = {
    rail_bundle: 0, self_loop: 0, template_member: 0, chain: 0,
  };
  const incidentEdges = {};
  const adjacency = {};
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

  // Mode-stub: paint per-node fake data so the user can see how
  // overlays would read. X.4.c.5 / X.4.c.6 replace these with real
  // coverage / trainer data fetchers.
  _applyModeStubs(svg);

  if (status) {
    const totalEdges = Object.values(edgeCounts).reduce((s, n) => s + n, 0);
    const nodes = counts.role + counts.rail + counts.template;
    status.textContent = `engine: ${engine} · ${nodes} nodes · ${totalEdges} edges`;
  }

  // Wire chrome interactivity now that the SVG is annotated.
  _wireToggles(svg);
  _wireEdgeLabelToggles(svg);
  _wireMode(svg);
  _wireLayer(svg);
  _wireFocus(svg, incidentEdges, adjacency);
  _wireActiveEngine(engine);
  _wirePanZoom(svg);
}

// X.4.b chrome iteration — mark which engine link is active so the user
// can see what they're on at a glance (default ``dot`` highlighted when
// no ``?engine=`` is present).
function _wireActiveEngine(engine) {
  for (const a of document.querySelectorAll(".engine-link")) {
    a.classList.toggle("active", a.getAttribute("data-engine") === engine);
  }
}

// Vanilla SVG pan + wheel zoom — no library. Operates on the SVG's
// viewBox, so transforms compose with everything else (focus dimming,
// layer hides, mode tints) without any extra coupling.
//
// Controls:
//   - Mouse wheel → zoom (centered on cursor)
//   - Mouse drag (left button) → pan
//   - Double-click on background → reset
//
// Click-to-focus on nodes is unaffected because we only consume drags
// (mousedown + move + up); a click without drag still fires through.
function _wirePanZoom(svg) {
  // Capture the original viewBox so reset has a target. wasm-graphviz
  // always emits a viewBox attr; if missing (defensive), synthesize from
  // width/height before they were stripped.
  let vb = svg.getAttribute("viewBox");
  if (!vb) {
    // Defensive: synthesize a 1000x1000 box; aspect ratio will look
    // off but at least pan/zoom won't crash.
    vb = "0 0 1000 1000";
    svg.setAttribute("viewBox", vb);
  }
  const [origX, origY, origW, origH] = vb.split(/\s+/).map(parseFloat);
  let cur = { x: origX, y: origY, w: origW, h: origH };
  const apply = () => {
    svg.setAttribute("viewBox", `${cur.x} ${cur.y} ${cur.w} ${cur.h}`);
  };
  const reset = () => {
    cur = { x: origX, y: origY, w: origW, h: origH };
    apply();
  };

  // Wheel zoom — centered on cursor position in SVG coords.
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    // Cursor position as a fraction of the viewport (0..1).
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    // Cursor in viewBox coordinates BEFORE zoom.
    const cx = cur.x + fx * cur.w;
    const cy = cur.y + fy * cur.h;
    // Zoom factor: wheel up (negative deltaY) zooms in.
    const factor = e.deltaY < 0 ? 0.85 : 1.18;
    const newW = Math.max(50, cur.w * factor);
    const newH = Math.max(50, cur.h * factor);
    // Re-anchor so the cursor stays over the same SVG point.
    cur = {
      x: cx - fx * newW,
      y: cy - fy * newH,
      w: newW,
      h: newH,
    };
    apply();
  }, { passive: false });

  // Drag pan — left button only. mousedown on the SVG (not on a node)
  // starts; mousemove updates; mouseup ends. We use a small drag-
  // threshold so a click-to-focus still fires on tiny mouse drift.
  let dragging = null;
  svg.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    dragging = {
      startClientX: e.clientX,
      startClientY: e.clientY,
      startVbX: cur.x,
      startVbY: cur.y,
      moved: false,
    };
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - dragging.startClientX;
    const dy = e.clientY - dragging.startClientY;
    if (!dragging.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    dragging.moved = true;
    const rect = svg.getBoundingClientRect();
    // Convert pixel delta → SVG coord delta via the current zoom level.
    const sx = cur.w / rect.width;
    const sy = cur.h / rect.height;
    cur.x = dragging.startVbX - dx * sx;
    cur.y = dragging.startVbY - dy * sy;
    apply();
    // Mid-drag, suppress text selection.
    e.preventDefault();
  });
  window.addEventListener("mouseup", (e) => {
    if (!dragging) return;
    if (dragging.moved) {
      // Stop the click-to-focus from firing after a real drag.
      e.stopPropagation();
    }
    dragging = null;
  }, true);  // capture phase so we run BEFORE click handlers

  // Double-click on background → reset zoom + pan.
  svg.addEventListener("dblclick", (e) => {
    if (e.target === svg) reset();
  });
}

function _applyModeStubs(svg) {
  // STUB: deterministically tag every other role node "covered" / "uncovered"
  // and every Nth rail "planted" — pure visual demo so the user can see
  // how mode overlays would read. Real data lands in X.4.c.5 / X.4.c.6.
  const nodes = svg.querySelectorAll('g.node');
  nodes.forEach((node, idx) => {
    const kind = node.getAttribute('data-kind');
    if (kind === 'role') {
      // 2 of every 3 covered, 1 uncovered — gives a visible mix.
      node.setAttribute(
        'data-coverage',
        idx % 3 === 0 ? 'uncovered' : 'covered',
      );
    } else if (kind === 'rail' && idx % 4 === 0) {
      // 1 in 4 rails carries a stub planted exception.
      node.setAttribute('data-planted', 'drift');
    }
  });
}

function _setHideClass(svg, kind, hidden) {
  svg.classList.toggle(`hide-${kind}`, hidden);
}

function _wireToggles(svg) {
  // Each .toggle-X checkbox toggles a `hide-X` class on the SVG root —
  // CSS hides matching nodes/edges.
  for (const kind of TOGGLE_KINDS) {
    const cb = document.getElementById(`toggle-${kind}`);
    if (!cb) continue;
    const apply = () => _setHideClass(svg, kind, !cb.checked);
    cb.addEventListener("change", apply);
    apply();  // initial sync
  }
  const reset = document.getElementById("toggle-reset");
  if (reset) {
    reset.addEventListener("click", (e) => {
      e.preventDefault();
      for (const kind of TOGGLE_KINDS) {
        const cb = document.getElementById(`toggle-${kind}`);
        if (cb) {
          cb.checked = true;
          svg.classList.remove(`hide-${kind}`);
        }
      }
      for (const kind of EDGE_LABEL_KINDS) {
        const cb = document.getElementById(`toggle-edge-label-${kind}`);
        if (cb) {
          cb.checked = true;
          svg.classList.remove(`hide-edge-label-${kind}`);
        }
      }
      const modeSel = document.getElementById("mode-select");
      if (modeSel) {
        modeSel.value = "default";
        for (const m of ["default", "coverage", "trainer"]) {
          svg.classList.remove(`mode-${m}`);
        }
        svg.classList.add("mode-default");
      }
      // Reset layer stepper to L3 (full).
      _setLayer(svg, 3);
      svg.classList.remove("focused");
      for (const el of svg.querySelectorAll('.dim, .focus')) {
        el.classList.remove('dim', 'focus');
      }
    });
  }
}

function _wireEdgeLabelToggles(svg) {
  for (const kind of EDGE_LABEL_KINDS) {
    const cb = document.getElementById(`toggle-edge-label-${kind}`);
    if (!cb) continue;
    const apply = () => svg.classList.toggle(`hide-edge-label-${kind}`, !cb.checked);
    cb.addEventListener("change", apply);
    apply();
  }
}

function _wireMode(svg) {
  const sel = document.getElementById("mode-select");
  if (!sel) return;
  const apply = () => {
    for (const m of ["default", "coverage", "trainer"]) {
      svg.classList.remove(`mode-${m}`);
    }
    svg.classList.add(`mode-${sel.value}`);
  };
  sel.addEventListener("change", apply);
  apply();
}

// X.4.b chrome iteration (per user feedback): conceptual layer stepper.
//   Layer 1 = Roles only (the chart of accounts).
//   Layer 2 = + Rails (the connectivity between roles).
//   Layer 3 = + Chains & Templates (composed-on-top concepts).
// Reads the user's mental model: roles core, rails connect, chains/templates
// layer on top — cumulative reveal builds comprehension layer-by-layer.
function _setLayer(svg, layer) {
  for (const n of [1, 2, 3]) svg.classList.remove(`layer-${n}`);
  svg.classList.add(`layer-${layer}`);
  for (const btn of document.querySelectorAll(".layer-btn")) {
    const n = parseInt(btn.getAttribute("data-layer"), 10);
    btn.classList.toggle("active", n === layer);
  }
}

function _wireLayer(svg) {
  const buttons = document.querySelectorAll(".layer-btn");
  if (buttons.length === 0) return;
  for (const btn of buttons) {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const layer = parseInt(btn.getAttribute("data-layer"), 10);
      if (layer >= 1 && layer <= 3) _setLayer(svg, layer);
    });
  }
  // Default: full (Layer 3) — all the user's existing toggle work
  // still composes on top.
  _setLayer(svg, 3);
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

  for (const node of svg.querySelectorAll('g.node')) {
    node.style.cursor = "pointer";
    node.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = node.getAttribute('data-id');
      if (!id) return;
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
      } else {
        for (const nbr of (adjacency[id] || [])) focused.add(nbr);
      }
      svg.classList.add("focused");
      for (const n of svg.querySelectorAll('g.node')) {
        const nid = n.getAttribute('data-id');
        n.classList.remove('dim', 'focus');
        if (focused.has(nid)) n.classList.add('focus');
        else n.classList.add('dim');
      }
      for (const eGroup of svg.querySelectorAll('g.edge')) {
        const src = eGroup.getAttribute('data-source');
        const dst = eGroup.getAttribute('data-target');
        eGroup.classList.remove('dim', 'focus');
        if (focused.has(src) && focused.has(dst)) eGroup.classList.add('focus');
        else eGroup.classList.add('dim');
      }
    });
  }

  svg.addEventListener("click", (e) => {
    if (e.target === svg) reset();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") reset();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderDiagram);
} else {
  renderDiagram();
}
