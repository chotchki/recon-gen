// Dev-log forwarder — wraps the HTMX event bus + custom 'sankey:click'
// events + browser-side console errors / uncaught exceptions / unhandled
// promise rejections, and POSTs each to /log. Server echoes the events
// inline with uvicorn's log stream so the developer sees what the
// browser is doing in real time. Gated by a meta tag so production
// deploys (no meta = no listeners attached, zero overhead).
//
// X.4.b.3: the console / onerror / unhandledrejection hooks added to
// catch wasm-graphviz failures + Studio diagram-page JS errors during
// the spike-arm iteration loop. Same gating + anti-loop pattern.
//
// Anti-loop note: the /log POST goes via plain fetch, NOT htmx, so
// logging events don't generate more events. Errors swallowed so a
// down /log endpoint can't break the page. The console hooks call the
// originals BEFORE forwarding so DevTools still shows everything.

(() => {
  if (!document.querySelector('meta[name="dev-log"]')) return;

  // Sticky guard: if a /log POST itself errors, the catch is silent —
  // but if console.error gets called inside the catch chain (unlikely
  // but possible), we don't want to recurse. The flag is checked on
  // every send.
  var _sending = false;

  function send(eventName, payload) {
    if (_sending) return;
    _sending = true;
    try {
      fetch("/log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          Object.assign({ event: eventName }, payload || {}),
        ),
        keepalive: true,
      }).catch(() => {});
    } finally {
      _sending = false;
    }
  }

  // Tame arbitrary console-args into a JSON-safe array of strings.
  function stringifyArgs(args) {
    var out = [];
    var i;
    var a;
    for (i = 0; i < args.length; i++) {
      a = args[i];
      if (a instanceof Error) {
        out.push(a.name + ": " + a.message + (a.stack ? "\n" + a.stack : ""));
      } else if (typeof a === "string") {
        out.push(a);
      } else {
        try {
          out.push(JSON.stringify(a));
        } catch {
          out.push(String(a));
        }
      }
    }
    return out;
  }

  function describe(el) {
    if (!el?.tagName) return null;
    var s = el.tagName.toLowerCase();
    if (el.id) s += "#" + el.id;
    if (el.getAttribute?.("data-visual-id")) {
      s += "[data-visual-id=" + el.getAttribute("data-visual-id") + "]";
    }
    return s;
  }

  // HTMX event bus — the trigger / request / swap lifecycle.
  var htmxEvents = [
    "htmx:beforeRequest",
    "htmx:afterRequest",
    "htmx:beforeSwap",
    "htmx:afterSwap",
    "htmx:responseError",
    "htmx:sendError",
    "htmx:targetError",
  ];
  htmxEvents.forEach((name) => {
    document.body.addEventListener(name, (evt) => {
      var detail = evt.detail || {};
      var rc = detail.requestConfig || {};
      var xhr = detail.xhr || {};
      send(name, {
        target: describe(evt.target),
        verb: rc.verb || null,
        path: rc.path || null,
        status: xhr.status || null,
      });
    });
  });

  // Custom event the bootstrap fires on d3 node clicks — see
  // fireAnchorRequest in the main bootstrap. Captures the
  // user-intent moment BEFORE htmx.ajax fires so the log shows
  // "click → request → swap" in order.
  document.body.addEventListener("sankey:click", (evt) => {
    var detail = evt.detail || {};
    send("sankey:click", {
      visualId: detail.visualId || null,
      anchor: detail.anchor || null,
    });
  });

  // X.4.b.3 — console.error / .warn pass through to original first
  // (so DevTools still shows everything), then forward to /log.
  ["error", "warn"].forEach((level) => {
    var orig = console[level];
    console[level] = (...args) => {
      try {
        orig.apply(console, args);
      } catch {}
      send("console:" + level, { args: stringifyArgs(args) });
    };
  });

  // window.onerror — uncaught synchronous exceptions.
  window.addEventListener("error", (evt) => {
    send("window:error", {
      message: evt.message || null,
      filename: evt.filename || null,
      line: evt.lineno || null,
      col: evt.colno || null,
      stack: (evt.error && evt.error.stack) || null,
    });
  });

  // Unhandled promise rejection — wasm-graphviz returns promises;
  // a layout failure that nobody .catch()es lands here.
  window.addEventListener("unhandledrejection", (evt) => {
    var reason = evt.reason;
    var msg, stack;
    if (reason instanceof Error) {
      msg = reason.name + ": " + reason.message;
      stack = reason.stack || null;
    } else {
      try {
        msg = JSON.stringify(reason);
      } catch {
        msg = String(reason);
      }
      stack = null;
    }
    send("window:unhandledrejection", { reason: msg, stack: stack });
  });

  send("dev-log:ready", { ua: navigator.userAgent });
})();
