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

// CF.3.d — `rail` / `template` / `chain` / `control_parent` are now
// server-emit toggles (URL `?show=...`), not CSS hide. Only the
// internal/external role scope split remains a CSS-toggle pair (v0
// scope cut — server-side scope split is a smaller win, queued for
// v0.1). The `if (!cb) continue;` guard in `_wireToggles` already
// handles missing IDs gracefully, so dropping the four entries here
// is intent-documentation, not a functional change.
const TOGGLE_KINDS = [
  "role-internal", "role-external",
];
const EDGE_LABEL_KINDS = [
  "rail_bundle", "self_loop", "chain", "control_parent",
];

// CF.3.f — port-suffixed edge endpoints. When a chain/rail edge docks
// at an HTML-table cell PORT (e.g. `tmpl__T:leg_R:e` source or
// `tmpl__T2:w` target), graphviz emits the title with the port name
// dropped but the compass anchor (`:e`/`:w`/`:n`/`:s`) preserved. We
// strip the compass suffix BEFORE prefix-matching so id/kind extraction
// works on both port-anchored and plain endpoints.
function _stripCompass(s) {
  return s.replace(/:[ewns]$/, "");
}

function _idFromTitle(title) {
  const stripped = _stripCompass(title);
  for (const [prefix, _kind] of Object.entries(PREFIX_TO_KIND)) {
    if (stripped.startsWith(prefix)) return stripped.slice(prefix.length);
  }
  return stripped;
}

