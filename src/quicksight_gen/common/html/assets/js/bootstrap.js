// Bootstrap JS — runs on initial page load AND after every HTMX swap.
// Hydration model (selector notation, NOT literal HTML — render.py's
// inline-script load step would otherwise see angle brackets and
// either trip Python tests or terminate the script tag prematurely):
//
//   section[data-visual-kind="Sankey"][data-visual-id="X"]
//     > div#visual-data-X.visual-data       ← HTMX swap target
//         > script[type="application/json"][class="chart-data"]
//             contains the d3 payload
//
// After swap, evt.detail.target is the .visual-data div. Walk UP
// to its enclosing [data-visual-kind] section and dispatch by kind.
// The script tag with the JSON payload sits inside the swap target.
//
// Currently supports Sankey + ForceGraph. New visual kinds add one
// case arm to hydrateSection + one renderXxx function.
//
// The htmx:afterSwap event is the X.4 future-proofing hook — that
// phase's swap-on-edit pattern reuses this exact dispatch.

(() => {
  // Build the merged values dict for an anchor click — current form
  // inputs PLUS the anchor selection. d3 owns the SVG so it owns the
  // click; htmx.ajax() is HTMX's documented programmatic-trigger API
  // and produces a request indistinguishable from an attribute-bound
  // hx-post (same swap target, same headers, same hydrate path on
  // the response).
  function fireAnchorRequest(visualId, anchorName) {
    var form = document.querySelector("#filter-form");
    var values = { anchor: anchorName };
    if (form) {
      new FormData(form).forEach((v, k) => {
        values[k] = v;
      });
    }
    // Fire a custom event so the dev-log forwarder (if enabled) can
    // capture the user-intent moment BEFORE htmx.ajax fires its own
    // events. No-op when dev-log is off.
    document.body.dispatchEvent(
      new CustomEvent("sankey:click", {
        detail: { visualId: visualId, anchor: anchorName },
      }),
    );
    htmx.ajax("POST", "/visual/" + visualId + "/data", {
      target: "#visual-data-" + visualId,
      swap: "innerHTML",
      values: values,
    });
  }

  function hydrateSection(section) {
    var dataScript = section.querySelector("script.chart-data");
    if (!dataScript) return;
    var kind = section.getAttribute("data-visual-kind");
    var visualId = section.getAttribute("data-visual-id");
    var data;
    try {
      data = JSON.parse(dataScript.textContent);
    } catch (e) {
      console.error("bad chart data", e);
      return;
    }
    var target = section.querySelector(".visual-data");
    if (!target) return;
    target.querySelectorAll("svg").forEach((s) => {
      s.remove();
    });
    switch (kind) {
      case "Sankey":
        renderSankey(target, data, visualId);
        break;
      case "ForceGraph":
        renderForceGraph(target, data, visualId);
        break;
      default:
        console.warn("no hydrator for kind", kind);
    }
  }

  function hydrate(root) {
    // Handle both initial-load (root = body, scan inside) and
    // post-swap (root = .visual-data div, walk up to section) cases.
    if (root.matches?.("[data-visual-kind]")) {
      hydrateSection(root);
      return;
    }
    var section = root.closest?.("[data-visual-kind]");
    if (section) {
      hydrateSection(section);
      return;
    }
    if (root.querySelectorAll) {
      root.querySelectorAll("[data-visual-kind]").forEach(hydrateSection);
    }
  }

  function renderSankey(target, data, visualId) {
    var width = target.clientWidth || 800;
    var height = 400;
    var svg = d3
      .select(target)
      .append("svg")
      .attr("width", width)
      .attr("height", height);
    var sankey = d3
      .sankey()
      .nodeWidth(15)
      .nodePadding(10)
      .extent([
        [1, 1],
        [width - 1, height - 6],
      ]);
    var graph = sankey({
      nodes: data.nodes.map((d) => Object.assign({}, d)),
      links: data.links.map((d) => Object.assign({}, d)),
    });
    svg
      .append("g")
      .selectAll("rect")
      .data(graph.nodes)
      .enter()
      .append("rect")
      .attr("x", (d) => d.x0)
      .attr("y", (d) => d.y0)
      .attr("height", (d) => d.y1 - d.y0)
      .attr("width", (d) => d.x1 - d.x0)
      // Tailwind classes target SVG presentation via fill-* /
      // stroke-* utilities. Hover + transition give the click
      // affordance for free; cursor-pointer replaces the inline
      // .style('cursor') we had before.
      .attr(
        "class",
        "fill-blue-500 hover:fill-blue-700 cursor-pointer transition-colors",
      )
      .on("click", (_event, d) => {
        if (visualId) fireAnchorRequest(visualId, d.name);
      });
    svg
      .append("g")
      .attr("fill", "none")
      .selectAll("path")
      .data(graph.links)
      .enter()
      .append("path")
      .attr("d", d3.sankeyLinkHorizontal())
      .attr("class", "stroke-slate-400")
      .attr("stroke-opacity", 0.35)
      .attr("stroke-width", (d) => Math.max(1, d.width));
    svg
      .append("g")
      .selectAll("text")
      .data(graph.nodes)
      .enter()
      .append("text")
      .attr("x", (d) => (d.x0 < width / 2 ? d.x1 + 6 : d.x0 - 6))
      .attr("y", (d) => (d.y1 + d.y0) / 2)
      .attr("dy", "0.35em")
      .attr("text-anchor", (d) => (d.x0 < width / 2 ? "start" : "end"))
      .text((d) => d.name)
      .attr("class", "fill-slate-700 text-xs font-sans pointer-events-none");
  }

  // d3-force ships in the d3 main bundle — no separate CDN needed.
  // Layout is iterative (alpha decays each tick); sim.tick() fires
  // until equilibrium then stops. Click on a node fires the same
  // anchor pattern as the Sankey for consistency.
  function renderForceGraph(target, data, visualId) {
    var width = target.clientWidth || 800;
    var height = 400;
    var svg = d3
      .select(target)
      .append("svg")
      .attr("width", width)
      .attr("height", height);

    // Mutate copies — d3.forceSimulation rewrites x/y on the
    // node/link objects it's given. Avoid stomping the JSON we
    // received from the server.
    var nodes = data.nodes.map((d) => Object.assign({}, d));
    var links = data.links.map((d) => Object.assign({}, d));

    var sim = d3
      .forceSimulation(nodes)
      .force(
        "link",
        d3
          .forceLink(links)
          .id((d) => d.id)
          .distance(80)
          .strength(0.7),
      )
      .force("charge", d3.forceManyBody().strength(-220))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide(22));

    var link = svg
      .append("g")
      .selectAll("line")
      .data(links)
      .enter()
      .append("line")
      .attr("class", "stroke-slate-400")
      .attr("stroke-opacity", 0.5)
      .attr("stroke-width", 1.5);

    var node = svg
      .append("g")
      .selectAll("circle")
      .data(nodes)
      .enter()
      .append("circle")
      .attr("r", 12)
      .attr(
        "class",
        "fill-blue-500 hover:fill-blue-700 cursor-pointer transition-colors",
      )
      .on("click", (_event, d) => {
        if (visualId) fireAnchorRequest(visualId, d.id || d.label);
      });

    var label = svg
      .append("g")
      .selectAll("text")
      .data(nodes)
      .enter()
      .append("text")
      .text((d) => d.label || d.id)
      .attr("class", "fill-slate-700 text-xs font-sans pointer-events-none");

    sim.on("tick", () => {
      link
        .attr("x1", (d) => d.source.x)
        .attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x)
        .attr("y2", (d) => d.target.y);
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      label.attr("x", (d) => d.x + 14).attr("y", (d) => d.y + 4);
    });
  }

  document.addEventListener("htmx:afterSwap", (evt) => {
    hydrate(evt.detail.target);
  });
  document.addEventListener("DOMContentLoaded", () => {
    hydrate(document.body);
  });

  // X.2.a.2 — test-mode export. When window.__test_mode__ is set
  // BEFORE this script runs (via Playwright's addInitScript), the
  // IIFE-scoped functions become reachable for unit tests under
  // tests/js/. Production deploys never set the flag, so the export
  // costs nothing at runtime.
  if (typeof window !== "undefined" && window.__test_mode__) {
    window.__bootstrap_internals__ = {
      fireAnchorRequest: fireAnchorRequest,
      hydrate: hydrate,
      hydrateSection: hydrateSection,
      renderSankey: renderSankey,
      renderForceGraph: renderForceGraph,
    };
  }
})();
