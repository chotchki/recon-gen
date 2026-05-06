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
// Currently supports KPI + Table + Sankey + ForceGraph. New
// visual kinds add one case arm to hydrateSection + one
// renderXxx function.
//
// The htmx:afterSwap event is the X.4 future-proofing hook — that
// phase's swap-on-edit pattern reuses this exact dispatch.

(() => {
  // Build the merged values dict for an anchor click — current form
  // inputs PLUS the anchor selection. d3 owns the SVG so it owns the
  // click; htmx.ajax() is HTMX's documented programmatic-trigger API
  // and produces a request indistinguishable from an attribute-bound
  // hx-get (same swap target, same headers, same hydrate path on
  // the response).
  //
  // X.2.b: GET, not POST — the URL is the cache key + bookmark, and
  // the server's route is a path-templated GET. Each section carries
  // its own data-fetch-url (server-side authority on URL shape).
  function fireAnchorRequest(visualId, anchorName) {
    var section = document.querySelector(
      'section[data-visual-id="' + visualId + '"]',
    );
    var fetchUrl = section ? section.getAttribute("data-fetch-url") : null;
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
    if (!fetchUrl) {
      // Defensive: a section without data-fetch-url means the page
      // shell didn't render with X.2.b's REST surface. Surface it so
      // a regression doesn't fail silently.
      console.error(
        "fireAnchorRequest: no data-fetch-url for visual",
        visualId,
      );
      return;
    }
    htmx.ajax("GET", fetchUrl, {
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
    // Clear any prior render — chart-data script tag included. The
    // script already gave us the data; the renderXxx below paints
    // fresh into a clean target. Without this, repeat hydrates
    // (initial-load + post-swap) would accumulate DOM children.
    target.innerHTML = "";
    switch (kind) {
      case "KPI":
        renderKPI(target, data, visualId);
        break;
      case "Table":
        renderTable(target, data, visualId);
        break;
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

  // KPI — one big number per measure, with optional label + delta
  // arrow underneath. Data shape:
  //   { values: [{ value: 1234, label: "Open", format: "number"|"currency",
  //                delta: -50? }, ...] }
  // Single-value shorthand also accepted: { value: 1234, label: "Open" }
  // Pure HTML (no SVG) — text is the right primitive for a number,
  // and Tailwind's tabular-nums keeps digit columns aligned across
  // KPIs in a row.
  function renderKPI(target, data, _visualId) {
    var values = data.values
      ? data.values
      : [{ value: data.value, label: data.label || "", format: data.format }];
    var container = d3
      .select(target)
      .append("div")
      .attr("class", "flex flex-wrap gap-6 p-4");
    var cards = container
      .selectAll("div.kpi-card")
      .data(values)
      .enter()
      .append("div")
      .attr("class", "kpi-card flex-1 min-w-[180px] text-center");
    cards
      .append("div")
      .attr("class", "kpi-value text-4xl font-bold text-blue-600 tabular-nums")
      .text((d) => formatKPIValue(d.value, d.format));
    cards
      .filter((d) => typeof d.delta === "number")
      .append("div")
      .attr(
        "class",
        (d) =>
          "kpi-delta text-sm tabular-nums " +
          (d.delta < 0 ? "text-red-600" : "text-green-600"),
      )
      .text(
        (d) =>
          (d.delta >= 0 ? "▲ +" : "▼ ") +
          formatKPIValue(Math.abs(d.delta), d.format),
      );
    cards
      .append("div")
      .attr("class", "kpi-label text-sm text-slate-600 mt-2")
      .text((d) => d.label || "");
  }

  function formatKPIValue(value, format) {
    if (typeof value !== "number") return String(value == null ? "" : value);
    if (format === "currency") {
      return (
        "$" +
        value.toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })
      );
    }
    return value.toLocaleString("en-US");
  }

  // Table — sortable columns + page-offset pagination + a "0–50 of
  // 1247" total-row count (X.2.a.6 hint: total row count is the
  // win over QS's "page 1 of N" — both UX clearer for users AND
  // testability win for e2e). Data shape:
  //   { columns: [{name: "id", label: "ID", format?: "currency"|"number"|"date"}, ...],
  //     rows: [["v1", "v2", ...], ...],
  //     total_rows: 1247,
  //     page_offset: 0,
  //     page_size: 50,
  //     sort_column?: "id:desc"  // current sort, server-resolved }
  // Sort + paginate are URL state — clicking a header / pager fires
  // an HTMX swap with new query-string params; URL == cache key
  // (X.2.b's GET-shape contract) means the same view is bookmarkable.
  function renderTable(target, data, visualId) {
    var section = target.closest("section[data-visual-kind]");
    var fetchUrl = section ? section.getAttribute("data-fetch-url") : null;
    var columns = data.columns || [];
    var rows = data.rows || [];
    var pageOffset =
      typeof data.page_offset === "number" ? data.page_offset : 0;
    var pageSize =
      typeof data.page_size === "number" ? data.page_size : rows.length;
    var totalRows =
      typeof data.total_rows === "number" ? data.total_rows : rows.length;
    var currentSort = data.sort_column || "";

    // Outer wrapper — overflow-x-auto so wide tables scroll
    // horizontally rather than overflowing the dashboard layout.
    var wrapper = d3
      .select(target)
      .append("div")
      .attr("class", "overflow-x-auto");
    var table = wrapper
      .append("table")
      .attr("class", "table-data min-w-full text-sm");

    // Header row — sticky so long-scroll tables don't lose context.
    var thead = table
      .append("thead")
      .attr("class", "sticky top-0 bg-slate-100 text-slate-700 font-semibold");
    var headerRow = thead.append("tr");
    headerRow
      .selectAll("th")
      .data(columns)
      .enter()
      .append("th")
      .attr("class", "px-3 py-2 text-left border-b border-slate-300")
      .each(function (col) {
        var th = d3.select(this);
        // Sort link — clicking flips sort direction (asc → desc →
        // unsorted). Without a fetch URL the link still renders but
        // doesn't carry the swap directives (renderTable is exercised
        // outside an HTMX swap during JS unit tests).
        var nextSort = nextSortDirection(col.name, currentSort);
        var sortBadge = sortBadgeFor(col.name, currentSort);
        if (fetchUrl) {
          th.append("a")
            .attr(
              "href",
              buildTableUrl(fetchUrl, {
                sort_column: nextSort,
                page_offset: 0,
                page_size: pageSize,
              }),
            )
            .attr(
              "hx-get",
              buildTableUrl(fetchUrl, {
                sort_column: nextSort,
                page_offset: 0,
                page_size: pageSize,
              }),
            )
            .attr("hx-target", "#visual-data-" + visualId)
            .attr("hx-push-url", "true")
            .attr("class", "table-sort-link hover:text-blue-700")
            .text(col.label || col.name)
            .append("span")
            .attr("class", "table-sort-badge ml-1 text-xs")
            .text(sortBadge);
        } else {
          th.append("span").text(col.label || col.name);
          if (sortBadge) {
            th.append("span")
              .attr("class", "table-sort-badge ml-1 text-xs")
              .text(sortBadge);
          }
        }
      });

    // Body — striped rows, monospace numerics where format hints
    // it via tabular-nums.
    var tbody = table.append("tbody");
    var trs = tbody
      .selectAll("tr")
      .data(rows)
      .enter()
      .append("tr")
      .attr(
        "class",
        (_d, i) => "table-row " + (i % 2 === 0 ? "bg-white" : "bg-slate-50"),
      );
    trs
      .selectAll("td")
      .data((row) => row.map((v, ci) => ({ value: v, col: columns[ci] || {} })))
      .enter()
      .append("td")
      .attr(
        "class",
        (cell) =>
          "px-3 py-2 border-b border-slate-200 " +
          (isNumericFormat(cell.col.format) ? "tabular-nums text-right" : ""),
      )
      .text((cell) => formatTableCell(cell.value, cell.col.format));

    // Pager — "0–50 of 1247" + Prev/Next links. Range uses 1-based
    // human counting (so "1–50 of 1247" reads naturally) but the
    // page_offset query param stays 0-based for consistency with
    // standard pagination semantics.
    var pager = wrapper
      .append("div")
      .attr(
        "class",
        "table-pager flex items-center justify-between mt-3 px-3 text-sm text-slate-600",
      );
    var displayStart = totalRows === 0 ? 0 : pageOffset + 1;
    var displayEnd = Math.min(pageOffset + pageSize, totalRows);
    pager
      .append("span")
      .attr("class", "table-pager-range")
      .text(displayStart + "–" + displayEnd + " of " + totalRows);
    var nav = pager.append("div").attr("class", "flex gap-2");
    var prevDisabled = pageOffset <= 0;
    var nextDisabled = pageOffset + pageSize >= totalRows;
    if (fetchUrl) {
      nav
        .append("a")
        .attr("class", pagerLinkClass(prevDisabled) + " table-pager-prev")
        .attr("aria-disabled", prevDisabled ? "true" : "false")
        .attr(
          "href",
          prevDisabled
            ? null
            : buildTableUrl(fetchUrl, {
                page_offset: Math.max(0, pageOffset - pageSize),
                page_size: pageSize,
                sort_column: currentSort,
              }),
        )
        .attr(
          "hx-get",
          prevDisabled
            ? null
            : buildTableUrl(fetchUrl, {
                page_offset: Math.max(0, pageOffset - pageSize),
                page_size: pageSize,
                sort_column: currentSort,
              }),
        )
        .attr("hx-target", "#visual-data-" + visualId)
        .attr("hx-push-url", "true")
        .text("← Prev");
      nav
        .append("a")
        .attr("class", pagerLinkClass(nextDisabled) + " table-pager-next")
        .attr("aria-disabled", nextDisabled ? "true" : "false")
        .attr(
          "href",
          nextDisabled
            ? null
            : buildTableUrl(fetchUrl, {
                page_offset: pageOffset + pageSize,
                page_size: pageSize,
                sort_column: currentSort,
              }),
        )
        .attr(
          "hx-get",
          nextDisabled
            ? null
            : buildTableUrl(fetchUrl, {
                page_offset: pageOffset + pageSize,
                page_size: pageSize,
                sort_column: currentSort,
              }),
        )
        .attr("hx-target", "#visual-data-" + visualId)
        .attr("hx-push-url", "true")
        .text("Next →");
    }
    // After the new HTMX-attributed nodes are in the DOM, re-process
    // them so HTMX's attribute scanner picks up the hx-get directives.
    // Without this the click would 404 against the browser-resolved
    // href instead of going through HTMX's swap pipeline.
    if (typeof htmx !== "undefined" && htmx.process) {
      htmx.process(target);
    }
  }

  // Sort cycle: clicking a column cycles asc → desc → off (back to
  // server default ordering). Encoded as ``col:asc`` / ``col:desc``
  // / empty in the sort_column query param.
  function nextSortDirection(colName, currentSort) {
    if (currentSort === colName + ":asc") return colName + ":desc";
    if (currentSort === colName + ":desc") return "";
    return colName + ":asc";
  }

  function sortBadgeFor(colName, currentSort) {
    if (currentSort === colName + ":asc") return "▲";
    if (currentSort === colName + ":desc") return "▼";
    return "";
  }

  function isNumericFormat(format) {
    return format === "currency" || format === "number";
  }

  function formatTableCell(value, format) {
    if (value == null) return "";
    if (typeof value === "number") return formatKPIValue(value, format);
    return String(value);
  }

  function pagerLinkClass(disabled) {
    if (disabled) {
      return "px-2 py-1 rounded text-slate-400 cursor-not-allowed";
    }
    return "px-2 py-1 rounded text-blue-600 hover:bg-blue-50 cursor-pointer";
  }

  // Merge query-string params onto the base fetch URL. Values that
  // are empty / undefined / null get dropped so the URL stays
  // canonical for caching.
  function buildTableUrl(fetchUrl, params) {
    var u = new URL(fetchUrl, window.location.origin);
    Object.keys(params).forEach((key) => {
      var val = params[key];
      if (val === "" || val === null || val === undefined) {
        u.searchParams.delete(key);
      } else {
        u.searchParams.set(key, String(val));
      }
    });
    // Preserve relative URL form (path + ?...) so the link works
    // both via HTMX and via direct navigation.
    return u.pathname + u.search;
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
      renderKPI: renderKPI,
      renderTable: renderTable,
      renderSankey: renderSankey,
      renderForceGraph: renderForceGraph,
      formatKPIValue: formatKPIValue,
      buildTableUrl: buildTableUrl,
      nextSortDirection: nextSortDirection,
    };
  }
})();
