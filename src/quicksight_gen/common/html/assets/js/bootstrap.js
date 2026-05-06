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
// Currently supports KPI + Table + BarChart + LineChart + Sankey
// + ForceGraph. New visual kinds add one case arm to
// hydrateSection + one renderXxx function.
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
      case "BarChart":
        renderBarChart(target, data, visualId);
        break;
      case "LineChart":
        renderLineChart(target, data, visualId);
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
      .attr("class", "kpi-value text-4xl font-bold text-accent tabular-nums")
      .text((d) => formatKPIValue(d.value, d.format));
    cards
      .filter((d) => typeof d.delta === "number")
      .append("div")
      .attr(
        "class",
        (d) =>
          "kpi-delta text-sm tabular-nums " +
          (d.delta < 0 ? "text-danger" : "text-success"),
      )
      .text(
        (d) =>
          (d.delta >= 0 ? "▲ +" : "▼ ") +
          formatKPIValue(Math.abs(d.delta), d.format),
      );
    cards
      .append("div")
      .attr("class", "kpi-label text-sm text-secondary-fg mt-2")
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
      .attr(
        "class",
        "sticky top-0 bg-surface-bg text-secondary-fg font-semibold",
      );
    var headerRow = thead.append("tr");
    headerRow
      .selectAll("th")
      .data(columns)
      .enter()
      .append("th")
      .attr("class", "px-3 py-2 text-left border-b border-surface-border")
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
            .attr("class", "table-sort-link hover:opacity-80")
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
        (_d, i) => "table-row " + (i % 2 === 0 ? "bg-white" : "bg-surface-bg"),
      );
    trs
      .selectAll("td")
      .data((row) => row.map((v, ci) => ({ value: v, col: columns[ci] || {} })))
      .enter()
      .append("td")
      .attr(
        "class",
        (cell) =>
          "px-3 py-2 border-b border-surface-border " +
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
        "table-pager flex items-center justify-between mt-3 px-3 text-sm text-secondary-fg",
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

  // BarChart — vertical bars with x/y axes + axis labels. Data shape:
  //   { categories: ["Cat A", "Cat B", ...],
  //     series: [{name?: "...", values: [n1, n2, ...]}, ...],
  //     x_label?: "...",  // axis label, plain English (Q.1.a.3)
  //     y_label?: "...",
  //     format?: "number"|"currency" }
  // Single-series shorthand also accepted: ``{categories, values}``.
  // d3 native — no charting lib. Bars are click-targets for future
  // drill (X.2.e); for now they're inert.
  function renderBarChart(target, data, _visualId) {
    var width = target.clientWidth || 800;
    var height = 320;
    var margin = { top: 16, right: 24, bottom: 56, left: 64 };
    var innerW = width - margin.left - margin.right;
    var innerH = height - margin.top - margin.bottom;

    var categories = data.categories || [];
    var series = data.series
      ? data.series
      : [{ name: data.label || "", values: data.values || [] }];
    var format = data.format;

    var svg = d3
      .select(target)
      .append("svg")
      .attr("width", width)
      .attr("height", height);
    var g = svg
      .append("g")
      .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

    // Outer band per category, inner band per series so multi-series
    // groups bars side-by-side. Single-series collapses to a regular
    // bar chart by virtue of one inner slot.
    var x0 = d3.scaleBand().domain(categories).range([0, innerW]).padding(0.15);
    var x1 = d3
      .scaleBand()
      .domain(series.map((s, i) => s.name || String(i)))
      .range([0, x0.bandwidth()])
      .padding(0.05);
    var allValues = [];
    series.forEach((s) => {
      (s.values || []).forEach((v) => {
        if (typeof v === "number") allValues.push(v);
      });
    });
    var maxVal = allValues.length > 0 ? d3.max(allValues) : 0;
    var y = d3
      .scaleLinear()
      .domain([0, maxVal || 1])
      .nice()
      .range([innerH, 0]);

    // Axes — formatted via the same formatKPIValue helper so
    // currency / number formatting stays consistent across the
    // dashboard.
    var xAxis = d3.axisBottom(x0);
    var yAxis = d3
      .axisLeft(y)
      .ticks(5)
      .tickFormat((v) => formatKPIValue(v, format));
    g.append("g")
      .attr("class", "barchart-x-axis")
      .attr("transform", "translate(0," + innerH + ")")
      .call(xAxis)
      .selectAll("text")
      .attr("class", "text-xs fill-primary-fg");
    g.append("g")
      .attr("class", "barchart-y-axis")
      .call(yAxis)
      .selectAll("text")
      .attr("class", "text-xs fill-primary-fg");

    // Axis labels (plain English from the tree per Q.1.a.3 — they
    // carry over via the tree's Measure.axis_label / Column.label).
    if (data.x_label) {
      svg
        .append("text")
        .attr("class", "barchart-x-label text-xs fill-secondary-fg")
        .attr("text-anchor", "middle")
        .attr("x", margin.left + innerW / 2)
        .attr("y", height - 8)
        .text(data.x_label);
    }
    if (data.y_label) {
      svg
        .append("text")
        .attr("class", "barchart-y-label text-xs fill-secondary-fg")
        .attr("text-anchor", "middle")
        .attr(
          "transform",
          "translate(16," + (margin.top + innerH / 2) + ") rotate(-90)",
        )
        .text(data.y_label);
    }

    // Bars per (category × series) — one rect per data point.
    var seriesGroups = g
      .selectAll("g.barchart-series")
      .data(series)
      .enter()
      .append("g")
      .attr("class", "barchart-series")
      .attr("data-series-name", (s, i) => s.name || String(i));
    seriesGroups
      .selectAll("rect")
      .data((s, si) =>
        (s.values || []).map((v, ci) => ({
          value: v,
          category: categories[ci],
          seriesIdx: si,
          seriesName: s.name || String(si),
        })),
      )
      .enter()
      .append("rect")
      .attr("class", "barchart-bar fill-accent hover:opacity-80")
      .attr("x", (d) => (x0(d.category) || 0) + (x1(d.seriesName) || 0))
      .attr("y", (d) => (typeof d.value === "number" ? y(d.value) : innerH))
      .attr("width", x1.bandwidth())
      .attr("height", (d) =>
        typeof d.value === "number" ? innerH - y(d.value) : 0,
      );
  }

  // LineChart — one line per series + axes + legend. Data shape:
  //   { x_values: ["2026-01-01", ...] | [1, 2, 3, ...],
  //     series: [{name?: "...", values: [n1, n2, ...], color?: "#hex"}, ...],
  //     x_label?, y_label?, format?: "number"|"currency",
  //     x_kind?: "date"|"number"  // controls x scale (default "date") }
  // Single-series shorthand: ``{x_values, values, label}``.
  // d3 native — d3.line() + d3.scaleTime / scaleLinear. Series get
  // colour via Tailwind palette indices unless explicit ``color``
  // is supplied per series.
  function renderLineChart(target, data, _visualId) {
    var width = target.clientWidth || 800;
    var height = 320;
    var margin = { top: 16, right: 24, bottom: 56, left: 64 };
    var innerW = width - margin.left - margin.right;
    var innerH = height - margin.top - margin.bottom;

    var xValues = data.x_values || [];
    var series = data.series
      ? data.series
      : [{ name: data.label || "", values: data.values || [] }];
    var format = data.format;
    var xKind = data.x_kind || "date";

    var svg = d3
      .select(target)
      .append("svg")
      .attr("width", width)
      .attr("height", height);
    var g = svg
      .append("g")
      .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

    // Parse x values per kind. Date strings (YYYY-MM-DD or full
    // ISO) parse via Date.parse → scaleTime; numeric x values use
    // scaleLinear (e.g. day-offsets, sequence indices).
    var xParsed;
    var xScale;
    if (xKind === "date") {
      xParsed = xValues.map((v) => new Date(v));
      xScale = d3.scaleTime().domain(d3.extent(xParsed)).range([0, innerW]);
    } else {
      xParsed = xValues.map((v) => Number(v));
      xScale = d3.scaleLinear().domain(d3.extent(xParsed)).range([0, innerW]);
    }

    var allValues = [];
    series.forEach((s) => {
      (s.values || []).forEach((v) => {
        if (typeof v === "number") allValues.push(v);
      });
    });
    var maxVal = allValues.length > 0 ? d3.max(allValues) : 0;
    var minVal = allValues.length > 0 ? d3.min(allValues) : 0;
    var yScale = d3
      .scaleLinear()
      .domain([Math.min(0, minVal), maxVal || 1])
      .nice()
      .range([innerH, 0]);

    var xAxis = d3.axisBottom(xScale).ticks(6);
    var yAxis = d3
      .axisLeft(yScale)
      .ticks(5)
      .tickFormat((v) => formatKPIValue(v, format));
    g.append("g")
      .attr("class", "linechart-x-axis")
      .attr("transform", "translate(0," + innerH + ")")
      .call(xAxis)
      .selectAll("text")
      .attr("class", "text-xs fill-primary-fg");
    g.append("g")
      .attr("class", "linechart-y-axis")
      .call(yAxis)
      .selectAll("text")
      .attr("class", "text-xs fill-primary-fg");

    if (data.x_label) {
      svg
        .append("text")
        .attr("class", "linechart-x-label text-xs fill-secondary-fg")
        .attr("text-anchor", "middle")
        .attr("x", margin.left + innerW / 2)
        .attr("y", height - 8)
        .text(data.x_label);
    }
    if (data.y_label) {
      svg
        .append("text")
        .attr("class", "linechart-y-label text-xs fill-secondary-fg")
        .attr("text-anchor", "middle")
        .attr(
          "transform",
          "translate(16," + (margin.top + innerH / 2) + ") rotate(-90)",
        )
        .text(data.y_label);
    }

    // Default colour palette mirrors Tailwind's blue/emerald/amber/
    // rose 500s — high contrast against the slate background.
    var defaultPalette = [
      "#3b82f6", // blue-500
      "#10b981", // emerald-500
      "#f59e0b", // amber-500
      "#f43f5e", // rose-500
      "#8b5cf6", // violet-500
      "#06b6d4", // cyan-500
    ];

    var line = d3
      .line()
      .defined((d) => d.y != null && !Number.isNaN(d.y))
      .x((d) => xScale(d.x))
      .y((d) => yScale(d.y));

    series.forEach((s, si) => {
      var colour = s.color || defaultPalette[si % defaultPalette.length];
      var points = (s.values || []).map((y, i) => ({
        x: xParsed[i],
        y: typeof y === "number" ? y : null,
      }));
      g.append("path")
        .datum(points)
        .attr("class", "linechart-line")
        .attr("data-series-name", s.name || String(si))
        .attr("fill", "none")
        .attr("stroke", colour)
        .attr("stroke-width", 2)
        .attr("d", line);
    });

    // Legend — only when 2+ series (single-series chart's legend
    // is just visual noise; the title carries the meaning).
    var legend;
    var entries;
    if (series.length > 1) {
      legend = svg
        .append("g")
        .attr("class", "linechart-legend")
        .attr(
          "transform",
          "translate(" + (margin.left + 8) + "," + (margin.top + 4) + ")",
        );
      entries = legend
        .selectAll("g.linechart-legend-entry")
        .data(series)
        .enter()
        .append("g")
        .attr("class", "linechart-legend-entry")
        .attr("transform", (_d, i) => "translate(0," + i * 16 + ")");
      entries
        .append("rect")
        .attr("width", 10)
        .attr("height", 10)
        .attr(
          "fill",
          (s, i) => s.color || defaultPalette[i % defaultPalette.length],
        );
      entries
        .append("text")
        .attr("x", 14)
        .attr("y", 9)
        .attr("class", "text-xs fill-primary-fg")
        .text((s, i) => s.name || "Series " + (i + 1));
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
      return "px-2 py-1 rounded text-secondary-fg cursor-not-allowed";
    }
    return "px-2 py-1 rounded text-accent hover:bg-link-tint cursor-pointer";
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
        "fill-accent hover:opacity-80 cursor-pointer transition-colors",
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
      .attr("class", "stroke-secondary-fg")
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
      .attr("class", "fill-primary-fg text-xs font-sans pointer-events-none");
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
      .attr("class", "stroke-secondary-fg")
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
        "fill-accent hover:opacity-80 cursor-pointer transition-colors",
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
      .attr("class", "fill-primary-fg text-xs font-sans pointer-events-none");

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
      renderBarChart: renderBarChart,
      renderLineChart: renderLineChart,
      renderSankey: renderSankey,
      renderForceGraph: renderForceGraph,
      formatKPIValue: formatKPIValue,
      buildTableUrl: buildTableUrl,
      nextSortDirection: nextSortDirection,
    };
  }
})();
