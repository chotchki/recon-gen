// BX.16 (2026-06-11) — inline chain shape-preview re-render shim.
//
// The chain edit page wires its `#chain-shape-preview` container to the
// POST endpoint `/l2_shape/chain/shape-preview` via HTMX
// (`hx-trigger="input changed delay:300ms"`). Each swap drops fresh
// HTML into the container — including a `<template id="chain-shape-dot">`
// carrying the freshly-built DOT source. This shim listens for the swap
// event and renders the DOT into an SVG via wasm-graphviz, the same
// renderer mini-diagram.js + diagram.js use (no system `dot` dep).
//
// Separate from mini-diagram.js because:
//   - mini-diagram.js auto-renders ONCE on DOMContentLoaded against the
//     fixed-DOT "you are here" inset. Loading it on the chain edit page
//     would cause two renderers fighting over the same wasm load + render
//     target after the first HTMX swap.
//   - The chain shape preview is form-state-driven; every keystroke can
//     change the DOT. Render-on-swap is the right trigger.
//
// Empty state: the server returns a plain prompt fragment WITHOUT a
// `<template>` when the children list is empty; this shim short-circuits
// when no template is present, so the prompt renders as-is.

let _shapeRendererPromise = null;
function _getShapeRenderer() {
  if (_shapeRendererPromise === null) {
    _shapeRendererPromise = (async () => {
      const mod = await import("/studio/wasm-graphviz/index.js");
      return await mod.Graphviz.load();
    })();
  }
  return _shapeRendererPromise;
}

async function renderChainShapePreview() {
  const container = document.getElementById("chain-shape-preview");
  if (!container) return;
  const dotTemplate = container.querySelector('#chain-shape-dot');
  const target = container.querySelector('#chain-shape-target');
  // Empty state path: no template ⇒ the server-rendered prompt is final.
  if (!dotTemplate || !target) return;
  const dot = dotTemplate.content
    ? dotTemplate.content.textContent.trim()
    : dotTemplate.textContent.trim();
  if (!dot) return;

  let renderer;
  try {
    renderer = await _getShapeRenderer();
  } catch (err) {
    console.error("studio/chain-shape-preview: wasm-graphviz load failed", err);
    return;
  }

  let svgText;
  try {
    svgText = renderer.layout(dot, "svg", "dot");
  } catch (err) {
    console.error("studio/chain-shape-preview: layout failed", err);
    return;
  }

  target.innerHTML = svgText;
  const svg = target.querySelector("svg");
  if (!svg) return;
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  svg.setAttribute("class", "topology-svg");
  svg.style.width = "100%";
  svg.style.height = "100%";
}

if (typeof window !== "undefined" && window.__test_mode__) {
  window.__chain_shape_preview_internals__ = {
    renderChainShapePreview,
  };
} else {
  // Render on initial page load (server emits the first preview inline
  // so the operator sees the current chain shape without typing) +
  // after every HTMX swap into the preview container.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderChainShapePreview);
  } else {
    renderChainShapePreview();
  }
  document.body.addEventListener("htmx:afterSwap", (evt) => {
    const tgt = evt.target;
    if (tgt && tgt.id === "chain-shape-preview") {
      renderChainShapePreview();
    }
  });
}
