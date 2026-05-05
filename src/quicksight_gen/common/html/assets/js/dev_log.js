// Dev-log forwarder — wraps the HTMX event bus + custom 'sankey:click'
// events and POSTs each to /log. Server echoes the events inline with
// uvicorn's log stream so the developer sees what the browser is doing
// in real time. Gated by a meta tag so production deploys (no meta =
// no listeners attached, zero overhead).
//
// Anti-loop note: the /log POST goes via plain fetch, NOT htmx, so
// logging events don't generate more events. Errors swallowed so a
// down /log endpoint can't break the page.

(() => {
  if (!document.querySelector('meta[name="dev-log"]')) return;

  function send(eventName, payload) {
    fetch("/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ event: eventName }, payload || {})),
      keepalive: true,
    }).catch(() => {});
  }

  function describe(el) {
    if (!el || !el.tagName) return null;
    var s = el.tagName.toLowerCase();
    if (el.id) s += "#" + el.id;
    if (el.getAttribute && el.getAttribute("data-visual-id")) {
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

  send("dev-log:ready", { ua: navigator.userAgent });
})();
