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
  "control_parent",
];
const EDGE_LABEL_KINDS = [
  "rail_bundle", "self_loop", "chain", "control_parent",
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

  // Layout engine is locked to dot — the per-rail rails-as-nodes model
  // depends on dot's rank algorithm. Other engines (neato/sfdp/etc.)
  // don't handle clusters or directed-rank semantics the way the
  // chosen layout needs.
  const status = document.getElementById("diagram-status");
  if (status) status.textContent = "rendering…";

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
    svgText = renderer.layout(dot, "svg", "dot");
  } catch (err) {
    console.error("studio/diagram: layout failed", err);
    if (status) status.textContent = "layout failed; see console";
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

  // Annotate every edge for visibility-toggle CSS. Adjacency is no
  // longer built client-side — focus mode is server-rendered now
  // (X.4.b focus, 2026-05-13).
  let edgeCounts = {
    rail_bundle: 0, self_loop: 0, template_member: 0, chain: 0,
  };
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
  }

  if (status) {
    const totalEdges = Object.values(edgeCounts).reduce((s, n) => s + n, 0);
    const nodes = counts.role + counts.rail + counts.template;
    status.textContent = `${nodes} nodes · ${totalEdges} edges`;
  }

  // Wire chrome interactivity now that the SVG is annotated.
  _wireToggles(svg);
  _wireEdgeLabelToggles(svg);
  _wireFocus(svg);
  _wirePanZoom(svg);
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
  // Reset button is now a plain anchor (href="?") that navigates to
  // the bare /diagram with no params. The browser handles it; nothing
  // for JS to do here.
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

// Layer stepper is server-rendered — the chrome's `<a class="layer-btn">`
// links carry `?layer=N` in href, server filters the topology emit and
// dot re-lays out the smaller subset cleanly. No JS layer wiring needed
// (the click is just a navigation).

// Focus mode: click a node → navigate to ?focus=<id> so the server
// re-emits a filtered DOT (focus + 1-hop) and dot re-lays out the
// smaller subgraph cleanly. Click on empty SVG canvas → drop ?focus
// to restore the full picture. Escape clears focus too.
//
// Why navigation instead of CSS dim: dimming kept the original layout
// (node positions frozen, just opacity-faded). The user wanted "zoom
// in" semantics — re-render so the focused subset gets dot's full
// canvas. Server-side filter keeps the implementation small (no DOT
// rewriting on the JS side).
function _wireFocus(svg) {
  const _navigateToFocus = (focusId) => {
    // Preserve ?layer= so click-to-focus doesn't reset the user's
    // chosen layer. Only ?focus= changes.
    const url = new URL(window.location.href);
    if (focusId) {
      url.searchParams.set("focus", focusId);
    } else {
      url.searchParams.delete("focus");
    }
    window.location.href = url.toString();
  };

  for (const node of svg.querySelectorAll('g.node')) {
    node.style.cursor = "pointer";
    node.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = node.getAttribute('data-id');
      if (id) _navigateToFocus(id);
    });
  }

  // Click on empty SVG (background) clears focus. e.target === svg
  // means the click landed on the SVG root, not on a child element.
  svg.addEventListener("click", (e) => {
    if (e.target === svg) _navigateToFocus(null);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") _navigateToFocus(null);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderDiagram);
} else {
  renderDiagram();
}