function _kindFromTitle(title) {
  const stripped = _stripCompass(title);
  for (const [prefix, kind] of Object.entries(PREFIX_TO_KIND)) {
    if (stripped.startsWith(prefix)) return kind;
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
// CF.3.f post-fix: any role↔template-port edge is a rail edge (the
// template's leg cell is the rail's port surrogate post-CF.3.f). The
// old `template_member` arm is dead (CF.3.f deletes membership edges
// entirely — templates are composite shapes, not clusters with rails
// inside). Anything role↔template OR template↔role is rail_bundle.
function _edgeKind(srcId, dstId) {
  const srcKind = _kindFromTitle(srcId);
  const dstKind = _kindFromTitle(dstId);
  if (srcId === dstId) return "self_loop";
  if (srcKind === "role" && dstKind === "role") {
    // Could be rail_bundle or control_parent — both go role→role.
    // Default to rail_bundle; the post-process below uses the typed
    // graph's edge list to overwrite control-parent cases.
    return "rail_bundle";
  }
  if (
    (srcKind === "role" && dstKind === "template") ||
    (srcKind === "template" && dstKind === "role")
  ) {
    // CF.3.f — rail edges dock at the template's leg port; the title
    // looks like role↔template after port stripping. Pre-CF.3.f rail
    // nodes were separate (role→rail→role); post-CF.3.f they're cells
    // in the composite. Both renderings carry the rail-flow semantic.
    return "rail_bundle";
  }
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
  if (!el) return { role_meta: {}, edge_meta: {} };
  try {
    const parsed = JSON.parse(el.textContent || "{}");
    return {
      role_meta: parsed.role_meta || {},
      edge_meta: parsed.edge_meta || {},
    };
  } catch (err) {
    console.error("studio/diagram: bad sidecar JSON", err);
    return { role_meta: {}, edge_meta: {} };
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
  // CF.3.c — the server-provided edge_meta sidecar carries the typed
  // edge kind keyed by the exact `<src>-><dst>` form graphviz emits,
  // letting us bypass the title heuristic. Falls back to the
  // heuristic only when the sidecar misses (defensive — should be
  // rare; e.g. an edge graphviz emitted but the server didn't
  // instrument).
  const edgeMeta = sidecar.edge_meta || {};
  for (const g of svg.querySelectorAll('g.edge')) {
    const titleEl = g.querySelector('title');
    if (!titleEl) continue;
    const titleText = titleEl.textContent.trim();
    const parsed = _parseEdgeTitle(titleText);
    if (!parsed) continue;
    const fromMeta = edgeMeta[titleText];
    const kind = fromMeta || _edgeKind(parsed.source, parsed.target);
    g.setAttribute('data-source', parsed.source);
    g.setAttribute('data-target', parsed.target);
    g.setAttribute('data-kind', kind);
    if (kind in edgeCounts) edgeCounts[kind] += 1;
  }

  if (status) {
    const totalEdges = Object.values(edgeCounts).reduce((s, n) => s + n, 0);
    const nodes = counts.role + counts.rail + counts.template;
    // CF.3.m polish — surface the deployment prefix in the bottom
    // status line too (data-prefix is the L2 stem the server already
    // shows in the sidebar's summary strip). Reads as a single
    // breadcrumb-style line: `<prefix> · <nodes> · <edges>`.
    const prefix = status.dataset.prefix || "";
    const counts_str = `${nodes} nodes · ${totalEdges} edges`;
    status.textContent = prefix ? `${prefix} · ${counts_str}` : counts_str;
  }

  // Wire chrome interactivity now that the SVG is annotated.
  _wireToggles(svg);
  _wireEdgeLabelToggles(svg);
  _wireFocus(svg);
  _wirePanZoom(svg);
  _wireCoverage(svg);
  _wireTrainer(svg);
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
  // CF.3.m polish — sidebar header "Reset zoom" button. Lives in the
  // always-visible part of the floating sidebar so a misadjusted view
  // can be recovered when the collapsible body is closed.
  const resetZoomBtn = document.getElementById("reset-zoom-btn");
  if (resetZoomBtn) {
    resetZoomBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      reset();
    });
  }
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
    // CF.3.m — right-click → context menu with "Open in editor".
    // Skip when the node id can't be mapped to a unique edit URL
    // (bundles + roles) — the browser's native menu fires instead,
    // so the operator isn't stuck with a no-op popup.
    node.addEventListener("contextmenu", (e) => {
      const id = node.getAttribute('data-id');
      if (!id) return;
      const editorUrl = _editorUrlForNode(id);
      if (!editorUrl) return;  // unresolvable; defer to browser menu
      e.preventDefault();
      e.stopPropagation();
      _showNodeContextMenu(e.clientX, e.clientY, id, editorUrl);
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

// CF.3.m polish — node-id → L2 Editor *edit* URL. Mirror of the
// Python `_editor_url_for_focus_node` in `_studio_routes.py`; drift
// between the two is a UX bug. Per operator lock (2026-06-05):
// right-click "Open in editor" must land directly on `/edit`, not a
// list / view page. Synthetic bundles + roles (shared across
// multiple accounts) return null and the caller suppresses the
// context menu entirely — disambiguation deferred until we wire a
// "which entity uses this role?" picker.
function _editorUrlForNode(nodeId) {
  if (!nodeId) return null;
  if (nodeId.startsWith("rail__bundle_")) return null;
  if (nodeId.startsWith("rail__")) {
    return "/l2_shape/rail/" + nodeId.slice("rail__".length) + "/edit";
  }
  if (nodeId.startsWith("tmpl__")) {
    return "/l2_shape/transfer_template/"
      + nodeId.slice("tmpl__".length) + "/edit";
  }
  if (nodeId.startsWith("role__")) return null;  // ambiguous; deferred
  return null;
}

// CF.3.m — single-item right-click menu. Lazily-created floating div;
// dismisses on next click anywhere, Esc, or scroll. Keeps the surface
// tiny — when we need a second item we can lift this into a typed
// menu primitive.
let _contextMenuEl = null;
function _showNodeContextMenu(x, y, nodeId, editorUrl) {
  _hideNodeContextMenu();
  const menu = document.createElement("div");
  menu.id = "diagram-node-contextmenu";
  // Tailwind utilities — match the sidebar's chrome shape.
  menu.className = (
    "absolute z-30 bg-white border border-surface-border " +
    "shadow-md rounded-md text-sm py-1 min-w-44"
  );
  menu.style.top = y + "px";
  menu.style.left = x + "px";
  const link = document.createElement("a");
  link.href = editorUrl;
  link.className = (
    "block px-3 py-1.5 text-primary-fg no-underline " +
    "hover:bg-link-tint"
  );
  link.textContent = "Edit this entity";
  link.setAttribute("data-node-id", nodeId);
  menu.appendChild(link);
  document.body.appendChild(menu);
  _contextMenuEl = menu;
  // One-shot dismiss handlers.
  const dismiss = (e) => {
    if (e && e.target && menu.contains(e.target)) return;
    _hideNodeContextMenu();
  };
  // Defer the listeners so the click that opened the menu doesn't
  // immediately close it.
  setTimeout(() => {
    document.addEventListener("click", dismiss, { once: true });
    document.addEventListener("contextmenu", dismiss, { once: true });
    document.addEventListener("scroll", _hideNodeContextMenu, { once: true, capture: true });
  }, 0);
  document.addEventListener("keydown", _contextMenuKeydown);
}

function _hideNodeContextMenu() {
  if (_contextMenuEl && _contextMenuEl.parentNode) {
    _contextMenuEl.parentNode.removeChild(_contextMenuEl);
  }
  _contextMenuEl = null;
  document.removeEventListener("keydown", _contextMenuKeydown);
}

function _contextMenuKeydown(e) {
  if (e.key === "Escape") _hideNodeContextMenu();
}

// X.4.c.5.d/e — Coverage overlay. The chrome's `#toggle-coverage`
// checkbox is server-rendered ONLY when the demo-DB pool is wired
// (see `<meta name="diagram-coverage-available">`); this function
// no-ops if either the meta or the checkbox is absent.
//
// On toggle-on: fetch /diagram/coverage once (cached for the
// session — Studio's audience is one user iterating, not a hot path),
// stamp `data-presence="yes|no"` + `data-row-count="N"` per node,
// inject a <title> with "<id> · N rows" so the browser's native
// hover tooltip shows the count, then add `.coverage-on` to the SVG
// root so the CSS tint rules activate.
//
// On toggle-off: drop `.coverage-on` from the SVG root. The
// data-presence / data-row-count attrs stay in place (cheap to leave;
// re-toggling on doesn't need a re-fetch).
let _coverageCache = null;
async function _wireCoverage(svg) {
  if (!document.querySelector('meta[name="diagram-coverage-available"]')) {
    return;
  }
  const cb = document.getElementById("toggle-coverage");
  if (!cb) return;

  const apply = async () => {
    if (!cb.checked) {
      svg.classList.remove("coverage-on");
      return;
    }
    if (_coverageCache === null) {
      try {
        const resp = await fetch("/diagram/coverage");
        if (!resp.ok) {
          console.error("studio/diagram: /diagram/coverage", resp.status);
          return;
        }
        _coverageCache = await resp.json();
      } catch (err) {
        console.error("studio/diagram: coverage fetch failed", err);
        return;
      }
    }
    _stampCoverage(svg, _coverageCache);
    svg.classList.add("coverage-on");
  };
  cb.addEventListener("change", apply);
  // Off by default — no initial apply.
}

function _stampCoverage(svg, cov) {
  const nodes = cov.nodes || {};
  for (const g of svg.querySelectorAll('g.node[data-id]')) {
    const id = g.getAttribute('data-id');
    const entry = nodes[id];
    if (!entry) continue;
    g.setAttribute('data-presence', entry.present ? 'yes' : 'no');
    g.setAttribute('data-row-count', String(entry.count));
    // Inject / update the <title> with the count for native hover.
    let titleEl = g.querySelector('title');
    if (!titleEl) {
      titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      g.insertBefore(titleEl, g.firstChild);
    }
    const display = g.getAttribute('data-display-id') || id;
    titleEl.textContent = entry.present
      ? `${display} · ${entry.count.toLocaleString()} rows`
      : `${display} · no data`;
  }

  // Edges: chain edges by id `chain__<src>__<dst>`. The graphviz
  // edges carry data-source / data-target on g.edge — synthesize the
  // chain edge id by stripping the `rail__`/`tmpl__` prefix from each
  // endpoint, since chain edges fly between rails / templates.
  const chainEdges = cov.chain_edges || {};
  for (const g of svg.querySelectorAll('g.edge[data-kind="chain"]')) {
    const src = g.getAttribute('data-source') || "";
    const dst = g.getAttribute('data-target') || "";
    const srcName = _stripIdPrefix(src);
    const dstName = _stripIdPrefix(dst);
    const edgeId = `chain__${srcName}__${dstName}`;
    const entry = chainEdges[edgeId];
    if (!entry) continue;
    g.setAttribute('data-presence', entry.present ? 'yes' : 'no');
    g.setAttribute('data-row-count', String(entry.count));
  }
}

function _stripIdPrefix(s) {
  for (const prefix of Object.keys(PREFIX_TO_KIND)) {
    if (s.startsWith(prefix)) return s.slice(prefix.length);
  }
  return s;
}

// X.4.c.6 — Trainer overlay. Pure scenario walk on the server (no DB);
// route is always mounted, but we still gate on the
// `<meta name="diagram-trainer-available">` so the chrome contract is
// symmetric with coverage's.
//
// On toggle-on: fetch /diagram/trainer once (cached for the session),
// stamp `data-trainer-kinds="drift,overdraft,..."` on each node that
// has a planted plant, append the kinds to the existing <title>
// hover, then add `.trainer-on` to the SVG root so the CSS badges
// activate. On toggle-off: drop `.trainer-on`. The data-trainer-kinds
// attrs stay (cheap; re-toggle doesn't refetch).
let _trainerCache = null;
async function _wireTrainer(svg) {
  if (!document.querySelector('meta[name="diagram-trainer-available"]')) {
    return;
  }
  const cb = document.getElementById("toggle-trainer");
  if (!cb) return;

  const apply = async () => {
    if (!cb.checked) {
      svg.classList.remove("trainer-on");
      return;
    }
    if (_trainerCache === null) {
      try {
        const resp = await fetch("/diagram/trainer");
        if (!resp.ok) {
          console.error("studio/diagram: /diagram/trainer", resp.status);
          return;
        }
        _trainerCache = await resp.json();
      } catch (err) {
        console.error("studio/diagram: trainer fetch failed", err);
        return;
      }
    }
    _stampTrainer(svg, _trainerCache);
    svg.classList.add("trainer-on");
  };
  cb.addEventListener("change", apply);
  // Off by default — no initial apply.
}

function _stampTrainer(svg, tr) {
  const nodes = tr.nodes || {};
  for (const g of svg.querySelectorAll('g.node[data-id]')) {
    const id = g.getAttribute('data-id');
    const kinds = nodes[id];
    if (!kinds) continue;
    // Comma-joined kind list — the data attr is what the CSS keys off
    // (e.g. `[data-trainer-kinds*="drift"]`); the actual counts live
    // in the title for hover.
    const kindList = Object.keys(kinds).sort();
    g.setAttribute('data-trainer-kinds', kindList.join(','));

    // Append "[plants: drift×2, overdraft×1]" to the existing <title>
    // so the operator sees both row count + plant breakdown.
    const summary = kindList
      .map((k) => `${k}×${kinds[k]}`)  // × = multiplication sign
      .join(', ');
    let titleEl = g.querySelector('title');
    if (!titleEl) {
      titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      g.insertBefore(titleEl, g.firstChild);
    }
    const display = g.getAttribute('data-display-id') || id;
    const baseTitle = titleEl.textContent || display;
    // Avoid double-appending the plants block on re-toggle.
    if (!baseTitle.includes('[plants:')) {
      titleEl.textContent = `${baseTitle} [plants: ${summary}]`;
    }
  }
}

// X.4.c.7 — Test-mode export. When window.__test_mode__ is set
// BEFORE this script loads, expose the per-feature helpers on
// window.__diagram_internals__ so tests/js/test_diagram_*.py can
// drive them in isolation against a fixture SVG. Mirrors bootstrap.js's
// pattern (X.2.a.2). Production pages never set the flag → zero
// surface bleed; test mode also short-circuits the auto-renderDiagram
// invocation below so the fixture doesn't trigger the wasm-graphviz
// dynamic import (which fails over file:// CORS).
if (typeof window !== "undefined" && window.__test_mode__) {
  window.__diagram_internals__ = {
    _stampCoverage,
    _stampTrainer,
    _kindFromTitle,
    _idFromTitle,
    _parseEdgeTitle,
    _edgeKind,
    _stripIdPrefix,
  };
} else if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderDiagram);
} else {
  renderDiagram();
}
