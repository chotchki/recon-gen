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
    // WHY: copy data-bound-params from the (about-to-be-wiped) script
    // tag onto the persistent section element so failure-capture's
    // dom.html snapshot reveals what params each visual was queried
    // with. The server-rendered attr lives on the script which we
    // clear below.
    var boundParams = dataScript.getAttribute("data-bound-params");
    if (boundParams !== null) {
      section.setAttribute("data-bound-params", boundParams);
    }
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
        wireRowDrills(section, target, data);
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

  // u.4.e.3 — row-level drills on Table visuals. The server stamps a
  // `data-row-drills` JSON attribute on the visual's outer section
  // element (see render.py::_serialize_table_row_drills); after
  // `renderTable` paints the rows we decorate each table row:
  //   - left-click navigates via the *primary* drill (a DATA_POINT_CLICK
  //     drill if there is one, else the first drill) — the "click a row
  //     to go where it points" gesture, mirroring QS's data-point click;
  //   - if any drill is DATA_POINT_MENU, a trailing "⋯" `<button>` per
  //     row opens a `ctxmenu` popover listing every drill's label, and
  //     the same menu binds on the row's `contextmenu` (right-click —
  //     QS-gesture parity). Picking an item navigates to its URL.
  // The URL is `target_path` + `?param_<name>=<row cell value>` for each
  // declared param; params whose source column isn't in the rendered
  // table are skipped (so a drill with only static-date / sentinel
  // writes — App2 has no equivalent — just navigates to the sheet).
  function rowDrillUrl(drill, row, colIndex) {
    var qs = [];
    var params = drill.params || [];
    params.forEach((p) => {
      var i = colIndex[String(p.column).toLowerCase()];
      var v;
      if (i === undefined || i === null) return;
      v = row[i];
      if (v === undefined || v === null) v = "";
      qs.push(
        "param_" +
          encodeURIComponent(p.name) +
          "=" +
          encodeURIComponent(String(v)),
      );
    });
    return drill.target_path + (qs.length ? "?" + qs.join("&") : "");
  }

  function openRowMenu(eventOrEl, drills, row, colIndex) {
    if (typeof ctxmenu === "undefined" || !ctxmenu || !ctxmenu.show) return;
    var items = drills.map((d) => {
      var url = rowDrillUrl(d, row, colIndex);
      return {
        text: d.label,
        action: () => {
          window.location.href = url;
        },
      };
    });
    // ctxmenu.show stops propagation / prevents the browser menu itself;
    // passing the originating event anchors the popover at the cursor,
    // passing the button element anchors it to the button.
    ctxmenu.show(items, eventOrEl);
  }

  function wireRowDrills(section, target, data) {
    var raw = section && section.getAttribute("data-row-drills");
    if (!raw) return;
    var drills;
    var columns;
    var rows;
    var tbody;
    var colIndex;
    var hasMenu;
    var clickDrill;
    var headTr;
    var th;
    try {
      drills = JSON.parse(raw);
    } catch (e) {
      console.error("bad data-row-drills", e);
      return;
    }
    if (!Array.isArray(drills) || drills.length === 0) return;
    columns = (data && data.columns) || [];
    rows = (data && data.rows) || [];
    tbody = target.querySelector("tbody");
    if (!tbody) return;
    colIndex = {};
    columns.forEach((c, i) => {
      if (c && c.name != null) colIndex[String(c.name).toLowerCase()] = i;
    });
    hasMenu = false;
    clickDrill = null;
    drills.forEach((d) => {
      if (d.trigger === "DATA_POINT_MENU") hasMenu = true;
      if (!clickDrill && d.trigger === "DATA_POINT_CLICK") clickDrill = d;
    });
    if (!clickDrill) clickDrill = drills[0];
    if (hasMenu) {
      headTr = target.querySelector("thead tr");
      if (headTr && !headTr.querySelector("th.row-drill-col")) {
        th = document.createElement("th");
        th.className =
          "row-drill-col px-3 py-2 text-left border-b border-surface-border";
        th.setAttribute("aria-label", "Row actions");
        headTr.appendChild(th);
      }
    }
    tbody.querySelectorAll("tr").forEach((tr, ri) => {
      var row = rows[ri];
      var url;
      var td;
      var btn;
      if (!row) return;
      url = rowDrillUrl(clickDrill, row, colIndex);
      tr.classList.add("row-drillable");
      tr.style.cursor = "pointer";
      tr.setAttribute("data-row-drill", "1");
      tr.setAttribute("tabindex", "0");
      tr.addEventListener("click", () => {
        window.location.href = url;
      });
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          window.location.href = url;
        }
      });
      if (hasMenu) {
        tr.addEventListener("contextmenu", (e) => {
          openRowMenu(e, drills, row, colIndex);
        });
        td = document.createElement("td");
        td.className =
          "row-drill-col px-3 py-2 border-b border-surface-border text-right";
        btn = document.createElement("button");
        btn.type = "button";
        btn.className =
          "row-drill-menu-btn text-secondary-fg hover:text-primary-fg px-1 leading-none";
        btn.textContent = "⋯";
        btn.setAttribute("aria-label", "Row actions");
        btn.setAttribute("aria-haspopup", "menu");
        btn.addEventListener("click", (e) => {
          openRowMenu(e, drills, row, colIndex);
        });
        td.appendChild(btn);
        tr.appendChild(td);
      }
    });
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

  // X.2.m — error toaster. HTMX swap targets that hit a 4xx / 5xx
  // would otherwise leave the user staring at a blank panel (the
  // server returned an error body, hx-target="#visual-data-X" wants
  // to swap that body in, but our error page is a full HTML doc the
  // user can't see in the panel). Catch htmx:responseError, surface
  // a transient toast at top-right with the status + a generic
  // message, and let the original target keep whatever it already
  // had so the user sees context.
  //
  // Stacking: each toast appends to a single fixed container at
  // top-right; multiple errors firing in quick succession stack
  // vertically. Each toast auto-dismisses after ~5s via setTimeout
  // (no CSS animation; setTimeout + remove() is the simpler floor).
  function ensureToastContainer() {
    var container = document.getElementById("htmx-error-toaster");
    if (container) return container;
    container = document.createElement("div");
    container.id = "htmx-error-toaster";
    // Tailwind classes resolve via the same theme tokens as the
    // rest of the app — bg-danger / text-accent-fg pick up the
    // per-instance --color-* values injected at the top of the
    // page shell.
    container.className =
      "fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none";
    document.body.appendChild(container);
    return container;
  }

  function showErrorToast(message, status) {
    var container = ensureToastContainer();
    var toast = document.createElement("div");
    toast.className =
      "toast bg-danger text-accent-fg px-4 py-3 rounded-lg shadow-lg " +
      "text-sm pointer-events-auto max-w-sm";
    toast.setAttribute("role", "status");
    var statusLabel = "";
    if (status) statusLabel = " (HTTP " + status + ")";
    toast.textContent = message + statusLabel;
    container.appendChild(toast);
    // Auto-dismiss after 5s. Keep the timeout reference local so a
    // future "stack overflow" guard could clear them; today we let
    // each toast manage its own lifetime.
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 5000);
    return toast;
  }

  // HTMX dispatches its events on the triggering element + they
  // bubble up to document.body — listening on body matches the
  // dev_log forwarder pattern (dev_log.js uses
  // document.body.addEventListener for the same lifecycle events).
  document.body.addEventListener("htmx:responseError", (evt) => {
    var detail = evt.detail || {};
    var xhr = detail.xhr || {};
    showErrorToast(
      "Couldn't load this section. Try again.",
      xhr.status || null,
    );
  });
  // htmx:sendError fires when the request itself fails (network down,
  // CORS, etc.) — same UX answer as a 5xx, slightly different copy
  // since "the server returned X" doesn't apply.
  document.body.addEventListener("htmx:sendError", () => {
    showErrorToast("Network error. Check your connection.", null);
  });

  // AA.B.5.followon.skeleton — re-inject the loading skeleton into
  // every visual-data div whose request is about to fire. The
  // render.py initial markup includes a skeleton inside ``.visual-data``,
  // but the first htmx:beforeSwap wipes innerHTML before the response
  // paints; refresh requests on already-loaded visuals have stale
  // content in the swap target instead of a skeleton. This hook puts
  // the skeleton back ahead of EVERY request so the loading state is
  // always present + the "is loading?" signal (presence of
  // ``.visual-loading``) stays accurate across initial + refresh.
  // CSS opacity-with-300ms-delay keeps fast loads from flashing.
  //
  // AA.A.9.race — ALSO stamp ``data-requested-params`` on the visual-data
  // div using the same shape ``render.py::emit_visual_data_fragment``
  // serializes ``data-bound-params`` with. The pair is the per-visual
  // freshness oracle: ``afterSwap`` mirrors the response's
  // ``data-bound-params`` onto the div as ``data-rendered-params``;
  // the visual is settled iff ``requested === rendered``. Closes the
  // T2→T4 gap in ``hx-sync="this:queue last"`` chains where a queued
  // wave's content briefly shows old data after the in-flight wave's
  // swap clears the skeleton but before the queued wave's own
  // beforeRequest re-injects it.
  document.addEventListener("htmx:beforeRequest", (evt) => {
    const elt = evt.detail.elt;
    if (elt?.classList?.contains("visual-data")) {
      const params = evt.detail.requestConfig?.parameters || {};
      elt.dataset.requestedParams = _serializeBoundParams(params);
      elt.innerHTML =
        '<div class="visual-loading" aria-hidden="true">' +
        '<div class="skeleton-block"></div>' +
        "</div>";
    }
  });

  document.addEventListener("htmx:afterSwap", (evt) => {
    hydrate(evt.detail.target);
    wireFilterWidgets(evt.detail.target);
    // AA.A.9.race — mirror the response's data-bound-params onto the
    // visual-data div so it sits next to the requested-params snapshot
    // from beforeRequest. ``hydrateSection`` already copies the script
    // tag's data-bound-params onto the enclosing section just before
    // wiping innerHTML (see line ~85), so by the time we run here the
    // section is the freshness source of truth. We just mirror it onto
    // the swap target div so the driver reads both attrs from one node.
    const tgt = evt.detail.target;
    if (tgt?.classList?.contains("visual-data")) {
      const section = tgt.closest("[data-visual-id]");
      const bp = section?.getAttribute("data-bound-params");
      if (bp != null) {
        tgt.dataset.renderedParams = bp;
      }
    }
  });

  // AA.A.9.race — mirror of ``render.py::emit_visual_data_fragment``'s
  // ``relevant`` filter. Same key set (``param_*`` / ``filter_*`` /
  // ``date_from`` / ``date_to``), same value shape (single string or
  // array-of-strings on multi-valued), same byte-shape JSON
  // (sort_keys + compact). Stays in sync by convention — if the server
  // filter ever changes, this must change too.
  function _serializeBoundParams(params) {
    const rel = {};
    Object.keys(params)
      .sort()
      .forEach((k) => {
        if (
          k.indexOf("param_") === 0 ||
          k.indexOf("filter_") === 0 ||
          k === "date_from" ||
          k === "date_to"
        ) {
          const v = params[k];
          if (Array.isArray(v)) {
            rel[k] = v.length > 1 ? v : v.length === 1 ? v[0] : "";
          } else {
            rel[k] = v == null ? "" : v;
          }
        }
      });
    return JSON.stringify(rel);
  }
  document.addEventListener("DOMContentLoaded", () => {
    hydrate(document.body);
    wireCategoryFilters(document);
    wireFilterWidgets(document);
    wireFilterAutoRefresh();
    wireDataGenerationPoller();
  });

  // X.4.g.12.b — poll /data_generation_id; reload when the server-
  // reported value differs from what this page captured at first load.
  //
  // Captured baseline = meta[name="data-generation-id"] content (the
  // server's value at the moment this page rendered). Polling cadence
  // 3s is a UX/CPU sweet spot — short enough that "I clicked Deploy"
  // feels live (≤ 3s perceived staleness), long enough that a tab
  // open all day doesn't burn meaningful battery.
  //
  // Visible-tab-only via the Page Visibility API: a backgrounded tab
  // skips polls (saves battery + bandwidth) and immediately checks on
  // visibilitychange → "visible" so re-foregrounding catches up
  // without waiting for the next tick.
  //
  // No baseline meta = no-op. Lets dashboards pages opt in via the
  // server emitting the meta; other surfaces (the dashboards listing
  // page, error pages) stay quiet.
  function wireDataGenerationPoller() {
    var meta = document.querySelector('meta[name="data-generation-id"]');
    if (!meta) return;
    var baselineRaw = meta.getAttribute("content");
    var baseline = parseInt(baselineRaw, 10);
    if (Number.isNaN(baseline)) return;

    function pollOnce() {
      if (document.visibilityState !== "visible") return;
      fetch("/data_generation_id", { cache: "no-store" })
        .then((resp) => (resp.ok ? resp.json() : null))
        .then((body) => {
          if (!body) return;
          var current = parseInt(body.data_generation_id, 10);
          if (Number.isNaN(current)) return;
          if (current !== baseline) {
            // Update baseline before reloading so a slow reload + bumped
            // counter mid-flight doesn't double-fire. The reload will
            // re-read the meta tag from the freshly-rendered page.
            baseline = current;
            window.location.reload();
          }
        })
        .catch(() => {
          /* swallow — next tick retries */
        });
    }
    // Fire once immediately on load (catches "deploy happened in the
    // 200ms between page render + DOMContentLoaded"), then poll on a
    // 3s interval, AND re-poll when the tab regains focus.
    pollOnce();
    setInterval(pollOnce, 3000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") pollOnce();
    });
  }

  // X.2.g.1.e — auto-refresh visuals on filter change. Listens for
  // ``change`` events on the #filter-form and broadcasts a
  // ``refresh`` custom event after a 300ms debounce. Each visual
  // section's ``hx-trigger="load, refresh from:body"`` re-fires
  // its hx-get with the new form state. Drops the need for a
  // user-clicked Refresh button — filter changes are intent enough.
  // Debounce avoids re-firing on every keystroke in date inputs;
  // the standard ``change`` event already fires on blur for text
  // inputs, but on every keystroke / pick for date pickers in some
  // browsers.
  function wireFilterAutoRefresh() {
    var form = document.getElementById("filter-form");
    if (!form) return;
    var timer = null;
    form.addEventListener("change", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (typeof htmx === "undefined") return;
        // AA.B.5.followon — iterate visuals explicitly instead of
        // broadcasting a body-level ``refresh`` event. The broadcast
        // pattern was unreliable: under parallel-initial-load + mid-load
        // filter pick, the bottom 2-3 visuals in DOM order silently
        // dropped the trigger (proved by data-bound-params + per-visual
        // network capture — chain bqaak83tb / chain 11c02b0). Why an
        // explicit loop fixes it: HTMX's body-level event dispatch
        // appears to short-circuit on some condition involving the
        // in-flight requests of earlier visuals. Triggering each visual
        // directly bypasses the cross-element ordering entirely; each
        // visual independently consults its own ``hx-sync`` policy
        // (``this:queue last``) and either fires immediately or queues
        // for after its current request lands.
        var visuals = document.querySelectorAll(".visual-data[hx-get]");
        visuals.forEach((div) => {
          htmx.trigger(div, "refresh");
        });
      }, 300);
    });
  }

  // X.2.d / X.2.l.4 — CategoryFilter sync. Each .category-filter wrapper
  // holds a hidden ``<input name="filter_<col>">`` (the wire element —
  // HTMX serializes named inputs only) plus an un-named
  // ``<select multiple data-category-select>`` that Tom Select enhances.
  // This listener keeps the hidden input's value = the select's selected
  // option values joined by comma (the ``?filter_<col>=v1,v2,v3`` shape
  // the data fetcher consumes). The ``<select>``'s ``change`` event —
  // re-fired by Tom Select's onChange in wireTomSelect — runs ``update``
  // first (target phase) then bubbles to #filter-form (bubble phase),
  // where wireFilterAutoRefresh sees the now-updated hidden input. So no
  // extra dispatch is needed. ``data-wired`` makes this idempotent.
  function wireCategoryFilters(root) {
    var scope = root || document;
    var wrappers = scope.querySelectorAll(".category-filter");
    wrappers.forEach((div) => {
      if (div.dataset.wired === "1") return;
      div.dataset.wired = "1";
      var hidden = div.querySelector('input[type="hidden"]');
      var select = div.querySelector("select[data-category-select]");
      if (!hidden || !select) return;
      select.addEventListener("change", () => {
        var vals = Array.prototype.map.call(
          select.selectedOptions,
          (o) => o.value,
        );
        hidden.value = vals.join(",");
      });
    });
  }

  // X.2.l.4 — fancy filter widgets. The renderer (render.py) emits a
  // plain <select>/<input> carrying a data-widget="<kind>" attribute;
  // this enhances each one with Tom Select / Flatpickr / noUiSlider
  // (CDN-loaded in the page shell). The enhanced widget writes back
  // into the underlying element's .value and dispatches a bubbling
  // `change` so wireFilterAutoRefresh's #filter-form listener picks it
  // up — i.e. the HTMX wire shape (URL keys, form serialization) is
  // unchanged; only the chrome differs. data-widget-wired makes the
  // call idempotent, so it's safe to re-run after htmx:afterSwap.
  //
  // If a lib failed to load (offline, CDN blip), the typeof guard
  // leaves the plain <select>/<input> in place — degraded chrome, but
  // the filter still works. (X.2.p will bundle the libs locally so
  // there's no CDN to miss.)
  //
  // Markup contract (see render.py, X.2.l.4.b):
  //   <select  data-widget="tomselect" [multiple] ...>
  //   <input   data-widget="flatpickr-range" ...>  — siblings
  //       <input name="date_from"> / <input name="date_to"> get synced
  //   <div     data-widget="nouislider"
  //            data-min/data-max/[data-start-min]/[data-start-max]/[data-step]
  //            data-min-input="min_<col>" data-max-input="max_<col>">
  //       — two-handle range; those two number inputs get synced
  //   <div     data-widget="nouislider"
  //            data-min/data-max/[data-start-min]/[data-step]
  //            data-value-input="param_<name>">
  //       — single-handle parameter slider (X.2.u.4.e); that one
  //         <input name="param_<name>"> gets synced
  function wireFilterWidgets(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-widget]").forEach((el) => {
      if (el.dataset.widgetWired === "1") return;
      var kind = el.dataset.widget;
      if (kind === "tomselect") {
        wireTomSelect(el);
      } else if (kind === "flatpickr-range") {
        wireFlatpickrRange(el, scope);
      } else if (kind === "nouislider") {
        wireNoUiSlider(el, scope);
      }
    });
  }

  function wireTomSelect(el) {
    if (typeof TomSelect === "undefined") return;
    el.dataset.widgetWired = "1";
    new TomSelect(el, {
      plugins: el.multiple ? ["remove_button"] : [],
      // Tom Select syncs the underlying <select>'s selected options but
      // doesn't always re-fire a native `change` on it — do it
      // explicitly so wireFilterAutoRefresh sees the new value.
      onChange: () => {
        el.dispatchEvent(new Event("change", { bubbles: true }));
      },
    });
  }

  function wireFlatpickrRange(el, scope) {
    if (typeof flatpickr === "undefined") return;
    el.dataset.widgetWired = "1";
    var fromInput = scope.querySelector('input[name="date_from"]');
    var toInput = scope.querySelector('input[name="date_to"]');
    flatpickr(el, {
      mode: "range",
      dateFormat: "Y-m-d",
      onChange: (selectedDates, _dateStr, instance) => {
        var lo = selectedDates[0]
          ? instance.formatDate(selectedDates[0], "Y-m-d")
          : "";
        var hi = selectedDates[1]
          ? instance.formatDate(selectedDates[1], "Y-m-d")
          : "";
        if (fromInput) fromInput.value = lo;
        if (toInput) toInput.value = hi;
        if (fromInput) {
          fromInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
      },
    });
  }

  function wireNoUiSlider(el, scope) {
    if (typeof noUiSlider === "undefined") return;
    el.dataset.widgetWired = "1";
    var rangeLo = Number(el.dataset.min);
    var rangeHi = Number(el.dataset.max);
    var startMin = el.dataset.startMin ? Number(el.dataset.startMin) : rangeLo;
    // Single-handle mode (X.2.u.4.e — a ParameterSlider-bound named
    // param): one handle writing back into a single
    // <input name="param_X">. Marked by data-value-input. Two-handle
    // mode (a column NumericRangeSpec): min/max handles → the
    // min_<col>/max_<col> number inputs (data-min-input/data-max-input).
    var valueInput = el.dataset.valueInput
      ? scope.querySelector('input[name="' + el.dataset.valueInput + '"]')
      : null;
    var sopts = {
      start: [startMin],
      connect: [true, false],
      range: { min: rangeLo, max: rangeHi },
      tooltips: true,
    };
    if (el.dataset.step) sopts.step = Number(el.dataset.step);
    if (valueInput) {
      noUiSlider.create(el, sopts);
      el.noUiSlider.on("change", (values) => {
        valueInput.value = values[0];
        valueInput.dispatchEvent(new Event("change", { bubbles: true }));
      });
      return;
    }
    var minInput = scope.querySelector(
      'input[name="' + el.dataset.minInput + '"]',
    );
    var maxInput = scope.querySelector(
      'input[name="' + el.dataset.maxInput + '"]',
    );
    var startMax = el.dataset.startMax ? Number(el.dataset.startMax) : rangeHi;
    var opts = {
      start: [startMin, startMax],
      connect: true,
      range: { min: rangeLo, max: rangeHi },
      tooltips: true,
    };
    if (el.dataset.step) opts.step = Number(el.dataset.step);
    noUiSlider.create(el, opts);
    el.noUiSlider.on("change", (values) => {
      if (minInput) minInput.value = values[0];
      if (maxInput) maxInput.value = values[1];
      if (minInput) {
        minInput.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  }

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
      showErrorToast: showErrorToast,
      ensureToastContainer: ensureToastContainer,
      wireCategoryFilters: wireCategoryFilters,
      wireDataGenerationPoller: wireDataGenerationPoller,
      wireFilterAutoRefresh: wireFilterAutoRefresh,
      wireFilterWidgets: wireFilterWidgets,
      wireRowDrills: wireRowDrills,
      rowDrillUrl: rowDrillUrl,
      openRowMenu: openRowMenu,
    };
  }
})();
