// BX.8 (2026-06-11) — inline mini-diagram renderer for edit pages
// (Direction D2 from `docs/audits/bx_0_8_design_mockups/bx_8.md`).
//
// Reads a focused DOT source from `<template id="mini-topology-dot">`,
// renders it via wasm-graphviz into `#mini-diagram-target`, then
// post-processes the SVG:
//
//   1. Strip width/height so the SVG fills the wrapper's box.
//   2. Add `class="topology-svg"` so the shared diagram-svg.css rules
//      apply (the wrapper has `.studio-mini` for further scoping).
//   3. Walk every `g.node`, set `data-id` / `data-kind` from its title
//      (same rule as the full diagram), and tag the SELF node — the
//      one matching the wrapper's `data-self-id` — with
//      `class="self"` + `data-role="mini-diagram-self"`.
//   4. Do NOT wire the focus-click or coverage/trainer handlers; the
//      mini is a read-only "you are here" inset.
//
// Click semantics on neighbor nodes (operator Q2 lock): clicking
// anywhere on a node body navigates to that entity's edit page when
// resolvable (rail / template) — implemented by reusing the same
// `_editorUrlForNode` arms via a direct anchor-style click handler.
// Bundles + shared roles fall through to no-op (consistent with the
// main diagram's right-click rule). The self-node never navigates on
// click (you're already editing it).

const PREFIX_TO_KIND = {
  "role__": "role",
  "rail__": "rail",
  "tmpl__": "template",
};

function _stripCompass(s) {
  return s.replace(/:[ewns]$/, "");
}

function _kindFromTitle(title) {
  const stripped = _stripCompass(title);
  for (const [prefix, kind] of Object.entries(PREFIX_TO_KIND)) {
    if (stripped.startsWith(prefix)) return kind;
  }
  return "unknown";
}

function _idFromTitle(title) {
  const stripped = _stripCompass(title);
  for (const [prefix, _kind] of Object.entries(PREFIX_TO_KIND)) {
    if (stripped.startsWith(prefix)) return stripped.slice(prefix.length);
  }
  return stripped;
}

// Mirrors `_editorUrlForNode` in diagram.js + `_editor_url_for_focus_node`
// in `_studio_routes.py`. Drift between the three is a UX bug — the
// JS unit test pins parity for diagram.js vs Python; this duplicate is
// load-bearing because mini-diagram.js is loaded only on edit pages
// and importing diagram.js's symbols would force its auto-render to fire
// against the wrong target.
function _miniEditorUrlForNode(nodeId) {
  if (!nodeId) return null;
  if (nodeId.startsWith("rail__bundle_")) return null;
  if (nodeId.startsWith("rail__")) {
    return "/l2_shape/rail/" + nodeId.slice("rail__".length) + "/edit";
  }
  if (nodeId.startsWith("tmpl__")) {
    return "/l2_shape/transfer_template/"
      + nodeId.slice("tmpl__".length) + "/edit";
  }
  if (nodeId.startsWith("role__")) return null;
  return null;
}

let _miniRendererPromise = null;
function _getMiniRenderer() {
  if (_miniRendererPromise === null) {
    _miniRendererPromise = (async () => {
      const mod = await import("/studio/wasm-graphviz/index.js");
      return await mod.Graphviz.load();
    })();
  }
  return _miniRendererPromise;
}

async function renderMiniDiagram() {
  const wrapper = document.querySelector('[data-role="mini-diagram"]');
  if (!wrapper) return;
  const dotTemplate = wrapper.querySelector('#mini-topology-dot');
  const target = wrapper.querySelector('#mini-diagram-target');
  if (!dotTemplate || !target) {
    console.error("studio/mini-diagram: missing #mini-topology-dot or #mini-diagram-target");
    return;
  }
  const dot = dotTemplate.content
    ? dotTemplate.content.textContent.trim()
    : dotTemplate.textContent.trim();
  if (!dot) return;
  const selfId = wrapper.getAttribute('data-self-id') || "";

  let renderer;
  try {
    renderer = await _getMiniRenderer();
  } catch (err) {
    console.error("studio/mini-diagram: wasm-graphviz load failed", err);
    return;
  }

  let svgText;
  try {
    svgText = renderer.layout(dot, "svg", "dot");
  } catch (err) {
    console.error("studio/mini-diagram: layout failed", err);
    return;
  }

  target.innerHTML = svgText;
  const svg = target.querySelector("svg");
  if (!svg) return;

  // Same dimension-strip the full diagram does so the SVG fits the
  // wrapper. preserveAspectRatio defaults to xMidYMid meet, which
  // centers the focused subgraph inside the wrapper.
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  svg.setAttribute("class", "topology-svg");
  svg.style.width = "100%";
  svg.style.height = "100%";

  // Walk nodes — tag kinds + self.
  for (const g of svg.querySelectorAll('g.node')) {
    const titleEl = g.querySelector('title');
    if (!titleEl) continue;
    const title = titleEl.textContent.trim();
    const kind = _kindFromTitle(title);
    const id = _idFromTitle(title);
    g.setAttribute('data-kind', kind);
    g.setAttribute('data-id', title);
    g.setAttribute('data-display-id', id);
    if (title === selfId) {
      g.setAttribute('class', (g.getAttribute('class') || 'node') + ' self');
      g.setAttribute('data-role', 'mini-diagram-self');
      // Self-node: explicit SR-friendly description.
      const a = g.querySelector('title');
      if (a) {
        a.textContent = `${id} — this is what you're editing`;
      }
      continue;  // no click handler on self
    }
    // Neighbors: click navigates to THEIR edit page when resolvable.
    const editUrl = _miniEditorUrlForNode(title);
    if (editUrl) {
      g.setAttribute('data-edit-href', editUrl);
      g.style.cursor = "pointer";
      g.addEventListener("click", (e) => {
        e.stopPropagation();
        window.location.href = editUrl;
      });
    }
  }
}

if (typeof window !== "undefined" && window.__test_mode__) {
  window.__mini_diagram_internals__ = {
    _kindFromTitle,
    _idFromTitle,
    _miniEditorUrlForNode,
  };
} else if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderMiniDiagram);
} else {
  renderMiniDiagram();
}
